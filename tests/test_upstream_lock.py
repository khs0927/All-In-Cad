from __future__ import annotations

import json
from pathlib import Path

LOCK_PATH = Path("upstream/upstream.lock.json")
REQUIRED_REPOS = {
    "bimwright/dwg-mcp",
    "CEXT-Dan/PyRx",
    "FsDiG/Fs.Fox.CAD",
    "beiming183-cloud/AutoCAD-MCP",
    "LokmenoWer/best-cad-mcp",
    "jeremylongshore/cad-ai-agent",
    "dalingo81/ZWCAD-MCP",
    "mozman/ezdxf",
    "DomCR/ACadSharp",
    "LibreDWG/libredwg",
    "U-C4N/Autocad-MCP",
    "AnCode666/multiCAD-mcp",
    "puran-water/autocad-mcp",
    "Psalmustrack/lambdacad-mcp",
}


def load_projects() -> dict[str, dict[str, object]]:
    data = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return {project["repo"]: project for project in data["projects"]}


def test_required_cad_upstreams_are_pinned() -> None:
    projects = load_projects()
    assert REQUIRED_REPOS <= projects.keys()
    assert len(projects) == len(REQUIRED_REPOS)


def test_copyleft_upstreams_remain_external() -> None:
    projects = load_projects()
    for repo in ("CEXT-Dan/PyRx", "LibreDWG/libredwg"):
        assert "external" in str(projects[repo]["integration"])


def test_acadsharp_is_primary_direct_dwg_lane() -> None:
    projects = load_projects()
    uses = projects["DomCR/ACadSharp"]["use"]
    assert isinstance(uses, list)
    assert "primary headless DWG direct parser" in uses
