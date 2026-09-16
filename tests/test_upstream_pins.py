import json
from pathlib import Path

import pytest

from scripts.upstream.check_pins import AuditError, load_lock


def write_lock(path: Path, projects: list[dict[str, str]]) -> None:
    path.write_text(json.dumps({"projects": projects}), encoding="utf-8")


def test_load_lock_accepts_pinned_sha(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    write_lock(path, [{"repo": "owner/repo", "commit": "a" * 40}])
    assert load_lock(path)["projects"][0]["repo"] == "owner/repo"


def test_load_lock_rejects_duplicate_repo(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    project = {"repo": "owner/repo", "commit": "a" * 40}
    write_lock(path, [project, project])
    with pytest.raises(AuditError, match="duplicate"):
        load_lock(path)


def test_load_lock_rejects_short_sha(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    write_lock(path, [{"repo": "owner/repo", "commit": "abc"}])
    with pytest.raises(AuditError, match="40-character"):
        load_lock(path)
