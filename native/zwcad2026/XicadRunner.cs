using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using AllInCad.NativeProtocol;
using Newtonsoft.Json.Linq;

#if AIC_HAS_ZWCAD_SDK
using ZwSoft.ZwCAD.ApplicationServices;
using ZwSoft.ZwCAD.DatabaseServices;
using ZwApplication = ZwSoft.ZwCAD.ApplicationServices.Application;
#endif

namespace AllInCad.ZWCAD2026
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

        private static int CountEntities(ZwSoft.ZwCAD.DatabaseServices.Database db)
        {
            int n = 0;
            ZwSoft.ZwCAD.DatabaseServices.Transaction? tr =
                db.TransactionManager.StartTransaction();            try
            {
                ZwSoft.ZwCAD.DatabaseServices.BlockTable bt =
                    (ZwSoft.ZwCAD.DatabaseServices.BlockTable)tr.GetObject(
                        db.BlockTableId,
                        ZwSoft.ZwCAD.DatabaseServices.OpenMode.ForRead);
                foreach (ZwSoft.ZwCAD.DatabaseServices.ObjectId id in bt)
                {
                    ZwSoft.ZwCAD.DatabaseServices.BlockTableRecord btr =
                        (ZwSoft.ZwCAD.DatabaseServices.BlockTableRecord)tr.GetObject(
                            id, ZwSoft.ZwCAD.DatabaseServices.OpenMode.ForRead);
                    foreach (ZwSoft.ZwCAD.DatabaseServices.ObjectId _ in btr) { n++; }
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
            // Belt-and-braces: the SDK IsInCommand() runtime behaviour is unverified, and a
            // false negative here would skip legitimate inputs or abort a live command.
            try
            {
                Document? doc = ZwApplication.DocumentManager.MdiActiveDocument;
                if (doc is not null && doc.IsInCommand()) return true;
            }
            catch { }
            try
            {
                string a = ZwApplication.GetSystemVariable("CMDACTIVE")?.ToString() ?? "0";
                if (int.TryParse(a, out int n) && n != 0) return true;
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
                try
                {
                    doc.Database.Undo();
                    ok = true;
                    how = "Database.Undo()";
                }
                catch (System.Exception ex)
                {
                    ok = false;
                    how = "Database.Undo() failed: " + ex.Message;
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
                        doc.Database.StartUndoRecord();
                    }
                    catch (System.Exception ex)
                    {
                        // ZWCAD throws NotImplementedException here; fall back to the string U command.
                        job.UndoRecording = false;
                        job.Log("StartUndoRecord unavailable (" + ex.GetType().Name +
                                "); rollback will use _.U string fallback");
                    }
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

        private static void TryRollback(XicadRunJob job, string why)
        {
            try
            {
                Document? doc = ZwApplication.DocumentManager.MdiActiveDocument;
                if (doc is null) return;
                if (doc.IsInCommand())
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
                        doc.Database.Undo();
                    }
                    else
                    {
                        // StartUndoRecord is not implemented in ZWCAD; use the string command.
                        doc.SendStringToExecute("_.U\n", true, false, false);
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
}
