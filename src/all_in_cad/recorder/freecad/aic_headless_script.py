"""Minimal runnable skeleton for a headless FreeCAD AIC job. ARTIFACT-FIRST.

THE ONE RULE THAT MATTERS
-------------------------
This script MUST leave a UTF-8 JSON verdict record on disk, on BOTH the
success and the failure path:

    <workdir>/out/aic_verdict.json

    {"schema": "aic.verdict/1", "status": "ok" | "failed",
     "reason": "...", "objects": 19, "layers": ["CEN1", ...], ...}

If that file is missing, unparseable, or says status != "ok", the wrapper
reports FAILURE - no matter that the process exited 0 and printed nothing
wrong. The wrapper treats the verdict record as the ONLY authoritative
signal. A run that produces no artifact is a failed run: in a drawing
pipeline it means a silently lost drawing.

WHY YOU MUST NOT TRUST stdout (observed on this host)
-------------------------------------------------------
A parent run of freecadcmd.exe on a real DXF produced its output files, built
19 objects (11 Part::Feature shapes plus layers CEN1/WAL1/WAL2/DOOR/DOOR_ELE
plus the layer container) and exited 0 - while stdout carried NOTHING: no
[AIC-BEGIN], no [AIC-OK], no [AIC-FAIL]. WHY it was empty is undetermined, and
it is not reproducible in every run: live runs of the wrapper on the same
host did see the markers. Treat the channel as unreliable either way.

So print()-based markers are not the protocol. They are still emitted, as an
AUXILIARY signal, via FreeCAD.Console as well, so they may reach the
--log-file. The verdict record is what the wrapper actually adjudicates.

WRITING THE RECORD: use freecad_runner.write_verdict() when the wrapper is
importable, otherwise the local _write_verdict() below does the same thing
(temp file + atomic replace). Do not let an exception skip the record: wrap
everything and write status="failed" in the except path.

ENTRYPOINT GUARD
----------------
FreeCAD imports the .py argument rather than running it as __main__, so
    if __name__ == "__main__": main()
becomes a silent no-op (exit 0, no artifacts). freecad_runner.prepare_script()
AST-rewrites the guard into a temp copy and runs that; the original file on
disk is never modified. Keep your real work at module top level anyway.

HOW TO USE
----------
    run_freecad_script("aic_headless_script.py",
                       verdict_path="out/aic_verdict.json",
                       expect_artifacts=["out/aic_verdict.json", "out/drawing.dxf"])

Use sys.exit(2) as belt-and-braces when you already recorded a failure; do
not rely on exit codes for success, since exception paths are swallowed and
the process still returns 0.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

MARKER_BEGIN = "[AIC-BEGIN]"
MARKER_OK = "[AIC-OK]"
MARKER_FAIL = "[AIC-FAIL:"
VERDICT_SCHEMA = "aic.verdict/1"

OUT_DIR = Path("out")
ARTIFACT = OUT_DIR / "aic_result.json"
VERDICT = OUT_DIR / "aic_verdict.json"


def _echo(text: str) -> None:
    """Best-effort marker emission. The verdict record does not depend on it."""
    print(text, flush=True)
    try:                       # console output may reach the --log-file
        import FreeCAD
        FreeCAD.Console.PrintMessage(text + "\n")
    except Exception:
        pass


def _write_verdict(status: str, reason: str = "", objects: int | None = None,
                   layers=None, artifacts=None) -> None:
    """Write the authoritative JSON verdict record (atomic replace)."""
    rec = {
        "schema": VERDICT_SCHEMA,
        "status": status,
        "reason": reason,
        "script": "aic_headless_script.py",
        "objects": objects,
        "layers": list(layers or []),
        "artifacts": list(artifacts or []),
        "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    VERDICT.parent.mkdir(parents=True, exist_ok=True)
    tmp = VERDICT.with_suffix(".json.part")
    tmp.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(VERDICT)


def run() -> dict:
    """Do the actual FreeCAD work here. FreeCAD is imported lazily so this
    module also imports cleanly outside FreeCAD (e.g. in the wrapper tests)."""
    import FreeCAD
    import Part

    doc_name = "aic_headless"
    doc = FreeCAD.newDocument(doc_name)
    box = doc.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = 10.0, 6.0, 2.0
    doc.recompute()

    volume = Part.Shape(box.Shape).Volume
    if volume <= 0:
        raise RuntimeError("degenerate solid: volume <= 0")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(
        json.dumps({"status": "built", "volume": volume}, indent=2), encoding="utf-8"
    )
    FreeCAD.closeDocument(doc_name)
    return {"objects": 1, "layers": [], "volume": volume}


def main() -> int:
    # The record is written on BOTH paths. This is the contract the wrapper
    # adjudicates against; the marker echoes below are auxiliary only.
    _echo(f"{MARKER_BEGIN} {os.getpid()}")
    try:
        info = run()
    except BaseException as exc:  # noqa: BLE001 - we must record, not swallow
        reason = f"{type(exc).__name__}:{exc}".replace("]", ")")[:200]
        traceback.print_exc()
        _write_verdict("failed", reason=reason)
        _echo(f"{MARKER_FAIL}{reason}]")
        return 2
    _write_verdict(
        "ok",
        reason="built",
        objects=info.get("objects"),
        layers=info.get("layers"),
        artifacts=[{"path": str(ARTIFACT), "kind": "json"}],
    )
    _echo(MARKER_OK)
    return 0


# RULE 1: unconditional call at MODULE TOP LEVEL. The
# `if __name__ == "__main__":` guard is deliberately NOT used, because FreeCAD
# imports this file rather than executing it as __main__.
sys.exit(main())
