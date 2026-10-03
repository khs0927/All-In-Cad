using System.IO.Pipes;
using System.Text.Json;
using AllInCad.NativeProtocol;

if (args.Length is < 1 or > 2
    || !int.TryParse(args[0], out var processId)
    || processId <= 0)
{
    Console.Error.WriteLine("Usage: AllInCad.SmokeClient <process id> [autocad|zwcad]");
    return 2;
}

var hostKind = args.Length == 2 ? args[1].ToLowerInvariant() : "autocad";
if (hostKind is not ("autocad" or "zwcad"))
{
    Console.Error.WriteLine("Host must be autocad or zwcad.");
    return 2;
}

var hostId = hostKind == "zwcad" ? "zwcad2026" : "autocad2027";
var pipePrefix = hostKind == "zwcad" ? "all-in-cad-zwcad-" : "all-in-cad-acad-";
var tokenName = hostKind == "zwcad" ? "AIC_ZWCAD_SESSION_TOKEN" : "AIC_AUTOCAD_SESSION_TOKEN";
var sessionToken = Environment.GetEnvironmentVariable(tokenName);
var pipeName = string.Concat(pipePrefix, processId);
using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(20));
await using var pipe = new NamedPipeClientStream(".", pipeName, PipeDirection.InOut, PipeOptions.Asynchronous);
await pipe.ConnectAsync(timeout.Token);

foreach (var method in new[] { "system.ping", "host.context", "host.capabilities", "plan.execute" })
{
    using var emptyParams = JsonDocument.Parse("{}");
    var request = new RpcRequest(
        ProtocolConstants.Version,
        Guid.NewGuid(),
        method,
        emptyParams.RootElement.Clone(),
        sessionToken);
    var requestBytes = JsonSerializer.SerializeToUtf8Bytes(request);
    await FrameCodec.WriteFrameAsync(pipe, requestBytes, timeout.Token);
    var responseBytes = await FrameCodec.ReadFrameAsync(pipe, timeout.Token)
        ?? throw new EndOfStreamException("The native pipe closed without a response.");
    var response = JsonSerializer.Deserialize<RpcResponse>(responseBytes)
        ?? throw new InvalidDataException("The native pipe returned an empty response.");

    if (response.Protocol != ProtocolConstants.Version || response.RequestId != request.RequestId)
    {
        throw new InvalidDataException("The native response protocol or request id did not match.");
    }
    if (method == "plan.execute")
    {
        if (response.Ok || response.Error?.Code != "E_METHOD_DISABLED")
        {
            throw new InvalidDataException(hostId + " did not reject the write method in read-only bring-up mode.");
        }
        Console.WriteLine(JsonSerializer.Serialize(response, new JsonSerializerOptions { WriteIndented = true }));
        continue;
    }
    if (!response.Ok)
    {
        throw new InvalidOperationException(response.Error?.Code + ": " + response.Error?.Message);
    }

    var result = response.Result as JsonElement?;
    if (result is null || result.Value.ValueKind != JsonValueKind.Object)
    {
        throw new InvalidDataException("The native response result was not an object.");
    }
    if (method == "system.ping"
        && (!result.Value.TryGetProperty("pong", out var pong) || pong.ValueKind != JsonValueKind.True))
    {
        throw new InvalidDataException(hostId + " system.ping returned an unexpected result.");
    }
    if (method == "host.context"
        && (!result.Value.TryGetProperty("host", out var host) || host.GetString() != hostId))
    {
        throw new InvalidDataException(hostId + " host.context returned an unexpected host.");
    }
    if (method == "host.capabilities")
    {
        if (!result.Value.TryGetProperty("read", out var readMethods)
            || readMethods.ValueKind != JsonValueKind.Array)
        {
            throw new InvalidDataException(hostId + " capabilities omitted the read-method array.");
        }
        var actualReadMethods = readMethods.EnumerateArray()
            .Select(value => value.GetString() ?? string.Empty)
            .ToHashSet(StringComparer.Ordinal);
        var expectedReadMethods = new[] { "system.ping", "host.context", "host.capabilities" }
            .ToHashSet(StringComparer.Ordinal);
        if (!actualReadMethods.SetEquals(expectedReadMethods))
        {
            throw new InvalidDataException(hostId + " advertised an unexpected read-method set.");
        }
        if (!result.Value.TryGetProperty("write", out var writeMethods)
            || writeMethods.ValueKind != JsonValueKind.Array
            || writeMethods.GetArrayLength() != 0)
        {
            throw new InvalidDataException(hostId + " bring-up must advertise no write methods.");
        }
        if (!result.Value.TryGetProperty("write_enabled", out var writeEnabled)
            || writeEnabled.ValueKind != JsonValueKind.False
            || !result.Value.TryGetProperty("revision_tracking", out var revisionTracking)
            || revisionTracking.ValueKind != JsonValueKind.False)
        {
            throw new InvalidDataException(hostId + " bring-up must disable writes and report revision_tracking=false.");
        }
    }

    Console.WriteLine(JsonSerializer.Serialize(response, new JsonSerializerOptions { WriteIndented = true }));
}

return 0;
