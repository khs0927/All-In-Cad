#if AIC_AUTOCAD_SDK
using AllInCad.NativeProtocol;
using Autodesk.AutoCAD.Runtime;
using System.Windows.Forms;
using AcadApplication = Autodesk.AutoCAD.ApplicationServices.Core.Application;

namespace AllInCad.AutoCAD2027;

public sealed class Plugin : IExtensionApplication
{
    private static CurrentUserPipeServer? pipeServer;
    private static AutoCadUiDispatcher? uiDispatcher;
    private static ReadOnlyBackend? backend;

    public void Initialize()
    {
        try
        {
            uiDispatcher = new AutoCadUiDispatcher();
            backend = new ReadOnlyBackend(uiDispatcher);
            var pipeName = string.Concat("all-in-cad-acad-", Environment.ProcessId);
            var sessionToken = Environment.GetEnvironmentVariable("AIC_AUTOCAD_SESSION_TOKEN");
            pipeServer = new CurrentUserPipeServer(pipeName, backend.DispatchAsync, sessionToken);
            pipeServer.Start();
            WriteMessage("\nAll-In-Cad AutoCAD 2027 read-only pipe ready: " + pipeName);
        }
        catch (System.Exception error)
        {
            WriteMessage("\nAll-In-Cad AutoCAD 2027 startup failed: " + error.Message);
            throw;
        }
    }

    public void Terminate()
    {
        var server = pipeServer;
        pipeServer = null;
        if (server is not null)
        {
            server.DisposeAsync().AsTask().GetAwaiter().GetResult();
        }

        uiDispatcher?.Dispose();
        uiDispatcher = null;
        backend = null;
    }

    [CommandMethod("AIC_STATUS", CommandFlags.Session)]
    public static void Status()
    {
        var pipeName = string.Concat("all-in-cad-acad-", Environment.ProcessId);
        WriteMessage("\nAIC: AutoCAD 2027 read-only bring-up; pipe=" + pipeName);
        var document = AcadApplication.DocumentManager.MdiActiveDocument;
        WriteMessage(document is null
            ? "\nAIC: no active document."
            : "\nAIC: active document=" + document.Name);
        var server = pipeServer;
        WriteMessage("\nAIC: pipe listener=" + (server?.IsListening == true ? "ready" : "not ready"));
        if (!string.IsNullOrWhiteSpace(server?.LastListenerError))
        {
            WriteMessage("\nAIC: listener error=" + server.LastListenerError);
        }
    }

    private static void WriteMessage(string message)
    {
        var editor = AcadApplication.DocumentManager.MdiActiveDocument?.Editor;
        if (editor is not null)
        {
            editor.WriteMessage(message);
        }
        else
        {
            System.Diagnostics.Trace.WriteLine(message);
        }
    }
}

internal sealed class ReadOnlyBackend(AutoCadUiDispatcher dispatcher)
{
    public async Task<RpcResponse> DispatchAsync(RpcRequest request, CancellationToken cancellationToken)
    {
        if (string.Equals(request.Method, "system.ping", StringComparison.Ordinal))
        {
            return RpcResponse.Success(request.RequestId, new Dictionary<string, object?>
            {
                ["pong"] = true,
                ["host"] = "autocad2027",
                ["read_only"] = true,
            });
        }

        if (string.Equals(request.Method, "host.context", StringComparison.Ordinal))
        {
            var context = await dispatcher.InvokeAsync(ReadContext, cancellationToken).ConfigureAwait(false);
            return RpcResponse.Success(request.RequestId, context);
        }

        if (string.Equals(request.Method, "host.capabilities", StringComparison.Ordinal))
        {
            return RpcResponse.Success(request.RequestId, new Dictionary<string, object?>
            {
                ["host"] = "autocad2027",
                ["host_version"] = "2027",
                ["read"] = new[] { "system.ping", "host.context", "host.capabilities" },
                ["write"] = Array.Empty<string>(),
                ["revision_tracking"] = false,
                ["write_enabled"] = false,
            });
        }

        return RpcResponse.Failure(
            request.RequestId,
            "E_METHOD_DISABLED",
            "This AutoCAD bring-up worker exposes only read-only ping, context, and capability discovery.");
    }

    private static Dictionary<string, object?> ReadContext()
    {
        var document = AcadApplication.DocumentManager.MdiActiveDocument;
        string? documentId = null;
        if (document is not null)
        {
            var fingerprint = document.Database.FingerprintGuid;
            if (Guid.TryParse(fingerprint, out var parsedFingerprint) && parsedFingerprint != Guid.Empty)
            {
                documentId = parsedFingerprint.ToString("D");
            }
        }

        return new Dictionary<string, object?>
        {
            ["host"] = "autocad2027",
            ["host_version"] = "2027",
            ["process_id"] = Environment.ProcessId,
            ["document_open"] = document is not null,
            ["document_id"] = documentId,
            ["document_name"] = document?.Name,
            ["revision"] = null,
            ["revision_tracking"] = false,
            ["write_enabled"] = false,
        };
    }
}

internal sealed class AutoCadUiDispatcher : IDisposable
{
    private readonly int uiThreadId = Environment.CurrentManagedThreadId;
    private readonly Control control = new();
    private bool disposed;

    public AutoCadUiDispatcher()
    {
        _ = control.Handle;
    }

    public Task<T> InvokeAsync<T>(Func<T> callback, CancellationToken cancellationToken)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        if (cancellationToken.IsCancellationRequested)
        {
            return Task.FromCanceled<T>(cancellationToken);
        }

        if (Environment.CurrentManagedThreadId == uiThreadId)
        {
            return Task.FromResult(callback());
        }

        var completion = new TaskCompletionSource<T>(TaskCreationOptions.RunContinuationsAsynchronously);
        var registration = cancellationToken.Register(
            () => completion.TrySetCanceled(cancellationToken));
        try
        {
            control.BeginInvoke((Action)(() =>
            {
                registration.Dispose();
                if (completion.Task.IsCanceled)
                {
                    return;
                }

                try
                {
                    completion.TrySetResult(callback());
                }
                catch (System.Exception error)
                {
                    completion.TrySetException(error);
                }
            }));
        }
        catch (System.Exception error)
        {
            registration.Dispose();
            completion.TrySetException(error);
        }

        return completion.Task;
    }

    public void Dispose()
    {
        if (disposed)
        {
            return;
        }

        disposed = true;
        control.Dispose();
    }
}
#endif

