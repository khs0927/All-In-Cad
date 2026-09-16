from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
API_ROOT = "https://api.github.com"
COPYLEFT_LICENSES = {"LGPL-3.0", "GPL-3.0"}
COPYLEFT_EXTERNAL_INTEGRATIONS = {"external-dependency", "external-executable-only"}


class AuditError(RuntimeError):
    pass


def _validate_project(project: dict[str, Any], seen: set[str]) -> None:
    repo = project.get("repo")
    commit = project.get("commit")
    license_name = project.get("license")
    integration = project.get("integration")
    uses = project.get("use")

    if not isinstance(repo, str) or repo.count("/") != 1:
        raise AuditError(f"invalid repo name: {repo!r}")
    if repo in seen:
        raise AuditError(f"duplicate repo in lock: {repo}")
    seen.add(repo)

    if not isinstance(commit, str) or SHA_RE.fullmatch(commit) is None:
        raise AuditError(f"invalid 40-character commit SHA for {repo}")
    if not isinstance(license_name, str) or not license_name:
        raise AuditError(f"missing license for {repo}")
    if not isinstance(integration, str) or not integration:
        raise AuditError(f"missing integration policy for {repo}")
    if not isinstance(uses, list) or not uses or not all(isinstance(item, str) for item in uses):
        raise AuditError(f"invalid use list for {repo}")

    if license_name in COPYLEFT_LICENSES and integration not in COPYLEFT_EXTERNAL_INTEGRATIONS:
        raise AuditError(f"copyleft dependency must remain external: {repo}")


def load_lock(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    projects = data.get("projects")
    if not isinstance(projects, list):
        raise AuditError("lock file must contain a projects list")

    seen: set[str] = set()
    for project in projects:
        if not isinstance(project, dict):
            raise AuditError("each project entry must be an object")
        _validate_project(project, seen)
    return data


def github_json(path: str, token: str | None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "All-In-Cad-upstream-audit",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{API_ROOT}{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise AuditError(f"GitHub API {path} returned HTTP {exc.code}") from exc


def audit_online(lock: dict[str, Any], token: str | None) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for project in lock["projects"]:
        repo = project["repo"]
        pinned = project["commit"]
        repository = github_json(f"/repos/{repo}", token)
        default_branch = repository["default_branch"]
        latest = github_json(f"/repos/{repo}/commits/{default_branch}", token)["sha"]
        github_json(f"/repos/{repo}/commits/{pinned}", token)
        results.append(
            {
                "repo": repo,
                "pinned": pinned,
                "latest": latest,
                "status": "current" if pinned == latest else "behind",
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path("upstream/upstream.lock.json"),
    )
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    try:
        lock = load_lock(args.lock)
        results: list[dict[str, str]] = []
        if args.online:
            results = audit_online(lock, os.environ.get("GITHUB_TOKEN"))
        payload = {
            "reviewed_at": lock.get("reviewed_at"),
            "project_count": len(lock["projects"]),
            "online": args.online,
            "results": results,
        }
        rendered = json.dumps(payload, indent=2, sort_keys=True)
        print(rendered)
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(rendered + "\n", encoding="utf-8")
        return 0
    except (AuditError, OSError, json.JSONDecodeError) as exc:
        print(f"upstream audit failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
