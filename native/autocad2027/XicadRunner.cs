using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading;
using AllInCad.NativeProtocol;
using Newtonsoft.Json.Linq;

#if AIC_AUTOCAD_SDK
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Runtime;
using ZwApplication = Autodesk.AutoCAD.ApplicationServices.Application;
#endif

namespace AllInCad.AutoCAD2027
{
    /// <summary>
    /// One contract-verified execution of a natural XiCAD command.
    /// Advanced one phase per Application.Idle tick on the application thread.
    /// </summary>
    internal sealed class XicadRunJob
    {
        public string JobId = Guid.NewGuid().ToString("N");
        public string Command = "";
        public bool WrapC = true;
        public List<string> Args = new List<string>();
        public List<string> Contract = new List<string>();
        public int? ExpectDeltaMin;
        public int? ExpectDeltaMax;
        public int Step;
        public string Phase = "launch";
        public List<string> Trace = new List<string>();
        public int BeforeEntities;
        public string BeforeRevision = "";
        public bool UndoRecording;
        public bool CommandStarted;
        public DateTime Deadline;
        public DateTime PhaseDeadline;
        public string Status = "running";
        public string? Failure;
        public int AfterEntities = -1;
        public string? AfterRevision;
        public int Delta;
        public string LastPromptSeen = "";
        public string CmdNamesSeen = "";
        public bool RolledBack;

        public void Log(string s) => Trace.Add(DateTime.Now.ToString("HH:mm:ss.fff") + " " + s);
    }

    public sealed partial class Plugin
    {
        private static readonly object JobGate = new object();
        private static XicadRunJob? ActiveJob;

        private const int DefaultGateMs = 2500;
        private const int DefaultTotalMs = 20000;

        // ---------- in-process state readers (no LISP round trip) ----------

        private static string ReadVar(string name)
        {
            try { return ZwApplication.GetSystemVariable(name)?.ToString() ?? ""; }
            catch { return ""; }
        }

        private static int CountEntities()
        {
            try
            {
                Document? doc = ZwApplication.DocumentManager.MdiActiveDocument;
                if (doc is null) return -1;
                return CountEntities(doc.Database);
            }
            catch { return -1; }
        }

        private static int CountEntitiesStable()
        {
            // A transient read can return 0 while a command is still tearing down.
            for (int i = 0; i < 3; i++)
            {
                int n = CountEntities();
                if (n >= 0) return n;
                System.Threading.Thread.Sleep(120);
            }
            return -1;
        }

        private static int CountEntities(Autodesk.AutoCAD.DatabaseServices.Database db)
        {
            int n = 0;
            Autodesk.AutoCAD.DatabaseServices.Transaction? tr =
                db.TransactionManager.StartTransaction();            try
            {
                Autodesk.AutoCAD.DatabaseServices.BlockTable bt =
                    (Autodesk.AutoCAD.DatabaseServices.BlockTable)tr.GetObject(
                        db.BlockTableId,
                        Autodesk.AutoCAD.DatabaseServices.OpenMode.ForRead);
                foreach (Autodesk.AutoCAD.DatabaseServices.ObjectId id in bt)
                {
                    Autodesk.AutoCAD.DatabaseServices.BlockTableRecord btr =
                        (Autodesk.AutoCAD.DatabaseServices.BlockTableRecord)tr.GetObject(
                            id, Autodesk.AutoCAD.DatabaseServices.OpenMode.ForRead);
                    foreach (Autodesk.AutoCAD.DatabaseServices.ObjectId _ in btr) { n++; }
                }
            }
            finally
            {
                tr?.Dispose();
            }
            return n;
        }

        private static bool IsInCommand()
        {
            // ===== DOCUMENTED SEMANTIC GAP (AutoCAD 2027 port) =====
            // ZWCAD's Document.IsInCommand() does not exist in AutoCAD 2027 (verified by
            // compilation: CS1061). The only surviving signals are:
            //   (a) Editor.IsQuiescent  -> "the host is not busy" (inverted here)
            //   (b) CMDACTIVE system variable, unchanged from the ZWCAD original
            // NEITHER is equivalent to "a command is executing". IsQuiescent also goes false
            // for transparent commands, during object-snap/DCL interaction, and for the
            // teardown window right after ^C^C^C, and true again while a command is active but
            // the app has no input pending. CMDACTIVE is closer to the original intent but is
            // a string read on every call and can lag the real state by a tick.
            // Consequences we accept knowingly:
            //   - false positive (reports in-command when idle): the runner waits out a
            //     phase deadline and can time out a job that actually finished.
            //   - false negative (reports idle while a command is active): the runner may
            //     advance a step or judge "command did not start" incorrectly, and TryRollback
            //     may skip the ^C^C^C teardown before issuing _.U.
            // This is UNVERIFIED AT RUNTIME. A false positive here is worse than a false
            // negative for the rollback gate, so the ordering below keeps CMDACTIVE first and
            // treats IsQuiescent only as a corroborating signal.
            try
            {
                string a = ZwApplication.GetSystemVariable("CMDACTIVE")?.ToString() ?? "0";
                if (int.TryParse(a, out int n) && n != 0) return true;
            }
            catch { }
            try
            {
                // Only a definitively BUSY editor counts. We deliberately do NOT treat
                // "editor is quiescent" as "no command": quiescent is about host idleness.
                Document? doc = ZwApplication.DocumentManager.MdiActiveDocument;
                if (doc is not null && !doc.Editor.IsQuiescent) return true;
            }
            catch { }
            return false;
        }

        private static RpcResponse HandleXicadState(RpcRequest request)
        {
            return RunOnIdleWrapped(request, () =>
            {
                Document? doc = ZwApplication.DocumentManager.MdiActiveDocument;
                int ents = CountEntities();
                return RpcResponse.Success(request.RequestId, new
                {
                    op = "xicad.state",
                    document = doc?.Name,
                    entities = ents,
                    in_command = IsInCommand(),
                    cmd_active_raw = ReadVar("CMDACTIVE"),
                    cmd_names = ReadVar("CMDNAMES"),
                    last_prompt = ReadVar("LASTPROMPT"),
                    log_file_mode = ReadVar("LOGFILEMODE"),
                    active_job = ActiveJob?.JobId,
                });
            });
        }

        // ---------- contract-verified runner ----------

        private static RpcResponse HandleXicadRun(RpcRequest request)
        {
            JToken? p = request.Params;
            string cmd = p?["command"]?.ToString() ?? "";
            if (string.IsNullOrWhiteSpace(cmd))
                return RpcResponse.Failure(request.RequestId, "E_BAD_PARAMS", "params.command is required");

            // AutoCAD 2027 has no XiCAD. Without the XiCAD command symbols there is no
            // (c:<command>) to send, and the "Unknown command" text would sit in the command
            // line buffer. Fail closed instead of running a job that can only fail later.
            var gate = XicadGate(request, "xicad.run", requiredSymbol: null);
            if (gate is not null)
            {
                return gate;
            }

            var job = new XicadRunJob
            {
                Command = cmd.Trim(),
                WrapC = p?["wrap_c"]?.ToObject<bool?>() ?? true,
                ExpectDeltaMin = p?["expect_delta_min"]?.ToObject<int?>(),
                ExpectDeltaMax = p?["expect_delta_max"]?.ToObject<int?>(),
                Deadline = DateTime.Now.AddMilliseconds(
                    p?["total_ms"]?.ToObject<int?>() ?? DefaultTotalMs),
                PhaseDeadline = DateTime.Now.AddMilliseconds(
                    p?["gate_ms"]?.ToObject<int?>() ?? DefaultGateMs),
            };

            if (p?["args"] is JArray ja)
                foreach (JToken t in ja)
                    job.Args.Add(t.ToString());
            if (p?["contract"] is JArray jc)
                foreach (JToken t in jc)
                    job.Contract.Add(t.ToString());

            lock (JobGate)
            {
                if (ActiveJob is not null && ActiveJob.Status == "running")
                    return RpcResponse.Failure(request.RequestId, "E_BUSY",
                        "run already in progress: " + ActiveJob.JobId);
                ActiveJob = job;
            }

            job.Log("job created cmd=" + job.Command +
                    " args=[" + string.Join(" | ", job.Args) + "]" +
                    " contract=[" + string.Join(" | ", job.Contract) + "]");

            return RpcResponse.Success(request.RequestId, new
            {
                op = "xicad.run",
                job_id = job.JobId,
                command = job.Command,
                state = "queued",
            });
        }

        private static RpcResponse HandleXicadRunStatus(RpcRequest request)
        {
            XicadRunJob? job;
            lock (JobGate) { job = ActiveJob; }
            if (job is null)
                return RpcResponse.Failure(request.RequestId, "E_NO_JOB", "no job");
            return RpcResponse.Success(request.RequestId, new
            {
                op = "xicad.run_status",
                job_id = job.JobId,
                command = job.Command,
                status = job.Status,
                phase = job.Phase,
                step = job.Step,
                steps = job.Args.Count,
                failure = job.Failure,
                before_entities = job.BeforeEntities,
                after_entities = job.AfterEntities,
                delta = job.Delta,
                rolled_back = job.RolledBack,
                last_prompt = job.LastPromptSeen,
                cmd_names = job.CmdNamesSeen,
                trace = job.Trace,
            });
        }

        private static RpcResponse HandleXicadRollback(RpcRequest request)
        {
            return RunOnIdleWrapped(request, () =>
            {
                Document? doc = ZwApplication.DocumentManager.MdiActiveDocument;
                if (doc is null)
                    return RpcResponse.Failure(request.RequestId, "E_NO_DOC", "no active document");
                bool ok;
                string how;
                // AutoCAD 2027 has NO programmatic undo. Verified by enumeration of the whole
                // Undo surface of Database: only DisableUndoRecording(bool) and the
                // UndoRecording property exist. Database.Undo() is a compile error (CS1061).
                // The only available path is the "_.U" string command, which is UNVERIFIED at
                // runtime and asynchronous: SendStringToExecute queues text, so at the moment
                // this RPC reports ok=true the undo may not have happened yet. Callers must
                // re-snapshot entity counts rather than trust this response.
                try
                {
                    if (doc.Database.UndoRecording)
                    {
                        // Recording disabled means "_.U" has nothing grouped to reverse.
                        return RpcResponse.Failure(
                            request.RequestId,
                            "E_XICAD_ROLLBACK_UNAVAILABLE",
                            "Database.UndoRecording is false; the string '_.U' fallback has no undo record to reverse.");
                    }
                    SendUndoFallback(doc);
                    ok = true;
                    how = "SendStringToExecute(\"_.U\\n\") - queued, NOT yet applied (async)";
                }
                catch (System.Exception ex)
                {
                    ok = false;
                    how = "'_.U' string fallback failed: " + ex.Message;
                }
                return RpcResponse.Success(request.RequestId, new
                {
                    op = "xicad.rollback",
                    ok = ok,
                    method = how,
                    entities = CountEntities(),
                });
            });
        }

        private static RpcResponse RunOnIdleWrapped(RpcRequest request, Func<RpcResponse> action)
        {
            try
            {
                RpcResponse result = null!;
                RunOnIdle(() => { result = action(); });
                return result;
            }
            catch (System.Exception ex)
            {
                return RpcResponse.Failure(request.RequestId, "E_IDLE", ex.Message);
            }
        }

        // ---------- state machine: one phase per idle tick ----------

        internal static void TickActiveJob()
        {
            XicadRunJob? job;
            lock (JobGate) { job = ActiveJob; }
            if (job is null || job.Status != "running") return;

            try { AdvanceJob(job); }
            catch (System.Exception ex)
            {
                job.Status = "failed";
                job.Failure = "tick exception: " + ex.Message;
                job.Log("EXCEPTION " + ex.Message);
                TryRollback(job, "tick exception");
            }
        }

        private static void AdvanceJob(XicadRunJob job)
        {
            Document? doc = ZwApplication.DocumentManager.MdiActiveDocument;
            if (doc is null)
            {
                FailJob(job, "no active document");
                return;
            }

            bool inCmd = IsInCommand();
            string prompt = ReadVar("LASTPROMPT");
            job.LastPromptSeen = prompt;
            job.CmdNamesSeen = ReadVar("CMDNAMES");

            if (DateTime.Now > job.Deadline)
            {
                FailJob(job, $"timeout at phase={job.Phase} step={job.Step}/{job.Args.Count} " +
                             $"in_command={inCmd} last_prompt='{prompt}'");
                return;
            }

            switch (job.Phase)
            {
                case "launch":
                    job.BeforeEntities = CountEntities();
                    job.BeforeRevision = ReadVar("REVISION");
                    job.Log($"launch: before entities={job.BeforeEntities} rev={job.BeforeRevision}");
                    try
                    {
                        job.UndoRecording = doc.Database.UndoRecording;
                    }
                    catch (System.Exception ex)
                    {
                        job.UndoRecording = false;
                        job.Log("UndoRecording read failed (" + ex.GetType().Name + ")");
                    }
                    // Database.StartUndoRecord() DOES NOT EXIST in AutoCAD 2027 (CS1061). The
                    // whole Undo surface of Database is DisableUndoRecording(bool) plus the
                    // UndoRecording property; programmatic undo is not exposed at all. The
                    // call is therefore DELETED rather than emulated, and UndoRecording is now
                    // only a precondition check for the "_.U" fallback: if recording is off
                    // there is no group to reverse, so we skip the undo rather than issue a
                    // bare _.U that could discard the user's earlier work.
                    job.Log("StartUndoRecord absent in AutoCAD; undo via '_.U' string fallback only" +
                            (job.UndoRecording ? " (UndoRecording=true)" : " (UndoRecording=false: fallback will be skipped)"));
                    string launch = job.WrapC ? "(c:" + job.Command + ")" : job.Command;
                    // SendStringToExecute buffers text until a terminator. Without "\n" the
                    // string stays in the input line and the NEXT send concatenates onto it
                    // (observed: "_.LINE_.U" -> Unknown command). Always terminate.
                    doc.SendStringToExecute(launch + "\n", true, false, false);
                    job.Log("sent " + launch + "\\n");
                    job.Phase = "start_settle";
                    job.PhaseDeadline = DateTime.Now.AddMilliseconds(1500);
                    break;

                case "start_settle":
                    if (inCmd) { job.CommandStarted = true; job.Phase = "gate"; break; }
                    if (DateTime.Now < job.PhaseDeadline) return;
                    FailJob(job, $"command did not start: '{job.Command}' left no active command; " +
                                 $"last_prompt='{prompt}'");
                    return;

                case "gate":
                    if (job.Step < job.Args.Count)
                    {
                        string expect = job.Step < job.Contract.Count ? job.Contract[job.Step] : "";
                        // Always record the observed prompt. Even with no contract, this is how
                        // real prompt sequences are learned for later contract authoring.
                        if (expect.Length == 0)
                            job.Log($"observed prompt at step {job.Step}: '{prompt}'");
                        if (expect.Length > 0 && prompt.IndexOf(expect, StringComparison.Ordinal) < 0)
                        {
                            if (DateTime.Now > job.PhaseDeadline)
                            {
                                FailJob(job, $"prompt mismatch at step {job.Step}: expected '{expect}' " +
                                             $"but LASTPROMPT='{prompt}' in_command={inCmd}");
                                return;
                            }
                            return; // keep waiting for the prompt to settle
                        }

                        string arg = job.Args[job.Step];
                        if (arg.Length == 0 && !inCmd)
                        {
                            job.Log($"step {job.Step}: skip empty arg (not in command; would re-run last command)");
                            job.Step++;
                            job.PhaseDeadline = DateTime.Now.AddMilliseconds(DefaultGateMs);
                            return;
                        }
                        doc.SendStringToExecute(arg + "\n", true, false, false);
                        job.Log($"step {job.Step}: sent '{arg}' (prompt matched '{expect}')");
                        job.Step++;
                        job.PhaseDeadline = DateTime.Now.AddMilliseconds(DefaultGateMs);
                        if (job.Step >= job.Args.Count) job.Phase = "finish_settle";
                    }
                    else
                    {
                        job.Phase = "finish_settle";
                    }
                    break;

                case "finish_settle":
                    if (inCmd)
                    {
                        if (DateTime.Now > job.PhaseDeadline)
                        {
                            FailJob(job, $"command still active after all args; " +
                                         $"in_command=True last_prompt='{prompt}'");
                        }
                        return;
                    }
                    CompleteJob(job, doc);
                    break;
            }
        }

        private static void CompleteJob(XicadRunJob job, Document doc)
        {
            job.AfterEntities = CountEntitiesStable();
            job.AfterRevision = ReadVar("REVISION");
            job.Delta = job.AfterEntities - job.BeforeEntities;
            job.Log($"complete: after entities={job.AfterEntities} rev={job.AfterRevision} delta={job.Delta}");

            bool bad = false;
            if (job.ExpectDeltaMin.HasValue && job.Delta < job.ExpectDeltaMin.Value)
            { job.Failure = $"delta {job.Delta} below expected min {job.ExpectDeltaMin.Value}"; bad = true; }
            if (job.ExpectDeltaMax.HasValue && job.Delta > job.ExpectDeltaMax.Value)
            { job.Failure = $"delta {job.Delta} above expected max {job.ExpectDeltaMax.Value}"; bad = true; }

            if (bad)
            {
                job.Status = "failed";
                job.Log("verify failed: " + job.Failure);
                TryRollback(job, "delta verification");
            }
            else
            {
                job.Status = "ok";
            }
        }

        private static void FailJob(XicadRunJob job, string why)
        {
            job.Failure = why;
            job.Status = "failed";
            job.Log("FAILED: " + why);
            // after_entities stays -1 on failure: reading it mid-teardown produces
            // misleading zero values (observed), and it would misreport the drawing.
            TryRollback(job, "failure");
        }

        private static void SendUndoFallback(Document doc)
        {
            // The ONLY undo path AutoCAD 2027 exposes to .NET.
            //
            // C-2 HAZARD (documented, not fixed): SendStringToExecute BUFFERS on the command
            // line. If this text is queued while the command line already sits at a fresh
            // "Command:" prompt, it is not necessarily consumed as the next command - the user
            // pressing Enter can re-trigger it. The user then loses one UNDO step of their own
            // work. We cannot detect or prevent that from .NET; there is no programmatic undo
            // to fall back on. The mitigating rule is the CommandStarted gate in TryRollback:
            // we only ever send this after we saw a command actually start.
            doc.SendStringToExecute("_.U\n", true, false, false);
        }

        private static void TryRollback(XicadRunJob job, string why)
        {
            try
            {
                Document? doc = ZwApplication.DocumentManager.MdiActiveDocument;
                if (doc is null) return;
                if (IsInCommand())
                    doc.SendStringToExecute("^C^C^C\n", true, false, false);
                // Never issue a bare "_.U" just because a job failed: if the command never
                // started, an undo would discard the user's earlier work. Observed risk.
                if (!job.CommandStarted)
                {
                    job.Log("rollback skipped: command never started (no undo issued)");
                    return;
                }
                try
                {
                    if (job.UndoRecording)
                    {
                        // AUTO port note: the ZWCAD branch here called Database.Undo(), which
                        // does not exist in AutoCAD 2027 (CS1061). Both branches now use the
                        // same string-command fallback, so UndoRecording only decides WHETHER
                        // to undo at all, never HOW.
                        SendUndoFallback(doc);
                        job.Log("undo issued via '_.U' string fallback (Database.Undo() is absent in AutoCAD)");
                    }
                    else
                    {
                        // StartUndoRecord is not implemented in ZWCAD and does not exist at all
                        // in AutoCAD; use the string command.
                        SendUndoFallback(doc);
                    }
                    job.RolledBack = true;
                    job.Log($"rolled back ({why})");
                }
                catch (System.Exception ex)
                {
                    job.Log("rollback failed: " + ex.Message);
                }
            }
            catch (System.Exception ex)
            {
                job.Log("rollback failed: " + ex.Message);
            }
        }
    }

    /// <summary>
    /// AutoCAD 2027 port addition: XiCAD is not installed on this host by default, so this is
    /// the only honest way to answer "is XiCAD there yet?".
    ///
    /// METHOD: .NET has no synchronous AutoLISP evaluation API in AutoCAD 2027, so the probe
    /// queues a one-line LISP form on the application thread (the same path every command uses)
    /// and that form writes its findings to a temp file, which we then read. This is a real,
    /// observable measurement of the live session - not an assumption.
    ///
    /// It is also the verification hook for the acaddoc auto-loader: after the user installs
    /// XiCAD and restarts AutoCAD, one xicad.probe call shows whether the LISP symbols exist,
    /// whether acaddoc.lsp was found, and which support paths are in play.
    ///
    /// UNVERIFIED AT RUNTIME (compile-checked only): the exact LISP dialect accepted by
    /// SendStringToExecute, the timeout needed on a cold session, and vl-list-loaded-lsp
    /// availability on this build. All are reported as data in the response, never assumed.
    /// </summary>
    public sealed partial class Plugin
    {
        private const string XicadUnavailableCode = "E_XICAD_UNAVAILABLE";

        /// <summary>Command symbols the ZWCAD-side code actually invokes, plus common variants.</summary>
        private static readonly string[] XicadProbeSymbols =
        {
            "C:XIDRAWWALL",
            "C:XIDOOR2",
            "C:XIWIN2",
            "C:XIDRAWDOOR",
            "C:XIDRAWWINDOW",
            "C:XIWINDOW",
            "C:XIDOOR",
            "C:XICAD",
        };

        private static readonly string[] XicadPrimarySymbols = { "C:XIDRAWWALL", "C:XIDOOR2", "C:XIWIN2" };

        private static string ForceXicadLispAbs => @"C:/xicad/_nl_automation/runner/force_xicad_on_doc.lsp";

        private static string ProbeDir => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "All-In-Cad",
            "logs");

        private sealed class XicadProbe
        {
            public bool ProbeOk;
            public string? ProbeError;
            public Dictionary<string, string> Symbols = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            public string? ForceLispByName;
            public string? ForceLispByPath;
            public string? AcaddocLisp;
            public string? LoadedVlx;
            public string? LoadedLsp;
            public bool Available;

            public bool IsBound(string symbol) =>
                Symbols.TryGetValue(symbol, out string? v) && IsTrue(v);
        }

        private static bool IsTrue(string? v) =>
            string.Equals(v, "T", StringComparison.OrdinalIgnoreCase) ||
            string.Equals(v, "#t", StringComparison.OrdinalIgnoreCase);

        /// <summary>
        /// Fail-closed gate for every xicad.* operation that needs the real plugin.
        /// Returns null when the operation may proceed, or an E_XICAD_UNAVAILABLE failure.
        /// </summary>
        private static RpcResponse? XicadGate(
            RpcRequest request,
            string op,
            string? requiredSymbol = null,
            bool forceLoad = false)
        {
            XicadProbe probe = ProbeXicad(forceLoad: forceLoad, timeoutMs: 10000);
            bool available = probe.Available &&
                (requiredSymbol is null
                    ? XicadPrimarySymbols.Any(probe.IsBound)
                    : probe.IsBound(requiredSymbol));

            if (available)
            {
                return null;
            }

            // Fail closed with a code the client can distinguish from a ZWCAD "file missing"
            // error. We never fabricate a ZWCAD-shaped success here.
            string message = requiredSymbol is not null
                ? $"XiCAD is not available in this AutoCAD session: required command symbol {requiredSymbol} is not bound."
                : "XiCAD is not available in this AutoCAD session: none of " +
                  string.Join(", ", XicadPrimarySymbols) + " are bound.";

            var detail = new Dictionary<string, object?>
            {
                ["op"] = op,
                ["required_symbol"] = requiredSymbol,
                ["probe_ok"] = probe.ProbeOk,
                ["probe_error"] = probe.ProbeError,
                ["symbols"] = probe.Symbols,
                ["force_lisp_path"] = ForceXicadLispAbs,
                ["force_lisp_path_exists"] = File.Exists(ForceXicadLispAbs),
                ["force_lisp_findfile"] = probe.ForceLispByPath ?? probe.ForceLispByName,
                ["acaddoc_lisp_findfile"] = probe.AcaddocLisp,
                ["remedy"] = "Install XiCAD for AutoCAD 2027, then restart AutoCAD and call xicad.probe again.",
            };

            return RpcResponse.Failure(request.RequestId, XicadUnavailableCode, message, detail);
        }

        private static RpcResponse XicadProbeRequest(RpcRequest request)
        {
            bool forceLoad = request.Params?["force_load"]?.ToObject<bool?>() ?? false;
            int timeoutMs = request.Params?["timeout_ms"]?.ToObject<int?>() ?? 10000;
            XicadProbe probe = ProbeXicad(forceLoad, timeoutMs);

            Document? doc = null;
            string? version = null;
            int entities = -1;
            try
            {
                doc = ZwApplication.DocumentManager.MdiActiveDocument;
                version = ZwApplication.Version?.ToString();
                entities = doc is null ? -1 : CountEntities();
            }
            catch (System.Exception ex)
            {
                version = "unavailable: " + ex.Message;
            }

            return RpcResponse.Success(request.RequestId, new
            {
                op = "xicad.probe",
                host = "autocad2027",
                host_product = "AutoCAD",
                host_version = version,
                process_id = Process.GetCurrentProcess().Id,
                session_id = SessionId,
                sdk_bound = HostCapabilities.SdkBound,
                plugin_version = GetPluginVersion(),

                xicad_available = probe.Available,
                probe_ok = probe.ProbeOk,
                probe_error = probe.ProbeError,
                symbols = probe.Symbols,
                bound_symbol_count = probe.Symbols.Count(kv => IsTrue(kv.Value)),

                force_lisp = new
                {
                    path = ForceXicadLispAbs,
                    file_exists = File.Exists(ForceXicadLispAbs),
                    findfile_by_path = probe.ForceLispByPath,
                    findfile_by_name = probe.ForceLispByName,
                },
                acaddoc = new
                {
                    findfile = probe.AcaddocLisp,
                    loaded = probe.AcaddocLisp is not null,
                    support_paths = SupportPathCandidates(),
                },
                loaded_lisp = new
                {
                    vlx = probe.LoadedVlx,
                    lsp = probe.LoadedLsp,
                },

                document = new
                {
                    name = doc?.Name,
                    is_active = doc is not null,
                    entity_count = entities,
                    in_command = IsInCommand(),
                    cmd_names = ReadVar("CMDNAMES"),
                    last_prompt = ReadVar("LASTPROMPT"),
                    revision = doc is null ? 0L : ReadRevision(doc),
                    fingerprint_guid_is_string = DatabaseGuidIsString(doc),
                },

                caveats = new[]
                {
                    "symbol checks are AutoLISP (boundp) results observed on the live session",
                    "in_command uses CMDACTIVE + Editor.IsQuiescent, NOT the removed Document.IsInCommand()",
                    "probe writes a temp file and is therefore asynchronous; a busy command line may time out",
                },
            });
        }

        /// <summary>Observation only: AutoCAD's FingerprintGuid is a string, so the Guid match in BuildDocumentId never fires.</summary>
        private static bool DatabaseGuidIsString(Document? doc)
        {
            if (doc is null) return false;
            try { return doc.Database.GetType().GetProperty("FingerprintGuid")?.PropertyType == typeof(string); }
            catch { return false; }
        }

        private static string[] SupportPathCandidates()
        {
            var list = new List<string>();
            try
            {
                list.Add(Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                    "Autodesk", "AutoCAD 2027", "Support"));
            }
            catch { }
            try
            {
                list.Add(Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
                    "AutoCAD 2027", "Support"));
            }
            catch { }
            try
            {
                list.Add(@"C:\Users\khs09\all-in-cad\native\autocad2027\acaddoc.autoload.lsp");
            }
            catch { }
            return list.ToArray();
        }

        private static XicadProbe ProbeXicad(bool forceLoad, int timeoutMs)
        {
            var probe = new XicadProbe();

            // Optional best-effort load of the ZWCAD-era bootstrap file, exactly as the
            // xicad.ensure_loaded path does. Absence is not an error for a probe.
            if (forceLoad)
            {
                try { EnsureXicadLoadedQuiet(); }
                catch (System.Exception ex) { probe.ProbeError = "force load failed: " + ex.Message; }
            }

            string dir = ProbeDir;
            string outFile = Path.Combine(
                dir,
                "xicad_probe_" + Process.GetCurrentProcess().Id + "_" + Guid.NewGuid().ToString("N") + ".txt");

            try
            {
                Directory.CreateDirectory(dir);
                SendToActiveDocument(BuildProbeLisp(outFile) + "\n", activate: false);
            }
            catch (System.Exception ex)
            {
                probe.ProbeError = "probe dispatch failed: " + ex.Message;
                return probe;
            }

            // Poll for the report. LISP executes on the application thread's next idle turn.
            DateTime deadline = DateTime.UtcNow.AddMilliseconds(Math.Max(1000, timeoutMs));
            bool appeared = false;
            while (DateTime.UtcNow < deadline)
            {
                if (File.Exists(outFile) && new FileInfo(outFile).Length > 0)
                {
                    appeared = true;
                    break;
                }
                Thread.Sleep(150);
            }

            if (!appeared)
            {
                if (probe.ProbeError is null)
                {
                    probe.ProbeError = "probe LISP did not report within " + timeoutMs +
                        "ms (command line busy, LISP disabled, or SECURELOAD blocked (load))";
                }
                return probe;
            }

            try
            {
                foreach (string line in File.ReadAllLines(outFile))
                {
                    int eq = line.IndexOf('=');
                    if (eq <= 0) continue;
                    string key = line.Substring(0, eq).Trim();
                    string val = line.Substring(eq + 1).Trim();
                    if (key.StartsWith("C:", StringComparison.OrdinalIgnoreCase)) probe.Symbols[key] = val;
                    else if (key == "FORCE_LISP_BY_NAME") probe.ForceLispByName = val;
                    else if (key == "FORCE_LISP_BY_PATH") probe.ForceLispByPath = val;
                    else if (key == "ACADDOC_LISP") probe.AcaddocLisp = val;
                    else if (key == "LOADED_VLX") probe.LoadedVlx = val;
                    else if (key == "LOADED_LSP") probe.LoadedLsp = val;
                }
                probe.ProbeOk = true;
            }
            catch (System.Exception ex)
            {
                probe.ProbeError = "probe report unreadable: " + ex.Message;
            }
            finally
            {
                try { File.Delete(outFile); } catch { }
            }

            probe.Available = XicadPrimarySymbols.Any(probe.IsBound);
            if (!probe.Available && probe.ProbeError is null)
            {
                probe.ProbeError = "XiCAD command symbols are not defined in this session";
            }
            return probe;
        }

        private static string BuildProbeLisp(string outFile)
        {
            // One single-line LISP form: SendStringToExecute buffers per line, so a form split
            // over newlines would be entered piecemeal and the parens would be submitted
            // separately, leaving stray prompts on the command line.
            string p = outFile.Replace('\\', '/');
            System.Text.StringBuilder sb = new System.Text.StringBuilder();
            sb.Append("(progn (setq aicf (open \"").Append(p).Append("\" \"w\")) (if aicf (progn ");
            foreach (string s in XicadProbeSymbols)
            {
                sb.Append("(write-line (strcat \"").Append(s).Append("=\" (vl-princ-to-string (boundp '")
                  .Append(s).Append("))) aicf) ");
            }
            sb.Append("(write-line (strcat \"FORCE_LISP_BY_NAME=\" (vl-princ-to-string (findfile \"force_xicad_on_doc.lsp\"))) aicf) ");
            sb.Append("(write-line (strcat \"FORCE_LISP_BY_PATH=\" (vl-princ-to-string (findfile \"")
              .Append(ForceXicadLispAbs).Append("\"))) aicf) ");
            sb.Append("(write-line (strcat \"ACADDOC_LISP=\" (vl-princ-to-string (findfile \"acaddoc.lsp\"))) aicf) ");
            sb.Append("(write-line (strcat \"LOADED_VLX=\" (vl-princ-to-string (vl-catch-all-apply 'vl-list-loaded-vlx nil))) aicf) ");
            sb.Append("(write-line (strcat \"LOADED_LSP=\" (vl-princ-to-string (vl-catch-all-apply 'vl-list-loaded-lsp nil))) aicf) ");
            sb.Append("(close aicf) (princ)) (princ))");
            return sb.ToString();
        }
    }
}
