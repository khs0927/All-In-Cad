using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using Shared = AllInCad.NativeProtocol;

namespace AllInCad.AutoCAD2027
{
    // Plugin.cs and XicadRunner.cs were ported from a ZWCAD worker whose protocol types were
    // never committed. These host-local types keep that handler code unchanged and put it on
    // the committed shared server, so framing, the current-user ACL and the session-token gate
    // stay in native/shared. Types in this namespace win over the shared ones at name lookup.

    internal sealed class RpcRequest
    {
        public string Protocol { get; set; } = Shared.ProtocolConstants.Version;
        public Guid RequestId { get; set; }
        public string Method { get; set; } = string.Empty;
        public JObject? Params { get; set; }
        public string? SessionToken { get; set; }

        internal static RpcRequest From(Shared.RpcRequest request) =>
            new RpcRequest
            {
                Protocol = request.Protocol,
                RequestId = request.RequestId,
                Method = request.Method,
                Params = request.Params.ValueKind == JsonValueKind.Object
                    ? JObject.Parse(request.Params.GetRawText())
                    : null,
                SessionToken = request.SessionToken,
            };
    }

    internal sealed class RpcResponse
    {
        private RpcResponse(Shared.RpcResponse inner)
        {
            Inner = inner;
        }

        internal Shared.RpcResponse Inner { get; }

        public bool Ok => Inner.Ok;

        public object? Result => Inner.Result;

        public static RpcResponse Success(Guid requestId, object? result = null) =>
            new RpcResponse(Shared.RpcResponse.Success(requestId, result));

        public static RpcResponse Failure(
            Guid requestId,
            string code,
            string message,
            IReadOnlyDictionary<string, object?>? details = null) =>
            new RpcResponse(Shared.RpcResponse.Failure(requestId, code, message, details));
    }

    internal sealed class CurrentUserPipeServer : IDisposable
    {
        private readonly Shared.CurrentUserPipeServer inner;

        public CurrentUserPipeServer(
            string pipeName,
            Func<RpcRequest, CancellationToken, Task<RpcResponse>> dispatcher,
            string? requiredSessionToken = null)
        {
            inner = new Shared.CurrentUserPipeServer(
                pipeName,
                async (request, token) =>
                    (await dispatcher(RpcRequest.From(request), token).ConfigureAwait(false)).Inner,
                requiredSessionToken);
        }

        public void Start() => inner.Start();

        // Off the calling thread: Terminate runs on the AutoCAD application thread.
        public void Dispose() =>
            Task.Run(async () => await inner.DisposeAsync().ConfigureAwait(false)).GetAwaiter().GetResult();
    }

    internal static class PipeNames
    {
        internal const string SessionTokenVariable = "AIC_AUTOCAD_SESSION_TOKEN";

        internal static string ResolveAutoCad2027(int processId) => "all-in-cad-acad-" + processId;
    }

    internal sealed class WorkerDescriptor
    {
        [JsonPropertyName("protocol")]
        public string Protocol { get; set; } = string.Empty;

        [JsonPropertyName("host")]
        public string Host { get; set; } = string.Empty;

        [JsonPropertyName("pipe_name")]
        public string PipeName { get; set; } = string.Empty;

        [JsonPropertyName("process_id")]
        public int ProcessId { get; set; }

        [JsonPropertyName("session_id")]
        public string SessionId { get; set; } = string.Empty;

        [JsonPropertyName("started_at")]
        public string StartedAt { get; set; } = string.Empty;

        [JsonPropertyName("capabilities")]
        public string[] Capabilities { get; set; } = Array.Empty<string>();
    }

    internal static class WorkerDescriptorStore
    {
        internal static string Write(WorkerDescriptor descriptor)
        {
            string dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "All-In-Cad",
                "workers");
            Directory.CreateDirectory(dir);
            string path = Path.Combine(dir, descriptor.Host + "-" + descriptor.ProcessId + ".json");
            File.WriteAllText(path, JsonSerializer.Serialize(descriptor));
            return path;
        }

        internal static void TryDelete(string? path)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return;
            }

            try
            {
                File.Delete(path);
            }
            catch (IOException)
            {
            }
            catch (UnauthorizedAccessException)
            {
            }
        }
    }
}
