using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using AllInCad.NativeProtocol;
using Newtonsoft.Json.Linq;

#if AIC_AUTOCAD_SDK
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;
using ZwApplication = Autodesk.AutoCAD.ApplicationServices.Application;
#else
using AllInCad.AutoCAD2027.StubRuntime;
#endif

[assembly: ExtensionApplication(typeof(AllInCad.AutoCAD2027.Plugin))]
[assembly: CommandClass(typeof(AllInCad.AutoCAD2027.Commands))]

namespace AllInCad.AutoCAD2027
{
    /// <summary>
    /// AutoCAD 2027 .NET host worker. Starts the shared aic.native/1 Named Pipe server on
    /// Initialize and stops it on Terminate. Does not launch AutoCAD or use COM.
    /// Ported from the ZWCAD 2026 ZRX.NET worker by namespace substitution plus the five
    /// documented API adaptations in XicadRunner.cs; the pipe protocol is unchanged.
    /// </summary>
    public sealed partial class Plugin : IExtensionApplication
    {
        internal static CurrentUserPipeServer? Server { get; private set; }
        internal static string? DescriptorPath { get; private set; }
        internal static string SessionId { get; private set; } = Guid.NewGuid().ToString("N");
        internal static string? LastPipeName { get; private set; }

        static Plugin()
        {
            AppDomain.CurrentDomain.AssemblyResolve += ResolvePluginDependency;
            PreloadPluginDependencies();
        }

        private static void PreloadPluginDependencies()
        {
            try
            {
                string? dir = Path.GetDirectoryName(typeof(Plugin).Assembly.Location);
                if (string.IsNullOrWhiteSpace(dir))
                {
                    return;
                }

                string[] names =
                {
                    "Newtonsoft.Json.dll",
                    "System.Threading.Tasks.Extensions.dll",
                    "AllInCad.NativeProtocol.dll",
                };
                foreach (string name in names)
                {
                    string path = Path.Combine(dir, name);
                    if (File.Exists(path))
                    {
                        Assembly.LoadFrom(path);
                    }
                }
            }
            catch
            {
            }
        }

        private static Assembly? ResolvePluginDependency(object? sender, ResolveEventArgs args)
        {
            try
            {
                string name = new AssemblyName(args.Name).Name ?? string.Empty;
                if (string.IsNullOrWhiteSpace(name))
                {
                    return null;
                }

                string? dir = Path.GetDirectoryName(typeof(Plugin).Assembly.Location);
                if (string.IsNullOrWhiteSpace(dir))
                {
                    return null;
                }

                string candidate = Path.Combine(dir, name + ".dll");
                if (File.Exists(candidate))
                {
                    // Satisfy version-specific requests (e.g. Unsafe 4.0.4.1) with the deployed DLL.
                    return Assembly.LoadFrom(candidate);
                }
            }
            catch
            {
            }

            return null;
        }


        public void Initialize()
        {
            WriteBootLog("Initialize begin");
            try
            {
                // If a previous attempt left a dead server reference, clear it first.
                try
                {
                    Server?.Dispose();
                }
                catch
                {
                }

                Server = null;

                SessionId = Guid.NewGuid().ToString("N");
                int pid = Process.GetCurrentProcess().Id;
                string pipeName = PipeNames.ResolveZwcad2026(pid);
                LastPipeName = pipeName;
                WriteBootLog("pipeName=" + pipeName + " pid=" + pid);

                Server = new CurrentUserPipeServer(pipeName, DispatchAsync);
                Server.Start();
                EnsureIdleHook();
                WriteBootLog("pipe server started sdk=" + HostCapabilities.SdkBound);

                try
                {
                    DescriptorPath = WorkerDescriptorStore.Write(
                        new WorkerDescriptor
                        {
                            Protocol = ProtocolConstants.Version,
                            Host = "zwcad2026",
                            PipeName = pipeName,
                            ProcessId = pid,
                            SessionId = SessionId,
                            StartedAt = DateTimeOffset.Now.ToString("o"),
                            Capabilities = HostCapabilities.All,
                        });
                    WriteBootLog("descriptor=" + DescriptorPath);
                }
                catch (System.Exception dex)
                {
                    // Descriptor is discovery-only; keep the pipe alive.
                    WriteBootLog("descriptor write FAILED (pipe kept): " + dex);
                    DescriptorPath = null;
                }

                try
                {
                    ZwApplication.DocumentManager.MdiActiveDocument?.Editor.WriteMessage(
                        "\n[All-In-Cad] Named Pipe worker listening on " + @"\\.\pipe\" + pipeName);
                }
                catch
                {
                    // Message is best-effort; pipe worker must stay up even if editor is unavailable.
                }

                WriteBootLog("Initialize ok server=" + (Server is not null));
            }
            catch (System.Exception ex)
            {
                WriteBootLog("Initialize FAILED: " + ex);
                System.Diagnostics.Trace.WriteLine("[All-In-Cad] AutoCAD 2027 plugin Initialize failed: " + ex);
                try
                {
                    Server?.Dispose();
                }
                catch
                {
                }

                Server = null;
                try
                {
                    WorkerDescriptorStore.TryDelete(DescriptorPath);
                }
                catch
                {
                }

                DescriptorPath = null;
            }
        }

        public void Terminate()
        {
            WriteBootLog("Terminate begin");
            try
            {
                Server?.Dispose();
            }
            catch (System.Exception ex)
            {
                WriteBootLog("Terminate FAILED: " + ex);
                System.Diagnostics.Trace.WriteLine("[All-In-Cad] AutoCAD 2027 plugin Terminate failed: " + ex);
            }
            finally
            {
                Server = null;
                WorkerDescriptorStore.TryDelete(DescriptorPath);
                DescriptorPath = null;
                WriteBootLog("Terminate done");
            }
        }

        private static void WriteBootLog(string message)
        {
            string line = DateTimeOffset.Now.ToString("o")
                + " [pid=" + Process.GetCurrentProcess().Id + "] "
                + message
                + Environment.NewLine;
            foreach (string dir in GetBootLogDirs())
            {
                try
                {
                    Directory.CreateDirectory(dir);
                    File.AppendAllText(Path.Combine(dir, "autocad2027.log"), line);
                }
                catch
                {
                }
            }
        }

        private static IEnumerable<string> GetBootLogDirs()
        {
            yield return Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "All-In-Cad",
                "logs");
            yield return Path.Combine(Path.GetTempPath(), "All-In-Cad", "logs");
            string? asm = null;
            try
            {
                asm = Path.GetDirectoryName(typeof(Plugin).Assembly.Location);
            }
            catch
            {
            }

            if (!string.IsNullOrWhiteSpace(asm))
            {
                yield return Path.Combine(asm, "logs");
            }
        }

        private static Task<RpcResponse> DispatchAsync(RpcRequest request, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            switch (request.Method)
            {
                case "system.ping":
                    return Task.FromResult(HandlePing(request));
                case "host.context":
                    return Task.FromResult(HandleHostContext(request));
                case "host.capabilities":
                    return Task.FromResult(HandleCapabilities(request));
                case "host.send_command":
                    return Task.FromResult(HandleSendCommand(request));
                case "host.draw_line":
                    return Task.FromResult(HandleDrawLine(request));
                case "xicad.probe":
                    return Task.FromResult(HandleXicadProbe(request));
                case "xicad.ensure_loaded":
                    return Task.FromResult(HandleXicadEnsureLoaded(request));
                case "xicad.draw_wall":
                    return Task.FromResult(HandleXicadDrawWall(request));
                case "xicad.draw_door":
                    return Task.FromResult(HandleXicadDrawDoor(request));
                case "xicad.draw_window":
                    return Task.FromResult(HandleXicadDrawWindow(request));
                case "xicad.smoke_wall_door_window":
                    return Task.FromResult(HandleXicadSmoke(request));
                case "xicad.state":
                    return Task.FromResult(HandleXicadState(request));
                case "xicad.run":
                    return Task.FromResult(HandleXicadRun(request));
                case "xicad.run_status":
                    return Task.FromResult(HandleXicadRunStatus(request));
                case "xicad.rollback":
                    return Task.FromResult(HandleXicadRollback(request));
                default:
                    return Task.FromResult(
                        RpcResponse.Failure(
                            request.RequestId,
                            "E_METHOD_NOT_FOUND",
                            "Unsupported method: " + request.Method));
            }
        }

        private static RpcResponse HandlePing(RpcRequest request)
        {
            return RpcResponse.Success(
                request.RequestId,
                new
                {
                    pong = true,
                    protocol = ProtocolConstants.Version,
                    host = "zwcad2026",
                    pipe_name = LastPipeName,
                    process_id = Process.GetCurrentProcess().Id,
                    session_id = SessionId,
                    sdk_bound = HostCapabilities.SdkBound,
                    plugin_version = GetPluginVersion(),
                    listening = Server is not null,
                });
        }

        private static RpcResponse HandleCapabilities(RpcRequest request)
        {
            return RpcResponse.Success(
                request.RequestId,
                new
                {
                    host = "zwcad2026",
                    protocol = ProtocolConstants.Version,
                    sdk_bound = HostCapabilities.SdkBound,
                    capabilities = HostCapabilities.All,
                });
        }

        private static RpcResponse HandleHostContext(RpcRequest request)
        {
#if AIC_AUTOCAD_SDK
            try
            {
                Document? doc = ZwApplication.DocumentManager.MdiActiveDocument;
                string? path = null;
                string documentId = "none";
                long revision = 0;
                string? title = null;

                if (doc is not null)
                {
                    path = string.IsNullOrWhiteSpace(doc.Name) ? null : doc.Name;
                    title = path is null ? null : Path.GetFileName(path);
                    documentId = BuildDocumentId(doc);
                    revision = ReadRevision(doc);
                }

                Version? zwVersion = ZwApplication.Version;
                return RpcResponse.Success(
                    request.RequestId,
                    new
                    {
                        host = "zwcad2026",
                        host_product = "ZWCAD",
                        host_version = zwVersion?.ToString(),
                        protocol = ProtocolConstants.Version,
                        session_id = SessionId,
                        pipe_name = LastPipeName,
                        process_id = Process.GetCurrentProcess().Id,
                        sdk_bound = true,
                        document = new
                        {
                            document_id = documentId,
                            path,
                            title,
                            revision,
                            is_active = doc is not null,
                            entity_count = SnapshotModel().EntityCount,
                        },
                    });
            }
            catch (System.Exception ex)
            {
                return RpcResponse.Failure(
                    request.RequestId,
                    "E_HOST_CONTEXT",
                    ex.Message,
                    new Dictionary<string, object?> { ["exception_type"] = ex.GetType().FullName });
            }
#else
            return RpcResponse.Success(
                request.RequestId,
                new
                {
                    host = "zwcad2026",
                    host_product = "ZWCAD",
                    host_version = (string?)null,
                    protocol = ProtocolConstants.Version,
                    session_id = SessionId,
                    pipe_name = LastPipeName,
                    process_id = Process.GetCurrentProcess().Id,
                    sdk_bound = false,
                    document = new
                    {
                        document_id = "unavailable",
                        path = (string?)null,
                        title = (string?)null,
                        revision = 0L,
                        is_active = false,
                    },
                    warning = "Built without AIC_AUTOCAD_SDK.",
                });
#endif
        }

#if AIC_AUTOCAD_SDK
        private static string BuildDocumentId(Document doc)
        {
            // DOCUMENTED (not fixed): AutoCAD 2027 exposes Database.FingerprintGuid and
            // Database.VersionGuid as *string*, whereas this ZWCAD-era code pattern-matches
            // them as Guid. That compiles and then NEVER matches, so every AutoCAD call falls
            // through to the name-based fallback below and yields a stable-per-name id
            // ("autocad:<docName>") that does not change when the drawing is edited. Kept
            // verbatim on purpose: a correct fix changes document_id semantics on the wire and
            // is the client's call, not a silent port decision.
            try
            {
                Database db = doc.Database;
                object? fingerprint = db.GetType().GetProperty("FingerprintGuid")?.GetValue(db);
                object? version = db.GetType().GetProperty("VersionGuid")?.GetValue(db);
                if (fingerprint is Guid fg && version is Guid vg)
                {
                    return string.Format("zwcad:{0:N}:{1:N}", fg, vg);
                }
            }
            catch
            {
            }

            string name = doc.Name;
            return string.IsNullOrWhiteSpace(name) ? "zwcad:untitled" : "zwcad:" + name;
        }

        private static long ReadRevision(Document doc)
        {
            try
            {
                Database db = doc.Database;
                // "Handseed" is the real AutoCAD spelling; the "HandSeed" fallback below is a
                // dead branch kept verbatim from the ZWCAD source.
                object? handseed = db.GetType().GetProperty("Handseed")?.GetValue(db)
                    ?? db.GetType().GetProperty("HandSeed")?.GetValue(db);
                if (handseed is null)
                {
                    return 0;
                }

                object? value = handseed.GetType().GetProperty("Value")?.GetValue(handseed) ?? handseed;
                if (value is long l) return l;
                if (value is ulong ul) return unchecked((long)ul);
                if (value is int i) return i;
                if (value is uint ui) return ui;
                return Convert.ToInt64(value);
            }
            catch
            {
                return 0;
            }
        }
#endif

#if AIC_AUTOCAD_SDK
        private const string ForceXicadLisp = @"C:/xicad/_nl_automation/runner/force_xicad_on_doc.lsp";

        private static RpcResponse HandleDrawLine(RpcRequest request)
        {
            try
            {
                double x1 = request.Params?["x1"]?.ToObject<double?>() ?? 0;
                double y1 = request.Params?["y1"]?.ToObject<double?>() ?? 0;
                double x2 = request.Params?["x2"]?.ToObject<double?>() ?? 1000;
                double y2 = request.Params?["y2"]?.ToObject<double?>() ?? 0;
                var before = SnapshotModel();
                ObjectId? lineId = null;
                RunOnIdle(() =>
                {
                    Document? doc = ZwApplication.DocumentManager.MdiActiveDocument;
                    if (doc is null)
                    {
                        throw new InvalidOperationException("No active document.");
                    }

                    using (doc.LockDocument())
                    using (Transaction tr = doc.TransactionManager.StartTransaction())
                    {
                        var bt = (BlockTable)tr.GetObject(doc.Database.BlockTableId, OpenMode.ForRead);
                        var btr = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForWrite);
                        var line = new Line(
                            new Point3d(x1, y1, 0),
                            new Point3d(x2, y2, 0));
                        lineId = btr.AppendEntity(line);
                        tr.AddNewlyCreatedDBObject(line, true);
                        tr.Commit();
                    }
                });
                var after = SnapshotModel();
                return RpcResponse.Success(
                    request.RequestId,
                    new
                    {
                        op = "host.draw_line",
                        from = new { x = x1, y = y1 },
                        to = new { x = x2, y = y2 },
                        line_id = lineId?.Handle.ToString(),
                        before,
                        after,
                        delta_entities = after.EntityCount - before.EntityCount,
                    });
            }
            catch (System.Exception ex)
            {
                return RpcResponse.Failure(request.RequestId, "E_DRAW_LINE", ex.Message);
            }
        }

        private static RpcResponse HandleSendCommand(RpcRequest request)
        {
            try
            {
                string command = request.Params?["command"]?.ToString()
                    ?? request.Params?["cmd"]?.ToString()
                    ?? string.Empty;
                if (string.IsNullOrWhiteSpace(command))
                {
                    return RpcResponse.Failure(request.RequestId, "E_BAD_PARAMS", "params.command is required");
                }

                bool activate = request.Params?["activate"]?.ToObject<bool?>() ?? true;
                var before = SnapshotModel();
                SendToActiveDocument(command, activate);
                System.Threading.Thread.Sleep(request.Params?["wait_ms"]?.ToObject<int?>() ?? 400);
                var after = SnapshotModel();
                return RpcResponse.Success(request.RequestId, new { command, before, after });
            }
            catch (System.Exception ex)
            {
                return RpcResponse.Failure(request.RequestId, "E_SEND_COMMAND", ex.Message);
            }
        }

        private static RpcResponse HandleXicadProbe(RpcRequest request)
        {
            // Probe is intentionally NOT fail-closed on missing XiCAD: a missing plugin is the
            // expected, reportable state. It always answers with observed facts.
            return XicadProbeRequest(request);
        }

        private static RpcResponse HandleXicadEnsureLoaded(RpcRequest request)
        {
            // DOCUMENTED BEHAVIOUR CHANGE (AutoCAD port):
            // ZWCAD had C:/xicad/_nl_automation/runner/force_xicad_on_doc.lsp available, so this
            // handler could queue a load and report success. AutoCAD 2027 ships without XiCAD.
            // We still ATTEMPT the same load, then verify the XiCAD command symbols actually
            // exist. If they do not, we fail with E_XICAD_UNAVAILABLE instead of returning the
            // ZWCAD-shaped "load queued" success, which would be a lie on this host.
            try
            {
                var gate = XicadGate(request, "xicad.ensure_loaded", requiredSymbol: null, forceLoad: true);
                if (gate is not null)
                {
                    return gate;
                }

                var before = SnapshotModel();
                EnsureXicadLoadedQuiet();
                var after = SnapshotModel();
                return RpcResponse.Success(
                    request.RequestId,
                    new
                    {
                        force_lisp = ForceXicadLisp,
                        before,
                        after,
                        note = "force_xicad_on_doc.lsp load queued",
                    });
            }
            catch (System.Exception ex)
            {
                return RpcResponse.Failure(request.RequestId, "E_XICAD_LOAD", ex.Message);
            }
        }

        private static RpcResponse HandleXicadDrawWall(RpcRequest request)
        {
            // Fail closed: without XiCAD symbols there is no (c:xiDrawWall). Sending it anyway
            // yields "Unknown command" and, per the C-2 hazard below, the trailing text can be
            // re-executed later. We never fake a ZWCAD-shaped success.
            var gate = XicadGate(request, "xicad.draw_wall", requiredSymbol: "C:XIDRAWWALL");
            if (gate is not null)
            {
                return gate;
            }

            try
            {
                double x1 = request.Params?["x1"]?.ToObject<double?>() ?? 0;
                double y1 = request.Params?["y1"]?.ToObject<double?>() ?? 0;
                double x2 = request.Params?["x2"]?.ToObject<double?>() ?? 12000;
                double y2 = request.Params?["y2"]?.ToObject<double?>() ?? 0;
                EnsureXicadLoadedQuiet();
                var before = SnapshotModel();
                // Queue on Application.Idle (pipe thread cannot drive the command line).
                // Prefer (c:xiDrawWall); DCL may still appear — FILEDIA/CMDDIA off first.
                SendToActiveDocument("FILEDIA 0 CMDDIA 0 ", activate: true);
                string cmd =
                    "._CANCEL\n(c:xiDrawWall)\n" +
                    FormatPoint(x1, y1) + "\n" +
                    FormatPoint(x2, y2) + "\n\n";
                SendToActiveDocument(cmd, activate: true);
                System.Threading.Thread.Sleep(request.Params?["wait_ms"]?.ToObject<int?>() ?? 1500);
                var after = SnapshotModel();
                return RpcResponse.Success(
                    request.RequestId,
                    new
                    {
                        op = "xicad.draw_wall",
                        from = new { x = x1, y = y1 },
                        to = new { x = x2, y = y2 },
                        before,
                        after,
                        delta_entities = after.EntityCount - before.EntityCount,
                    });
            }
            catch (System.Exception ex)
            {
                return RpcResponse.Failure(request.RequestId, "E_XICAD_WALL", ex.Message);
            }
        }

        private static RpcResponse HandleXicadDrawDoor(RpcRequest request)
        {
            var gate = XicadGate(request, "xicad.draw_door", requiredSymbol: "C:XIDOOR2");
            if (gate is not null)
            {
                return gate;
            }

            try
            {
                double x = request.Params?["x"]?.ToObject<double?>() ?? 6000;
                double y = request.Params?["y"]?.ToObject<double?>() ?? 0;
                double? width = request.Params?["width"]?.ToObject<double?>();
                EnsureXicadLoadedQuiet();
                var before = SnapshotModel();
                SendToActiveDocument("FILEDIA 0 CMDDIA 0 ", activate: true);
                string cmd = "._CANCEL\n(c:xiDoor2)\n";
                if (width.HasValue)
                {
                    cmd += width.Value.ToString(System.Globalization.CultureInfo.InvariantCulture) + "\n";
                }
                cmd += FormatPoint(x, y) + "\n\n";
                SendToActiveDocument(cmd, activate: true);
                System.Threading.Thread.Sleep(request.Params?["wait_ms"]?.ToObject<int?>() ?? 1500);
                var after = SnapshotModel();
                return RpcResponse.Success(
                    request.RequestId,
                    new
                    {
                        op = "xicad.draw_door",
                        at = new { x, y },
                        width,
                        before,
                        after,
                        delta_entities = after.EntityCount - before.EntityCount,
                    });
            }
            catch (System.Exception ex)
            {
                return RpcResponse.Failure(request.RequestId, "E_XICAD_DOOR", ex.Message);
            }
        }

        private static RpcResponse HandleXicadDrawWindow(RpcRequest request)
        {
            var gate = XicadGate(request, "xicad.draw_window", requiredSymbol: "C:XIWIN2");
            if (gate is not null)
            {
                return gate;
            }

            try
            {
                double x = request.Params?["x"]?.ToObject<double?>() ?? 3000;
                double y = request.Params?["y"]?.ToObject<double?>() ?? 0;
                double? width = request.Params?["width"]?.ToObject<double?>();
                EnsureXicadLoadedQuiet();
                var before = SnapshotModel();
                SendToActiveDocument("FILEDIA 0 CMDDIA 0 ", activate: true);
                string cmd = "._CANCEL\n(c:xiWin2)\n";
                if (width.HasValue)
                {
                    cmd += width.Value.ToString(System.Globalization.CultureInfo.InvariantCulture) + "\n";
                }
                cmd += FormatPoint(x, y) + "\n\n";
                SendToActiveDocument(cmd, activate: true);
                System.Threading.Thread.Sleep(request.Params?["wait_ms"]?.ToObject<int?>() ?? 1500);
                var after = SnapshotModel();
                return RpcResponse.Success(
                    request.RequestId,
                    new
                    {
                        op = "xicad.draw_window",
                        at = new { x, y },
                        width,
                        before,
                        after,
                        delta_entities = after.EntityCount - before.EntityCount,
                    });
            }
            catch (System.Exception ex)
            {
                return RpcResponse.Failure(request.RequestId, "E_XICAD_WINDOW", ex.Message);
            }
        }

        private static RpcResponse HandleXicadSmoke(RpcRequest request)
        {
            var gate = XicadGate(request, "xicad.smoke_wall_door_window", requiredSymbol: null, forceLoad: true);
            if (gate is not null)
            {
                return gate;
            }

            try
            {
                EnsureXicadLoadedQuiet();
                System.Threading.Thread.Sleep(500);

                var wall = HandleXicadDrawWall(CloneRequest(request, new Newtonsoft.Json.Linq.JObject
                {
                    ["x1"] = 0, ["y1"] = 0, ["x2"] = 12000, ["y2"] = 0, ["wait_ms"] = 1800,
                }));
                var door = HandleXicadDrawDoor(CloneRequest(request, new Newtonsoft.Json.Linq.JObject
                {
                    ["x"] = 6000, ["y"] = 0, ["wait_ms"] = 1500,
                }));
                var win = HandleXicadDrawWindow(CloneRequest(request, new Newtonsoft.Json.Linq.JObject
                {
                    ["x"] = 3000, ["y"] = 0, ["wait_ms"] = 1500,
                }));
                var after = SnapshotModel();
                return RpcResponse.Success(
                    request.RequestId,
                    new
                    {
                        op = "xicad.smoke_wall_door_window",
                        wall = wall.Result,
                        door = door.Result,
                        window = win.Result,
                        final = after,
                        wall_ok = wall.Ok,
                        door_ok = door.Ok,
                        window_ok = win.Ok,
                    });
            }
            catch (System.Exception ex)
            {
                return RpcResponse.Failure(request.RequestId, "E_XICAD_SMOKE", ex.Message);
            }
        }

        private static RpcRequest CloneRequest(RpcRequest request, Newtonsoft.Json.Linq.JObject parameters) =>
            new RpcRequest
            {
                Protocol = request.Protocol,
                RequestId = request.RequestId,
                Method = request.Method,
                Params = parameters,
                SessionToken = request.SessionToken,
            };

        private static void EnsureXicadLoadedQuiet()
        {
            // C-2 HAZARD (carried over from the ZWCAD original, still unverified on AutoCAD):
            // SendStringToExecute BUFFERS text on the command line. The trailing " " appended
            // in SendToActiveDocument and the "\n" terminators below are what stop a half-typed
            // string from concatenating onto the next input. If a terminator is ever lost, the
            // pending text stays in the input line and the NEXT send appends to it, producing
            // garbage such as "_.LINE_.U" -> "Unknown command". Worse, a "_.U\n" queued while
            // the command line sits at the "Command:" prompt can be re-executed by the user's
            // next Enter, silently UNDOing the user's own work. Nothing here can detect that;
            // it is inherent to the only Undo path AutoCAD exposes (see TryRollback).
            string path = ForceXicadLisp.Replace("\\", "/");
            string loadCmd = "(progn (if (findfile \"" + path + "\") (load \"" + path + "\")) (princ)) ";
            SendToActiveDocument(loadCmd, activate: true);
            System.Threading.Thread.Sleep(600);
        }

        private static readonly object IdleGate = new object();
        private static readonly System.Collections.Generic.Queue<System.Action> IdleQueue =
            new System.Collections.Generic.Queue<System.Action>();
        private static bool IdleHooked;
        private static bool PumpBusy;
        private static System.Windows.Forms.Timer? JobTimer;

        private static void EnsureIdleHook()
        {
            if (IdleHooked)
            {
                return;
            }

            ZwApplication.Idle += OnApplicationIdle;
            IdleHooked = true;
            WriteBootLog("Application.Idle hooked for pipe-thread marshaling");

            // Application.Idle is NOT a reliable scheduler: when the ZWCAD window loses focus the
            // app stops processing input and Idle stops firing, which stalled a run at phase=launch
            // until its deadline. A WinForms timer ticks off WM_TIMER in the message loop, which a
            // background window still pumps, so use it as a second pump for the job state machine.
            try
            {
                JobTimer = new System.Windows.Forms.Timer { Interval = 60 };
                JobTimer.Tick += (s, e) => OnApplicationIdle(s, e);
                JobTimer.Start();
                WriteBootLog("WinForms timer pump started (interval=60ms) for job scheduling");
            }
            catch (System.Exception ex)
            {
                WriteBootLog("WinForms timer pump FAILED: " + ex.Message);
            }
        }

        private static void OnApplicationIdle(object sender, System.EventArgs e)
        {
            // Reentrancy guard: both Application.Idle and the WinForms timer call this on the same
            // application thread, and the timer can fire while an Idle action is still running.
            if (PumpBusy)
            {
                return;
            }

            PumpBusy = true;
            try
            {
                PumpOnce();
            }
            finally
            {
                PumpBusy = false;
            }
        }

        private static void PumpOnce()
        {
            // Advance any contract-verified Xicad run one phase per idle tick.
            // Runs even when the queue is empty, because a job is a multi-tick state machine.
            try { TickActiveJob(); }
            catch (System.Exception ex) { WriteBootLog("TickActiveJob FAILED: " + ex); }

            while (true)
            {
                System.Action? action;
                lock (IdleGate)
                {
                    if (IdleQueue.Count == 0)
                    {
                        return;
                    }

                    action = IdleQueue.Dequeue();
                }

                try
                {
                    action();
                }
                catch (System.Exception ex)
                {
                    WriteBootLog("Idle action FAILED: " + ex);
                }
            }
        }

        /// <summary>
        /// Named-pipe callbacks run on a worker thread. ZWCAD only reliably accepts
        /// SendStringToExecute from the application thread, so we queue onto Idle.
        /// </summary>
        private static void RunOnIdle(System.Action action, int timeoutMs = 15000)
        {
            EnsureIdleHook();
            using (var done = new System.Threading.ManualResetEventSlim(false))
            {
                System.Exception? error = null;
                lock (IdleGate)
                {
                    IdleQueue.Enqueue(() =>
                    {
                        try
                        {
                            action();
                        }
                        catch (System.Exception ex)
                        {
                            error = ex;
                        }
                        finally
                        {
                            done.Set();
                        }
                    });
                }

                if (!done.Wait(timeoutMs))
                {
                    throw new System.TimeoutException(
                        "Application.Idle did not run within " + timeoutMs + "ms — is ZWCAD focused/idle?");
                }

                if (error is not null)
                {
                    throw error;
                }
            }
        }

        private static void SendToActiveDocument(string command, bool activate)
        {
            RunOnIdle(() =>
            {
                Document? doc = ZwApplication.DocumentManager.MdiActiveDocument;
                if (doc is null)
                {
                    throw new InvalidOperationException("No active document.");
                }

                if (!command.EndsWith(" ", StringComparison.Ordinal) && !command.EndsWith("\n", StringComparison.Ordinal))
                {
                    command += " ";
                }

                doc.SendStringToExecute(command, activate, false, false);
                WriteBootLog("SendStringToExecute(idle): " + command.Replace("\n", "\\n"));
            });
        }

        private static string FormatPoint(double x, double y) =>
            string.Format(System.Globalization.CultureInfo.InvariantCulture, "{0:0.###},{1:0.###}", x, y);

        private sealed class ModelSnapshot
        {
            public int EntityCount { get; set; }
            public string? DocumentName { get; set; }
            public long Revision { get; set; }
        }

        private static ModelSnapshot SnapshotModel()
        {
            Document? doc = ZwApplication.DocumentManager.MdiActiveDocument;
            if (doc is null)
            {
                return new ModelSnapshot();
            }

            int count = 0;
            try
            {
                Database db = doc.Database;
                using (var tr = db.TransactionManager.StartOpenCloseTransaction())
                {
                    var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
                    var btr = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForRead);
                    foreach (ObjectId id in btr)
                    {
                        count++;
                    }

                    tr.Commit();
                }
            }
            catch
            {
                count = -1;
            }

            return new ModelSnapshot
            {
                EntityCount = count,
                DocumentName = doc.Name,
                Revision = ReadRevision(doc),
            };
        }
#else
        private static RpcResponse HandleSendCommand(RpcRequest request) =>
            RpcResponse.Failure(request.RequestId, "E_NO_SDK", "Built without ZWCAD SDK.");

        private static RpcResponse HandleXicadEnsureLoaded(RpcRequest request) =>
            RpcResponse.Failure(request.RequestId, "E_NO_SDK", "Built without ZWCAD SDK.");

        private static RpcResponse HandleXicadDrawWall(RpcRequest request) =>
            RpcResponse.Failure(request.RequestId, "E_NO_SDK", "Built without ZWCAD SDK.");

        private static RpcResponse HandleXicadDrawDoor(RpcRequest request) =>
            RpcResponse.Failure(request.RequestId, "E_NO_SDK", "Built without ZWCAD SDK.");

        private static RpcResponse HandleXicadDrawWindow(RpcRequest request) =>
            RpcResponse.Failure(request.RequestId, "E_NO_SDK", "Built without ZWCAD SDK.");

        private static RpcResponse HandleXicadSmoke(RpcRequest request) =>
            RpcResponse.Failure(request.RequestId, "E_NO_SDK", "Built without ZWCAD SDK.");
        private static RpcResponse HandleXicadSmokeStubUnused(RpcRequest request) =>
            RpcResponse.Failure(request.RequestId, "E_NO_SDK", "Built without ZWCAD SDK.");

        // No-SDK builds cannot probe: there is no Editor/SendStringToExecute to observe with.
        // The csproj makes an SDK-less build a hard error, so this is defence in depth only.
        private static RpcResponse HandleXicadProbeStub(RpcRequest request) =>
            RpcResponse.Failure(request.RequestId, "E_NO_SDK", "Built without AutoCAD SDK.");
#endif

        private static string GetPluginVersion()
        {
            Assembly asm = typeof(Plugin).Assembly;
            return asm.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion
                ?? asm.GetName().Version?.ToString()
                ?? "0.0.0";
        }
    }

    public static class Commands
    {
        [CommandMethod("AIC_START")]
        public static void AicStart()
        {
            new Plugin().Initialize();
            AicStatus();
        }

        [CommandMethod("AIC_STATUS")]
        public static void AicStatus()
        {
            Editor? editor = ZwApplication.DocumentManager.MdiActiveDocument?.Editor;
            if (editor is null)
            {
                return;
            }

            bool running = Plugin.Server is not null;
            string pipe = Plugin.LastPipeName ?? "(not started)";
            editor.WriteMessage(
                "\n[All-In-Cad] status: " + (running ? "listening" : "stopped") +
                "\n  protocol: " + ProtocolConstants.Version +
                "\n  pipe: \\\\.\\pipe\\" + pipe +
                "\n  session: " + Plugin.SessionId +
                "\n  sdk_bound: " + HostCapabilities.SdkBound +
                "\n  descriptor: " + (Plugin.DescriptorPath ?? "(none)"));
        }
    }

    internal static class HostCapabilities
    {
#if AIC_AUTOCAD_SDK
        internal const bool SdkBound = true;
#else
        internal const bool SdkBound = false;
#endif

        internal static readonly string[] All =
        {
            "system.ping",
            "host.context",
            "host.capabilities",
            "host.send_command",
            "host.draw_line",
            // AutoCAD port: added. Always answerable; reports whether XiCAD is actually present.
            "xicad.probe",
            "xicad.ensure_loaded",
            "xicad.draw_wall",
            "xicad.draw_door",
            "xicad.draw_window",
            "xicad.smoke_wall_door_window",
            "xicad.state",
            "xicad.run",
            "xicad.run_status",
            "xicad.rollback",
        };
    }
}
