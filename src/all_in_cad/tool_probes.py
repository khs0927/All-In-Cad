from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

from .extraction import ExtractionLane, ToolProbe


def detect_tool_probes(
    *,
    acadsharp_executable: str | None = None,
    libredwg_executable: str | None = None,
) -> dict[ExtractionLane, ToolProbe]:
    """Detect host-independent extraction tools without importing CAD-host APIs."""
    ezdxf_available = importlib.util.find_spec("ezdxf") is not None
    oda_available = _oda_is_installed() if ezdxf_available else False

    acadsharp = _resolve_file(
        acadsharp_executable or os.environ.get("AIC_ACADSHARP_PROBE")
    )
    libredwg = _resolve_executable(
        libredwg_executable
        or os.environ.get("AIC_LIBREDWG_DWG2DXF")
        or "dwg2dxf"
    )

    return {
        ExtractionLane.ACADSHARP: ToolProbe(
            lane=ExtractionLane.ACADSHARP,
            available=acadsharp is not None,
            executable=acadsharp,
        ),
        ExtractionLane.ODA: ToolProbe(
            lane=ExtractionLane.ODA,
            available=oda_available,
        ),
        ExtractionLane.EZDXF: ToolProbe(
            lane=ExtractionLane.EZDXF,
            available=ezdxf_available,
            executable=sys.executable if ezdxf_available else None,
        ),
        ExtractionLane.LIBREDWG: ToolProbe(
            lane=ExtractionLane.LIBREDWG,
            available=libredwg is not None,
            executable=libredwg,
        ),
    }


def capability_report(probes: dict[ExtractionLane, ToolProbe]) -> dict[str, dict[str, object]]:
    return {
        lane.value: {
            "available": bool(probe.available),
            "executable": probe.executable,
            "version": probe.version,
        }
        for lane, probe in sorted(probes.items(), key=lambda item: item[0].value)
    }


def _oda_is_installed() -> bool:
    try:
        from ezdxf.addons import odafc
    except ImportError:
        return False
    try:
        return bool(odafc.is_installed())
    except OSError:
        return False


def _resolve_file(candidate: str | None) -> str | None:
    if not candidate:
        return None
    path = Path(candidate).expanduser()
    if path.is_file():
        return str(path.resolve())
    discovered = shutil.which(candidate)
    return str(Path(discovered).resolve()) if discovered else None


def _resolve_executable(candidate: str | None) -> str | None:
    if not candidate:
        return None
    path = Path(candidate).expanduser()
    if path.is_file():
        return str(path.resolve())
    discovered = shutil.which(candidate)
    return str(Path(discovered).resolve()) if discovered else None
