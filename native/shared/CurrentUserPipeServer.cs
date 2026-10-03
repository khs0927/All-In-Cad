using System.IO.Pipes;
using System.Runtime.Versioning;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace AllInCad.NativeProtocol;

[SupportedOSPlatform("windows")]
public sealed class CurrentUserPipeServer : IAsyncDisposable
{
    private readonly string pipeName;
    private readonly Func<RpcRequest, CancellationToken, Task<RpcResponse>> dispatcher;
    private readonly string? requiredSessionToken;
    private readonly CancellationTokenSource cancellation = new();
    private Task? listener;
    private volatile bool listenerReady;
    private volatile string? lastListenerError;

    public bool IsListening => listenerReady;
    public string? LastListenerError => lastListenerError;

    public CurrentUserPipeServer(
        string pipeName,
        Func<RpcRequest, CancellationToken, Task<RpcResponse>> dispatcher,
        string? requiredSessionToken = null)
    {
        if (string.IsNullOrWhiteSpace(pipeName))
        {
            throw new ArgumentException("Pipe name is required.", nameof(pipeName));
        }

        this.pipeName = pipeName;
        this.dispatcher = dispatcher ?? throw new ArgumentNullException(nameof(dispatcher));
        this.requiredSessionToken = requiredSessionToken;
    }

    public void Start()
    {
        if (listener is not null)
        {
            throw new InvalidOperationException("Pipe server is already running.");
        }

        listener = Task.Run(ListenAsync);
    }

    private async Task ListenAsync()
    {
        while (!cancellation.IsCancellationRequested)
        {
            try
            {
                await using var pipe = new NamedPipeServerStream(
                    pipeName,
                    PipeDirection.InOut,
                    1,
                    PipeTransmissionMode.Byte,
                    PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
                listenerReady = true;
                lastListenerError = null;

                await pipe.WaitForConnectionAsync(cancellation.Token).ConfigureAwait(false);
                await ProcessConnectionAsync(pipe, cancellation.Token).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (cancellation.IsCancellationRequested)
            {
                listenerReady = false;
                return;
            }
            catch (Exception error)
            {
                listenerReady = false;
                lastListenerError = string.Concat(error.GetType().Name, ": ", error.Message);
                try
                {
                    await Task.Delay(250, cancellation.Token).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    return;
                }
            }
        }
    }

    private async Task ProcessConnectionAsync(Stream stream, CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            var body = await FrameCodec.ReadFrameAsync(stream, token).ConfigureAwait(false);
            if (body is null)
            {
                return;
            }

            RpcResponse response;
            try
            {
                var request = JsonSerializer.Deserialize<RpcRequest>(body)
                    ?? throw new JsonException("Request body is empty.");
                request.Validate();

                if (!SessionTokenMatches(request.SessionToken))
                {
                    response = RpcResponse.Failure(
                        request.RequestId,
                        "E_SESSION_AUTH",
                        "Invalid or missing session token.");
                }
                else
                {
                    response = await dispatcher(request, token).ConfigureAwait(false);
                }
            }
            catch (Exception error)
            {
                response = RpcResponse.Failure(
                    Guid.Empty,
                    "E_PROTOCOL",
                    error.Message,
                    new Dictionary<string, object?>
                    {
                        ["exception_type"] = error.GetType().FullName,
                    });
            }

            var encoded = JsonSerializer.SerializeToUtf8Bytes(response);
            await FrameCodec.WriteFrameAsync(stream, encoded, token).ConfigureAwait(false);
        }
    }

    private bool SessionTokenMatches(string? supplied)
    {
        if (requiredSessionToken is null)
        {
            return true;
        }

        if (supplied is null)
        {
            return false;
        }

        var expectedBytes = Encoding.UTF8.GetBytes(requiredSessionToken);
        var suppliedBytes = Encoding.UTF8.GetBytes(supplied);
        return expectedBytes.Length == suppliedBytes.Length
            && CryptographicOperations.FixedTimeEquals(expectedBytes, suppliedBytes);
    }

    public async ValueTask DisposeAsync()
    {
        cancellation.Cancel();
        if (listener is not null)
        {
            try
            {
                await listener.ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
            }
        }

        cancellation.Dispose();
    }
}
