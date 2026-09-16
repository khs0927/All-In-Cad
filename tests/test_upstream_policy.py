from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


def _load_check_pins() -> ModuleType:
    script = Path(__file__).resolve().parents[1] / "scripts" / "upstream" / "check_pins.py"
    spec = importlib.util.spec_from_file_location("all_in_cad_check_pins", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load upstream policy checker: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CHECK_PINS = _load_check_pins()
AuditError = _CHECK_PINS.AuditError
load_lock = _CHECK_PINS.load_lock


def _write_lock(tmp_path: Path, projects: list[dict[str, object]]) -> Path:
    path = tmp_path / "upstream.lock.json"
    path.write_text(json.dumps({"projects": projects}), encoding="utf-8")
    return path


def _project(**overrides: object) -> dict[str, object]:
    project: dict[str, object] = {
        "repo": "example/project",
        "commit": "a" * 40,
        "license": "MIT",
        "integration": "reference-and-adapt",
        "use": ["test fixture"],
    }
    project.update(overrides)
    return project


def test_repository_lock_passes_policy_validation() -> None:
    root = Path(__file__).resolve().parents[1]
    lock = load_lock(root / "upstream" / "upstream.lock.json")
    assert len(lock["projects"]) >= 1


@pytest.mark.parametrize("license_name", ["GPL-3.0", "LGPL-3.0"])
def test_copyleft_project_cannot_be_inprocess(tmp_path: Path, license_name: str) -> None:
    path = _write_lock(
        tmp_path,
        [_project(license=license_name, integration="python-dependency")],
    )

    with pytest.raises(AuditError, match="copyleft dependency must remain external"):
        load_lock(path)


@pytest.mark.parametrize(
    "integration",
    ["external-dependency", "external-executable-only"],
)
def test_copyleft_project_is_allowed_only_as_external_boundary(
    tmp_path: Path,
    integration: str,
) -> None:
    path = _write_lock(
        tmp_path,
        [_project(license="GPL-3.0", integration=integration)],
    )

    lock = load_lock(path)
    assert lock["projects"][0]["integration"] == integration


@pytest.mark.parametrize("integration", ["not-external", "external-vendored", "external-inprocess"])
def test_copyleft_project_rejects_deceptive_external_labels(
    tmp_path: Path,
    integration: str,
) -> None:
    path = _write_lock(
        tmp_path,
        [_project(license="GPL-3.0", integration=integration)],
    )

    with pytest.raises(AuditError, match="copyleft dependency must remain external"):
        load_lock(path)


def test_duplicate_repository_is_rejected(tmp_path: Path) -> None:
    path = _write_lock(tmp_path, [_project(), _project(commit="b" * 40)])

    with pytest.raises(AuditError, match="duplicate repo"):
        load_lock(path)


def test_non_full_commit_sha_is_rejected(tmp_path: Path) -> None:
    path = _write_lock(tmp_path, [_project(commit="abc123")])

    with pytest.raises(AuditError, match="40-character commit SHA"):
        load_lock(path)
