#if AIC_ZWCAD_SDK
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using ZwSoft.ZwCAD.ApplicationServices;
using ZwSoft.ZwCAD.DatabaseServices;
using ZwSoft.ZwCAD.Runtime;
using ZwApplication = ZwSoft.ZwCAD.ApplicationServices.Core.Application;

[assembly: ExtensionApplication(typeof(AllInCad.ZWCAD2026.Plugin))]
[assembly: CommandClass(typeof(AllInCad.ZWCAD2026.Plugin))]

namespace AllInCad.ZWCAD2026
{
    public sealed class Plugin : IExtensionApplication
    {
        private static CurrentUserPipeServer? pipeServer;
        private static ZwCadUiDispatcher? uiDispatcher;
        private static ReadOnlyBackend? backend;

        public void Initialize()
        {
            try
            {
                uiDispatcher = new ZwCadUiDispatcher();
                backend = new ReadOnlyBackend(uiDispatcher);
                var processId = Process.GetCurrentProcess().Id;
                var pipeName = "all-in-cad-zwcad-" + processId;
                pipeServer = new CurrentUserPipeServer(
                    pipeName,
                    backend.Dispatch,
                    Environment.GetEnvironmentVariable("AIC_ZWCAD_SESSION_TOKEN"));
                pipeServer.Start();
                WriteMessage("\nAll-In-Cad ZWCAD 2026 read-only pipe starting: " + pipeName);
            }
            catch (System.Exception error)
            {
                WriteMessage("\nAll-In-Cad ZWCAD 2026 startup failed: " + error.Message);
            }
        }

        public void Terminate()
        {
            var server = pipeServer;
            pipeServer = null;
            if (server != null)
            {
                server.Dispose();
            }

            if (uiDispatcher != null)
            {
                uiDispatcher.Dispose();
                uiDispatcher = null;
            }

            backend = null;
        }

        [CommandMethod("AIC_STATUS")]
        public static void Status()
        {
            var processId = Process.GetCurrentProcess().Id;
            WriteMessage("\nAIC: ZWCAD 2026 read-only bring-up; pipe=all-in-cad-zwcad-" + processId);
            var document = ZwApplication.DocumentManager.MdiActiveDocument;
            WriteMessage(document == null
                ? "\nAIC: no active document."
                : "\nAIC: active document=" + document.Name);
            var server = pipeServer;
            WriteMessage("\nAIC: pipe listener=" + ((server != null && server.IsListening) ? "ready" : "not ready"));
            if (server != null && !string.IsNullOrWhiteSpace(server.LastListenerError))
            {
                WriteMessage("\nAIC: listener error=" + server.LastListenerError);
            }
        }

        private static void WriteMessage(string message)
        {
            var document = ZwApplication.DocumentManager.MdiActiveDocument;
            if (document != null)
            {
                document.Editor.WriteMessage(message);
            }
            else
            {
                System.Diagnostics.Trace.WriteLine(message);
            }
        }
    }

    internal sealed class ReadOnlyBackend
    {
        private readonly ZwCadUiDispatcher dispatcher;

        public ReadOnlyBackend(ZwCadUiDispatcher dispatcher)
        {
            this.dispatcher = dispatcher;
        }

        public Dictionary<string, object?> Dispatch(string method)
        {
            if (string.Equals(method, "system.ping", StringComparison.Ordinal))
            {
                return new Dictionary<string, object?>
                {
                    ["pong"] = true,
                    ["host"] = "zwcad2026",
                    ["read_only"] = true
                };
            }

            if (string.Equals(method, "host.capabilities", StringComparison.Ordinal))
            {
                return new Dictionary<string, object?>
                {
                    ["host"] = "zwcad2026",
                    ["host_version"] = "2026",
                    ["read"] = new[] { "system.ping", "host.context", "host.capabilities" },
                    ["write"] = Array.Empty<string>(),
                    ["revision_tracking"] = false,
                    ["write_enabled"] = false
                };
            }

            if (string.Equals(method, "host.context", StringComparison.Ordinal))
            {
                var context = dispatcher.Invoke(ReadContext, TimeSpan.FromSeconds(15));
                return context;
            }

            throw new NotSupportedException("This ZWCAD bring-up worker exposes only read-only ping, context, and capability discovery.");
        }

        private static Dictionary<string, object?> ReadContext()
        {
            var document = ZwApplication.DocumentManager.MdiActiveDocument;
            string? documentId = null;
            if (document != null)
            {
                var fingerprint = Convert.ToString(document.Database.FingerprintGuid);
                if (Guid.TryParse(fingerprint, out var parsedFingerprint) && parsedFingerprint != Guid.Empty)
                {
                    documentId = parsedFingerprint.ToString("D");
                }
            }

            return new Dictionary<string, object?>
            {
                ["host"] = "zwcad2026",
                ["host_version"] = "2026",
                ["process_id"] = Process.GetCurrentProcess().Id,
                ["document_open"] = document != null,
                ["document_id"] = documentId,
                ["document_name"] = document == null ? null : document.Name,
                ["revision"] = null,
                ["revision_tracking"] = false,
                ["write_enabled"] = false
            };
        }
    }

    internal sealed class ZwCadUiDispatcher : IDisposable
    {
        private readonly int uiThreadId = Thread.CurrentThread.ManagedThreadId;
        private readonly Control control = new Control();
        private volatile bool disposed;

        public ZwCadUiDispatcher()
        {
            _ = control.Handle;
        }

        public T Invoke<T>(Func<T> callback, TimeSpan timeout)
        {
            if (disposed)
            {
                throw new ObjectDisposedException(nameof(ZwCadUiDispatcher));
            }

            if (Thread.CurrentThread.ManagedThreadId == uiThreadId)
            {
                return callback();
            }

            var completion = new TaskCompletionSource<T>(TaskCreationOptions.RunContinuationsAsynchronously);
            try
            {
                control.BeginInvoke((Action)(() =>
                {
                    if (disposed)
                    {
                        completion.TrySetException(new ObjectDisposedException(nameof(ZwCadUiDispatcher)));
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
                completion.TrySetException(error);
            }

            if (!completion.Task.Wait(timeout))
            {
                throw new TimeoutException("ZWCAD UI thread did not service the context request in time.");
            }

            return completion.Task.GetAwaiter().GetResult();
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
}
#else
namespace AllInCad.ZWCAD2026
{
    public static class BootstrapMarker
    {
        public const string TargetHost = "ZWCAD 2026";
        public const string PreferredExecution = "PyRx/ZRX first, ZRX.NET second, LISP fallback last";
    }
}
#endif




