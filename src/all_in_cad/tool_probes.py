from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

from .extraction import ExtractionLane, ToolProbe

ACADSHARP_ENV_VAR = "AIC_ACADSHARP_PROBE"
LIBREDWG_ENV_VAR = "AIC_LIBREDWG_DWG2DXF"
ODAFC_ENV_VAR = "AIC_ODAFC_PATH"

ACADSHARP_PROBE_NAME = "AllInCad.ACadSharpProbe.exe"
ODAFC_NAME = "ODAFileConverter.exe"

# ezdxf resolves the Windows ODA converter through a single config option whose
# shipped default is a fixed, version-less path. We do not edit ezdxf; we set the
# option to what we actually discovered so `odafc.is_installed()` and
# `odafc.readfile()` agree with this module's own detection.
EZDXF_ODAFC_SECTION = "odafc-addon"
EZDXF_ODAFC_WIN_PATH_KEY = "win_exec_path"


def detect_tool_probes(
    *,
    acadsharp_executable: str | None = None,
    libredwg_executable: str | None = None,
) -> dict[ExtractionLane, ToolProbe]:
    """Detect host-independent extraction tools without importing CAD-host APIs."""
    ezdxf_available = importlib.util.find_spec("ezdxf") is not None

    oda = discover_odafc()
    if ezdxf_available and oda is not None:
        configure_ezdxf_odafc(oda)
    oda_available = oda is not None or (ezdxf_available and _odafc_reports_installed())

    acadsharp = _resolve_file(
        acadsharp_executable
        or os.environ.get(ACADSHARP_ENV_VAR)
        or _repo_acadsharp_probe()
    )
    libredwg = _resolve_executable(
        libredwg_executable
        or os.environ.get(LIBREDWG_ENV_VAR)
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
            executable=oda,
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
    """Report installation detection only; availability is not execution evidence."""
    return {
        lane.value: {
            "available": bool(probe.available),
            "executable": probe.executable,
            "version": probe.version,
            "verification_kind": "installation_detection",
            "detection_status": "DETECTED" if probe.available else "NOT_DETECTED",
            "execution_status": "NOT_RUN",
            "fixture_status": "NOT_RUN",
            "execution_allowed": False,
        }
        for lane, probe in sorted(probes.items(), key=lambda item: item[0].value)
    }


def discover_odafc(
    *,
    search_roots: Sequence[Path] | None = None,
    env: dict[str, str] | None = None,
) -> str | None:
    """Locate the ODA File Converter executable without pinning a version.

    ODA installs into a version-stamped directory
    (``C:\\Program Files\\ODA\\ODAFileConverter 27.1.0\\``), so any detection that
    names a version literal silently goes stale on the next ODA release. This
    scans the install roots for ``<name>*`` directories and prefers the highest
    version found.
    """
    return discover_external_tool(
        executable_name=ODAFC_NAME,
        env_var=ODAFC_ENV_VAR,
        env=env,
        search_roots=odafc_search_roots() if search_roots is None else search_roots,
    )


def odafc_search_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        base = os.environ.get(var)
        if base:
            roots.append(Path(base) / "ODA")
    return tuple(roots)


def discover_external_tool(
    *,
    executable_name: str,
    env_var: str | None = None,
    env: dict[str, str] | None = None,
    search_roots: Iterable[Path] = (),
) -> str | None:
    """Version-agnostic external tool lookup: env override, PATH, then roots.

    The same order is used for every external tool so that adding a tool cannot
    reintroduce a per-tool version-pinned path.
    """
    environ = os.environ if env is None else env

    if env_var:
        override = _resolve_executable(environ.get(env_var))
        if override is not None:
            return override

    for command in (executable_name, Path(executable_name).stem):
        found = _resolve_executable(command)
        if found is not None:
            return found

    return _scan_version_agnostic(search_roots, executable_name)


def _scan_version_agnostic(search_roots: Iterable[Path], executable_name: str) -> str | None:
    candidates: list[tuple[tuple[int, ...], str]] = []
    for root in search_roots:
        root = Path(root)
        if not root.is_dir():
            continue
        # <root>/<exe>
        direct = root / executable_name
        if direct.is_file():
            candidates.append(((), str(direct.resolve())))
        # <root>/<name><version>/<exe>
        for entry in root.glob(f"{Path(executable_name).stem}*"):
            if not entry.is_dir():
                continue
            nested = entry / executable_name
            if nested.is_file():
                candidates.append((_version_key(entry.name), str(nested.resolve())))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[1]


def _version_key(name: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", name))


def _repo_acadsharp_probe() -> str | None:
    """Default to the ACadSharp probe this repository already builds."""
    root = _repository_root()
    if root is None:
        return None
    bin_root = root / "native" / "headless" / "bin"
    if not bin_root.is_dir():
        return None
    matches = sorted(bin_root.rglob(ACADSHARP_PROBE_NAME))
    return str(matches[-1].resolve()) if matches else None


def _repository_root() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / "native" / "headless").is_dir() and (parent / "pyproject.toml").is_file():
            return parent
    return None


def configure_ezdxf_odafc(executable: str) -> None:
    """Point ezdxf's odafc addon at the discovered converter.

    This sets a supported ezdxf configuration option; the installed package is
    left untouched.
    """
    try:
        import ezdxf
    except ImportError:
        return
    ezdxf.options.set(EZDXF_ODAFC_SECTION, EZDXF_ODAFC_WIN_PATH_KEY, executable)


def _odafc_reports_installed() -> bool:
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
