using System.IO.Pipes;
using System.Text.Json;
using AllInCad.NativeProtocol;

if (args.Length != 1 || !int.TryParse(args[0], out var processId) || processId <= 0)
{
    Console.Error.WriteLine("Usage: AllInCad.SmokeClient <AutoCAD process id>");
    return 2;
}

var pipeName = string.Concat("all-in-cad-acad-", processId);
var sessionToken = Environment.GetEnvironmentVariable("AIC_AUTOCAD_SESSION_TOKEN");
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
        ?? throw new EndOfStreamException("The AutoCAD pipe closed without a response.");
    var response = JsonSerializer.Deserialize<RpcResponse>(responseBytes)
        ?? throw new InvalidDataException("The AutoCAD pipe returned an empty response.");

    if (response.Protocol != ProtocolConstants.Version || response.RequestId != request.RequestId)
    {
        throw new InvalidDataException("The AutoCAD response protocol or request id did not match.");
    }
    if (method == "plan.execute")
    {
        if (response.Ok || response.Error?.Code != "E_METHOD_DISABLED")
        {
            throw new InvalidDataException("AutoCAD did not reject the write method in read-only bring-up mode.");
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
        throw new InvalidDataException("The AutoCAD response result was not an object.");
    }
    if (method == "system.ping"
        && (!result.Value.TryGetProperty("pong", out var pong) || pong.ValueKind != JsonValueKind.True))
    {
        throw new InvalidDataException("AutoCAD system.ping returned an unexpected result.");
    }
    if (method == "host.context"
        && (!result.Value.TryGetProperty("host", out var host) || host.GetString() != "autocad2027"))
    {
        throw new InvalidDataException("AutoCAD host.context returned an unexpected host.");
    }
    if (method == "host.capabilities")
    {
        if (!result.Value.TryGetProperty("read", out var readMethods)
            || readMethods.ValueKind != JsonValueKind.Array
            || !readMethods.EnumerateArray().Any(value => value.GetString() == "host.context"))
        {
            throw new InvalidDataException("AutoCAD capabilities omitted host.context from read methods.");
        }
        if (!result.Value.TryGetProperty("write", out var writeMethods)
            || writeMethods.ValueKind != JsonValueKind.Array
            || writeMethods.GetArrayLength() != 0)
        {
            throw new InvalidDataException("AutoCAD bring-up must advertise no write methods.");
        }
        if (!result.Value.TryGetProperty("write_enabled", out var writeEnabled)
            || writeEnabled.ValueKind != JsonValueKind.False)
        {
            throw new InvalidDataException("AutoCAD bring-up must report write_enabled=false.");
        }
    }

    Console.WriteLine(JsonSerializer.Serialize(response, new JsonSerializerOptions { WriteIndented = true }));
}

return 0;

