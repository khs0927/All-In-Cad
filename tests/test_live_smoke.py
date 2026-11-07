from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from all_in_cad.cli import main
from all_in_cad.live_smoke import (
    LEDGER_ITEMS,
    ROADMAP_PROMOTIONS,
    LiveSmokeEvidence,
    apply_roadmap,
    empty_ledger,
    evaluate,
    promotable,
    record,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "windows-live-smoke.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "windows-live-smoke.yml"
SHA = "a" * 64


def _step(step_id: str, status: str = "passed", **detail: Any) -> dict[str, Any]:
    return {
        "id": step_id,
        "status": status,
        "started_at": "2026-11-07T10:00:00.0000000Z",
        "duration_ms": 12,
        "detail": detail,
        "outputs": [{"path": f"{step_id}.json", "sha256": SHA}],
    }


def _evidence(mode: str = "write", **overrides: Any) -> dict[str, Any]:
    readback = {
        "handle": "1F75",
        "object_name": "AcDbLine",
        "com_modelspace_count": 1,
        "pipe_entity_count_after": 1,
        "requested": {
            "entity_type": "LINE",
            "layer": "0",
            "start": [0.0, 0.0, 0.0],
            "end": [1234.5, 678.9, 0.0],
        },
        "actual": {
            "entity_type": "LINE",
            "layer": "0",
            "start": [0.0, 0.0, 0.0],
            "end": [1234.5, 678.9000000000001, 0.0],
        },
    }
    steps = [
        _step("env.detect"),
        _step("doctor"),
        _step("autocad.install", product_version="26.0.0.0"),
        _step("plugin.build"),
        _step("autocad.process"),
        _step("worker.descriptor"),
    ]
    if mode in {"read-only", "write"}:
        steps += [_step("pipe.ping"), _step("pipe.capabilities"), _step("pipe.context")]
    if mode == "write":
        steps += [
            _step("com.attach"),
            _step("temp.create"),
            _step("guard.active_is_temp"),
            _step("pipe.draw_line", handle="1F75", delta_entities=1),
            _step("com.readback", **readback),
            _step("temp.save_digest"),
            _step("temp.cleanup", closed_temp_drawing=True),
        ]
    payload: dict[str, Any] = {
        "schema": "all-in-cad/live-smoke/v1",
        "run_id": f"20261107T100000Z-{mode}",
        "captured_at": "2026-11-07T10:00:05.0000000Z",
        "mode": mode,
        "host": "autocad2027",
        "machine": {"computer": "WIN-HOST", "runner": None, "workflow_run": None},
        "git_commit": "0" * 40,
        "versions": {"autocad": "26.0.0.0", "powershell": "5.1.19041.5486"},
        "steps": steps,
        "summary": {"passed": len(steps), "failed": 0, "skipped": 0, "ok": True},
    }
    payload.update(overrides)
    return payload


def _set_status(payload: dict[str, Any], step_id: str, status: str) -> dict[str, Any]:
    payload = copy.deepcopy(payload)
    for step in payload["steps"]:
        if step["id"] == step_id:
            step["status"] = status
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for step in payload["steps"]:
        counts[step["status"]] += 1
    payload["summary"] = {**counts, "ok": counts["failed"] == 0}
    return payload


def _by_key(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["key"]: item for item in results}


def test_write_run_passes_every_autocad_ledger_item() -> None:
    evidence = LiveSmokeEvidence.model_validate(_evidence())
    results = _by_key(evaluate(evidence))
    assert set(results) == {item.key for item in LEDGER_ITEMS}
    assert all(item["status"] == "passed" for item in results.values()), results
    verification = results["autocad2027.native.readback_crosscheck"]["verification"]
    assert verification["passed"] is True
    assert verification["sources"] == ["native-pipe-requested", "com-readback"]


def test_dry_run_only_speaks_to_detection() -> None:
    evidence = LiveSmokeEvidence.model_validate(_evidence("dry-run"))
    results = evaluate(evidence)
    assert [item["key"] for item in results] == ["autocad2027.toolchain.detected"]
    ledger, _ = record(empty_ledger(), evidence, SHA)
    assert promotable(ledger) == []


def test_read_only_run_never_promotes_write_items() -> None:
    evidence = LiveSmokeEvidence.model_validate(_evidence("read-only"))
    keys = {item["key"] for item in evaluate(evidence)}
    assert "autocad2027.native.write_transaction" not in keys
    ledger, _ = record(empty_ledger(), evidence, SHA)
    assert promotable(ledger) == [
        "AutoCAD 2027: load All-In-Cad .NET 10 plugin and pass Named Pipe ping/context tests."
    ]


def test_failed_readback_step_fails_the_item() -> None:
    payload = _set_status(_evidence(), "com.readback", "failed")
    evidence = LiveSmokeEvidence.model_validate(payload)
    results = _by_key(evaluate(evidence))
    item = results["autocad2027.native.readback_crosscheck"]
    assert item["status"] == "failed"
    assert "step com.readback did not pass" in item["reasons"]
    assert results["autocad2027.native.write_transaction"]["status"] == "passed"


def test_geometry_mismatch_fails_cross_check_even_if_host_step_passed() -> None:
    payload = _evidence()
    readback = next(step for step in payload["steps"] if step["id"] == "com.readback")
    readback["detail"]["actual"]["end"] = [1234.5, 600.0, 0.0]
    evidence = LiveSmokeEvidence.model_validate(payload)
    item = _by_key(evaluate(evidence))["autocad2027.native.readback_crosscheck"]
    assert item["status"] == "failed"
    codes = {finding["code"] for finding in item["verification"]["findings"]}
    assert "GEOMETRY_DIGEST_MISMATCH" in codes


def test_entity_count_mismatch_fails_cross_check() -> None:
    payload = _evidence()
    readback = next(step for step in payload["steps"] if step["id"] == "com.readback")
    readback["detail"]["com_modelspace_count"] = 2
    item = _by_key(evaluate(LiveSmokeEvidence.model_validate(payload)))[
        "autocad2027.native.readback_crosscheck"
    ]
    assert item["status"] == "failed"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(schema="all-in-cad/live-smoke/v0"),
        lambda p: p.update(mode="live"),
        lambda p: p["summary"].update(passed=1),
        lambda p: p["summary"].update(ok=False),
        lambda p: p["steps"].append(copy.deepcopy(p["steps"][0])),
        lambda p: p["steps"][0]["outputs"][0].update(sha256="xyz"),
        lambda p: p.update(unexpected=True),
    ],
)
def test_schema_rejects_malformed_evidence(mutate: Any) -> None:
    payload = _evidence()
    mutate(payload)
    with pytest.raises(ValidationError):
        LiveSmokeEvidence.model_validate(payload)


def test_powershell_json_quirks_are_accepted() -> None:
    payload = _evidence("dry-run")
    payload["steps"][0]["outputs"] = {"path": "one.json", "sha256": SHA}
    payload["steps"][1]["detail"] = None
    payload["steps"][1]["outputs"] = None
    evidence = LiveSmokeEvidence.model_validate_json(json.dumps(payload))
    assert len(evidence.steps[0].outputs) == 1
    assert evidence.steps[1].detail == {}


def test_record_is_idempotent_per_run() -> None:
    evidence = LiveSmokeEvidence.model_validate(_evidence())
    ledger, added = record(empty_ledger(), evidence, SHA)
    again, added_again = record(ledger, evidence, SHA)
    assert len(added) == len(LEDGER_ITEMS)
    assert added_again == []
    assert again == ledger


def test_latest_failure_blocks_promotion() -> None:
    good = LiveSmokeEvidence.model_validate(_evidence())
    bad_payload = _set_status(_evidence(), "pipe.draw_line", "failed")
    bad_payload.update(run_id="later", captured_at="2026-11-08T10:00:00Z")
    bad = LiveSmokeEvidence.model_validate(bad_payload)
    ledger, _ = record(empty_ledger(), good, SHA)
    ledger, _ = record(ledger, bad, SHA)
    lines = promotable(ledger)
    assert not any("commit a v1 LINE" in line for line in lines)


def test_roadmap_promotion_lines_exist_verbatim() -> None:
    roadmap = (ROOT / "docs" / "ROADMAP.md").read_text(encoding="utf-8")
    for line in ROADMAP_PROMOTIONS:
        assert re.search(r"^- \[[ x]\] " + re.escape(line) + "$", roadmap, re.MULTILINE), line
    known = {item.key for item in LEDGER_ITEMS}
    for keys in ROADMAP_PROMOTIONS.values():
        assert set(keys) <= known


def test_apply_roadmap_only_ticks() -> None:
    line = next(iter(ROADMAP_PROMOTIONS))
    text = f"- [ ] {line}\n- [ ] other\n"
    ticked_text, ticked = apply_roadmap(text, [line])
    assert ticked_text == f"- [x] {line}\n- [ ] other\n"
    assert ticked == [line]
    assert apply_roadmap(ticked_text, [line]) == (ticked_text, [])


def test_cli_ingest_is_read_only_until_apply(tmp_path: Path) -> None:
    evidence_path = tmp_path / "live-smoke.json"
    evidence_path.write_text("﻿" + json.dumps(_evidence()), encoding="utf-8")
    ledger = tmp_path / "ledger.json"
    roadmap = tmp_path / "ROADMAP.md"
    original = "".join(f"- [ ] {line}\n" for line in ROADMAP_PROMOTIONS)
    roadmap.write_text(original, encoding="utf-8")
    args = ["live-smoke", "ingest", str(evidence_path), "--ledger", str(ledger)]
    args += ["--roadmap", str(roadmap)]

    assert main(args) == 0
    assert not ledger.exists()
    assert roadmap.read_text(encoding="utf-8") == original

    assert main([*args, "--apply"]) == 0
    stored = json.loads(ledger.read_text(encoding="utf-8"))
    assert stored["schema"] == "all-in-cad/verification-ledger/v1"
    assert {entry["status"] for entry in stored["entries"]} == {"passed"}
    assert "- [ ]" not in roadmap.read_text(encoding="utf-8")


def test_cli_ingest_rejects_invalid_evidence(tmp_path: Path) -> None:
    evidence_path = tmp_path / "live-smoke.json"
    evidence_path.write_text(json.dumps({"schema": "nope"}), encoding="utf-8")
    assert main(["live-smoke", "ingest", str(evidence_path)]) == 2


def test_committed_ledger_is_valid() -> None:
    ledger = json.loads((ROOT / "docs" / "verification-ledger.json").read_text(encoding="utf-8"))
    assert ledger["schema"] == "all-in-cad/verification-ledger/v1"
    assert isinstance(ledger["entries"], list)


def test_script_emits_every_step_the_ledger_needs() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    emitted = set(re.findall(r'Invoke-Step "([^"]+)"', script))
    for item in LEDGER_ITEMS:
        assert set(item.required_steps) <= emitted, item.key
    assert 'schema = "all-in-cad/live-smoke/v1"' in script
    assert "artifacts\\live-smoke" in script
    for switch in ("[switch]$DryRun", "[switch]$Write"):
        assert switch in script


def test_script_only_writes_the_temp_drawing() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    # Every Save/SaveAs/Close targets the temp document object, never ActiveDocument.
    for call in re.findall(r"\$[\w:]+\.(?:SaveAs|Save|Close)\(", script):
        assert call.startswith("$script:tempDoc."), call
    assert "guard.active_is_temp" in script
    assert script.index('Invoke-Step "guard.active_is_temp"') < script.index(
        'Invoke-Step "pipe.draw_line"'
    )
    # host.draw_line only runs behind the temp-drawing guard.
    draw = script[script.index('Invoke-Step "pipe.draw_line"') :]
    assert draw.index('$script:state["guardOk"]') < draw.index('"host.draw_line"')
    assert "host.send_command" not in script


def test_workflow_is_manual_and_targets_the_self_hosted_runner() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    on_block = workflow.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "workflow_dispatch:" in on_block
    for trigger in ("push:", "pull_request:", "schedule:"):
        assert trigger not in on_block
    assert "runs-on: [self-hosted, Windows, X64]" in workflow
    assert "contents: read" in workflow
    assert "upload-artifact" in workflow
    assert "${{ inputs.process_id }}" not in workflow.split("run: |", 1)[1]
