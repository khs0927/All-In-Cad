"""Read-only PyRx capability probe for AutoCAD 2027 and ZWCAD 2026.

Load with PYLOAD inside a PyRx-enabled CAD host, then run AIC_PYRX_PROBE.
The command writes a small JSON report under LOCALAPPDATA/All-In-Cad/artifacts.
It deliberately avoids mutating the active drawing.
"""

from __future__ import annotations

import json
import os
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pyrx import Ap, Db


def _safe(callable_obj: Any) -> dict[str, Any]:
    try:
        value = callable_obj()
        return {"ok": True, "value": value}
    except Exception as exc:  # host APIs differ; preserve the exact failure
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _type_presence() -> dict[str, bool]:
    names = [
        "Line",
        "Circle",
        "Arc",
        "Polyline",
        "BlockReference",
        "DBText",
        "MText",
        "Dimension",
        "Hatch",
        "LayerTable",
    ]
    return {name: hasattr(Db, name) for name in names}


def _build_report() -> dict[str, Any]:
    db_result = _safe(Db.curDb)
    report: dict[str, Any] = {
        "schema": "all-in-cad/pyrx-probe/v1",
        "captured_at": datetime.now(UTC).isoformat(),
        "host_label": os.getenv("AIC_HOST_LABEL", "unset"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "db_types": _type_presence(),
        "current_database": {"ok": db_result["ok"]},
    }
    if not db_result["ok"]:
        report["current_database"]["error"] = db_result["error"]
        return report

    db = db_result["value"]
    model_result = _safe(db.modelSpace)
    report["model_space"] = {"ok": model_result["ok"]}
    if not model_result["ok"]:
        report["model_space"]["error"] = model_result["error"]
        return report

    model = model_result["value"]
    if hasattr(Db, "BlockReference"):
        block_result = _safe(lambda: model.objectIds(Db.BlockReference.desc()))
        report["block_reference_scan"] = {
            "ok": block_result["ok"],
            "count": len(block_result["value"]) if block_result["ok"] else None,
            "error": None if block_result["ok"] else block_result["error"],
        }
    return report


def _write_report(report: dict[str, Any]) -> Path:
    root = Path(os.getenv("LOCALAPPDATA", Path.home())) / "All-In-Cad" / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    label = str(report["host_label"]).replace(" ", "-").lower()
    path = root / f"pyrx-probe-{label}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


@Ap.Command()
def AIC_PYRX_PROBE() -> None:
    report = _build_report()
    path = _write_report(report)
    print(f"All-In-Cad PyRx probe: {path}")
