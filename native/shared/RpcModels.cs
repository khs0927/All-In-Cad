using System.Text.Json;
using System.Text.Json.Serialization;

namespace AllInCad.NativeProtocol;

public static class ProtocolConstants
{
    public const string Version = "aic.native/1";
}

public sealed record RpcError(
    [property: JsonPropertyName("code")] string Code,
    [property: JsonPropertyName("message")] string Message,
    [property: JsonPropertyName("details")]
    IReadOnlyDictionary<string, object?>? Details = null);

public sealed record RpcRequest(
    [property: JsonPropertyName("protocol")] string Protocol,
    [property: JsonPropertyName("request_id")] Guid RequestId,
    [property: JsonPropertyName("method")] string Method,
    [property: JsonPropertyName("params")] JsonElement Params,
    [property: JsonPropertyName("session_token")] string? SessionToken = null)
{
    public void Validate()
    {
        if (!string.Equals(Protocol, ProtocolConstants.Version, StringComparison.Ordinal))
        {
            throw new InvalidDataException($"Unsupported protocol: {Protocol}");
        }

        if (string.IsNullOrWhiteSpace(Method))
        {
            throw new InvalidDataException("Method is required.");
        }
    }
}

public sealed record RpcResponse(
    [property: JsonPropertyName("protocol")] string Protocol,
    [property: JsonPropertyName("request_id")] Guid RequestId,
    [property: JsonPropertyName("ok")] bool Ok,
    [property: JsonPropertyName("result")] object? Result = null,
    [property: JsonPropertyName("error")] RpcError? Error = null)
{
    public static RpcResponse Success(Guid requestId, object? result = null) =>
        new(ProtocolConstants.Version, requestId, true, result);

    public static RpcResponse Failure(
        Guid requestId,
        string code,
        string message,
        IReadOnlyDictionary<string, object?>? details = null) =>
        new(
            ProtocolConstants.Version,
            requestId,
            false,
            Error: new RpcError(code, message, details));
}
