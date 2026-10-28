"""Regression tests for version-agnostic external tool discovery.

Defect class under test: an external tool install directory carries a version
stamp (``ODAFileConverter 27.1.0``), so any detection that names a version
literal reports a perfectly healthy tool as missing and silently removes it
from the extraction fallback chain. These tests pin the *behaviour* (a version
this repository has never seen is still found) rather than a string.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from all_in_cad.extraction import ExtractionLane
from all_in_cad.tool_probes import (
    ODAFC_NAME,
    discover_external_tool,
    discover_odafc,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCTOR = REPO_ROOT / "scripts" / "windows" / "doctor.ps1"


def _install(root: Path, tool_stem: str, version: str, exe: str) -> Path:
    target = root / f"{tool_stem} {version}"
    target.mkdir(parents=True)
    exe_path = target / exe
    exe_path.write_bytes(b"MZ")
    return exe_path


@pytest.mark.parametrize("version", ["23.1.0", "25.3", "26.11.4", "27.1.0", "31.0.0", "9.9.9"])
def test_version_agnostic_odafc_discovery(tmp_path: Path, version: str) -> None:
    """A version directory this code has never seen must still be discovered."""
    root = tmp_path / "ODA"
    expected = _install(root, "ODAFileConverter", version, ODAFC_NAME)

    found = discover_odafc(search_roots=[root], env={})

    assert found == str(expected.resolve())


def test_newest_install_wins_when_several_are_present(tmp_path: Path) -> None:
    root = tmp_path / "ODA"
    _install(root, "ODAFileConverter", "26.4.0", ODAFC_NAME)
    newest = _install(root, "ODAFileConverter", "27.1.0", ODAFC_NAME)

    assert discover_odafc(search_roots=[root], env={}) == str(newest.resolve())


def test_unversioned_install_directory_still_found(tmp_path: Path) -> None:
    root = tmp_path / "ODA"
    (root / "ODAFileConverter").mkdir(parents=True)
    exe = root / "ODAFileConverter" / ODAFC_NAME
    exe.write_bytes(b"MZ")

    assert discover_odafc(search_roots=[root], env={}) == str(exe.resolve())


def test_env_override_beats_installed_copy(tmp_path: Path) -> None:
    root = tmp_path / "ODA"
    _install(root, "ODAFileConverter", "27.1.0", ODAFC_NAME)
    override = tmp_path / "custom" / ODAFC_NAME
    override.parent.mkdir()
    override.write_bytes(b"MZ")

    found = discover_odafc(
        search_roots=[root],
        env={"AIC_ODAFC_PATH": str(override)},
    )

    assert found == str(override.resolve())


def test_missing_tool_reports_absent_rather_than_guessing(tmp_path: Path) -> None:
    assert discover_odafc(search_roots=[tmp_path / "nothing"], env={}) is None


def test_discovery_is_not_oda_specific(tmp_path: Path) -> None:
    """The same helper must serve any external tool, so a new tool cannot
    reintroduce a version literal by copy-paste."""
    root = tmp_path / "tools"
    exe = _install(root, "dwg2dxf", "0.13.3", "dwg2dxf")

    found = discover_external_tool(
        executable_name="dwg2dxf",
        env_var="AIC_LIBREDWG_DWG2DXF",
        env={},
        search_roots=[root],
    )

    assert found == str(exe.resolve())


def test_oda_probe_reports_executable_when_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A detected lane must carry the path that was actually found, so the
    capability report cannot say 'available' without saying what is available."""
    import all_in_cad.tool_probes as tool_probes

    fake = Path("C:/Program Files/ODA/ODAFileConverter 27.1.0/ODAFileConverter.exe")
    monkeypatch.setattr(tool_probes, "discover_odafc", lambda **_kw: str(fake))

    probes = tool_probes.detect_tool_probes()
    oda = probes[ExtractionLane.ODA]

    assert oda.available is True
    assert oda.executable == str(fake)


def test_doctor_script_does_not_pin_an_oda_version() -> None:
    """Source-level proxy for the same defect in doctor.ps1.

    The behavioural oracle for the Python side is the parametrized case above;
    this guards the PowerShell twin, which has no importable seam. It fails if a
    version literal is reintroduced into the ODA detection block.
    """
    if sys.platform != "win32" or not DOCTOR.is_file():
        pytest.skip("doctor.ps1 is a Windows-only script")

    text = DOCTOR.read_text(encoding="utf-8")
    block = re.search(r"\$odaExeName.*?Add-Check \"ODA File Converter\"[^\n]*", text, re.S)
    assert block is not None, "ODA detection block not found in doctor.ps1"

    versioned = re.findall(r"ODAFileConverter[^\"'\s]*\s+\d+\.\*", block.group(0))
    assert not versioned, f"version-pinned ODA path reintroduced: {versioned}"
