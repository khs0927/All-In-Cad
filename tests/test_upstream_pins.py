import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


def _load_audit_module() -> ModuleType:
    script = Path(__file__).parents[1] / "scripts" / "upstream" / "check_pins.py"
    spec = importlib.util.spec_from_file_location("aic_check_pins", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load upstream audit script")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = _load_audit_module()
AuditError = audit.AuditError
load_lock = audit.load_lock


def project(
    repo: str = "owner/repo",
    commit: str = "a" * 40,
    license_name: str = "MIT",
    integration: str = "reference-and-adapt",
) -> dict[str, object]:
    return {
        "repo": repo,
        "commit": commit,
        "license": license_name,
        "integration": integration,
        "use": ["test fixture"],
    }


def write_lock(path: Path, projects: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"projects": projects}), encoding="utf-8")


def test_load_lock_accepts_pinned_sha(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    write_lock(path, [project()])
    assert load_lock(path)["projects"][0]["repo"] == "owner/repo"


def test_load_lock_rejects_duplicate_repo(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    fixture = project()
    write_lock(path, [fixture, fixture])
    with pytest.raises(AuditError, match="duplicate"):
        load_lock(path)


def test_load_lock_rejects_short_sha(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    write_lock(path, [project(commit="abc")])
    with pytest.raises(AuditError, match="40-character"):
        load_lock(path)


def test_load_lock_rejects_copyleft_vendoring(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    write_lock(path, [project(license_name="GPL-3.0", integration="vendor")])
    with pytest.raises(AuditError, match="must remain external"):
        load_lock(path)
