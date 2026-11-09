"""Live-host smoke evidence → verification ledger → ROADMAP promotion.

``scripts/windows-live-smoke.ps1`` runs on the Windows CAD host and writes one
``all-in-cad/live-smoke/v1`` JSON file per run. This module is the headless half:
it validates that file, turns passed steps into ledger items, re-derives the
requested/actual readback comparison with the same :func:`cross_check_evidence`
primitive the extraction lanes use, and decides which ROADMAP checkboxes the
ledger now proves.

Nothing here talks to CAD. A ROADMAP item is only promoted when the *latest*
ledger entry of every item it requires passed, and promotion only ever ticks a
box; a later regression is reported, never silently unticked.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .evidence import handle_independent_geometry_digest
from .models import VerificationReport
from .readback import EntitySnapshot
from .verification import DrawingEvidence, cross_check_evidence

EVIDENCE_SCHEMA = "all-in-cad/live-smoke/v1"
LEDGER_SCHEMA = "all-in-cad/verification-ledger/v1"

StepStatus = Literal["passed", "failed", "skipped"]
Mode = Literal["dry-run", "read-only", "write"]
_MODE_RANK: dict[str, int] = {"dry-run": 0, "read-only": 1, "write": 2}


class OutputDigest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LiveSmokeStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    status: StepStatus
    started_at: str
    duration_ms: int = Field(ge=0)
    detail: dict[str, Any] = Field(default_factory=dict)
    outputs: tuple[OutputDigest, ...] = ()

    @field_validator("detail", mode="before")
    @classmethod
    def _detail_default(cls, value: Any) -> Any:
        # PowerShell's ConvertTo-Json writes an empty ordered dictionary as null on 5.1.
        return {} if value is None else value

    @field_validator("outputs", mode="before")
    @classmethod
    def _outputs_default(cls, value: Any) -> Any:
        if value is None:
            return ()
        # ConvertTo-Json unrolls a one-element array into a bare object.
        return (value,) if isinstance(value, Mapping) else value


class LiveSmokeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    ok: bool


class LiveSmokeEvidence(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )

    schema_id: Literal["all-in-cad/live-smoke/v1"] = Field(alias="schema")
    run_id: str = Field(min_length=1)
    captured_at: str = Field(min_length=1)
    mode: Mode
    host: Literal["autocad2027", "zwcad2026"]
    machine: dict[str, Any] = Field(default_factory=dict)
    git_commit: str | None = None
    versions: dict[str, Any] = Field(default_factory=dict)
    steps: tuple[LiveSmokeStep, ...] = Field(min_length=1)
    summary: LiveSmokeSummary

    @field_validator("steps", mode="before")
    @classmethod
    def _steps_list(cls, value: Any) -> Any:
        return (value,) if isinstance(value, Mapping) else value

    @model_validator(mode="after")
    def _consistent(self) -> LiveSmokeEvidence:
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step ids must be unique")
        counts = {status: 0 for status in ("passed", "failed", "skipped")}
        for step in self.steps:
            counts[step.status] += 1
        actual = (counts["passed"], counts["failed"], counts["skipped"])
        declared = (self.summary.passed, self.summary.failed, self.summary.skipped)
        if actual != declared:
            raise ValueError(f"summary counts {declared} do not match steps {actual}")
        if self.summary.ok != (counts["failed"] == 0):
            raise ValueError("summary.ok must be true exactly when no step failed")
        return self

    def step(self, step_id: str) -> LiveSmokeStep | None:
        return next((item for item in self.steps if item.id == step_id), None)

    def passed(self, step_id: str) -> bool:
        found = self.step(step_id)
        return found is not None and found.status == "passed"


# ---------------------------------------------------------------- ledger items


Check = Callable[[LiveSmokeEvidence], VerificationReport | None]


@dataclass(frozen=True)
class LedgerItem:
    key: str
    host: str
    description: str
    required_steps: tuple[str, ...]
    minimum_mode: Mode
    check: Check | None = None


def readback_verification(evidence: LiveSmokeEvidence) -> VerificationReport | None:
    """Cross-check the requested LINE (pipe) against the COM readback.

    Both channels become ordinary :class:`DrawingEvidence` so the result is the
    same :class:`VerificationReport` the extraction lanes produce.
    """
    step = evidence.step("com.readback")
    if step is None or step.status != "passed":
        return None
    detail = step.detail
    try:
        requested = detail["requested"]
        actual = detail["actual"]
        handle = str(detail["handle"])
        pipe_count = int(detail["pipe_entity_count_after"])
        com_count = int(detail["com_modelspace_count"])
    except (KeyError, TypeError, ValueError):
        return None
    document_id = f"live-smoke:{evidence.run_id}"
    sides = (
        ("native-pipe-requested", requested, pipe_count),
        ("com-readback", actual, com_count),
    )
    evidences = []
    for source, payload, count in sides:
        snapshot = EntitySnapshot(
            document_id=document_id,
            handle=handle,
            entity_type=str(payload.get("entity_type", "")) or "UNKNOWN",
            layer=str(payload.get("layer", "")) or "<missing>",
            geometry={"start": payload.get("start"), "end": payload.get("end")},
        )
        evidences.append(
            DrawingEvidence(
                source=source,
                document_id=document_id,
                revision=0,
                entity_count=count,
                layer_counts={snapshot.layer: 1},
                geometry_digest=handle_independent_geometry_digest([snapshot]),
            )
        )
    return cross_check_evidence(evidences)


LEDGER_ITEMS: tuple[LedgerItem, ...] = (
    LedgerItem(
        key="autocad2027.toolchain.detected",
        host="autocad2027",
        description="AutoCAD 2027 install and local toolchain detected on the Windows host",
        required_steps=("env.detect", "doctor", "autocad.install"),
        minimum_mode="dry-run",
    ),
    LedgerItem(
        key="autocad2027.pipe.read_only",
        host="autocad2027",
        description="Named Pipe ping, capabilities and context against the live worker",
        required_steps=("pipe.ping", "pipe.capabilities", "pipe.context"),
        minimum_mode="read-only",
    ),
    LedgerItem(
        key="autocad2027.native.write_transaction",
        host="autocad2027",
        description="host.draw_line committed one LINE in a native transaction on a %TEMP% drawing",
        required_steps=("guard.active_is_temp", "pipe.draw_line", "temp.cleanup"),
        minimum_mode="write",
    ),
    LedgerItem(
        key="autocad2027.native.readback_crosscheck",
        host="autocad2027",
        description="post-commit LINE read back through COM and cross-checked against the request",
        required_steps=("pipe.draw_line", "com.readback"),
        minimum_mode="write",
        check=readback_verification,
    ),
    LedgerItem(
        key="autocad2027.native.approval_gate",
        host="autocad2027",
        description="live worker refused an untokened host.draw_line and a re-sent approval "
        "token, and drew only with a single-use token from host.plan_write",
        required_steps=(
            "pipe.approval_gate",
            "pipe.approval_request",
            "pipe.draw_line",
            "pipe.approval_reuse",
        ),
        minimum_mode="write",
    ),
)

# ROADMAP line text (without the "- [ ] " prefix) → ledger keys that must all be passing.
ROADMAP_PROMOTIONS: dict[str, tuple[str, ...]] = {
    "AutoCAD 2027: load All-In-Cad .NET 10 plugin and pass Named Pipe ping/context tests.": (
        "autocad2027.pipe.read_only",
    ),
    "AutoCAD 2027: commit a v1 LINE primitive in a native transaction on a %TEMP% drawing "
    "(`scripts/windows-live-smoke.ps1 -Write`).": ("autocad2027.native.write_transaction",),
    "AutoCAD 2027: read back the committed LINE through COM and cross-check it against the "
    "request (`scripts/windows-live-smoke.ps1 -Write`).": (
        "autocad2027.native.readback_crosscheck",
    ),
    "AutoCAD 2027: the live worker refuses untokened and re-used pipe writes "
    "(write approval gate, `scripts/windows-live-smoke.ps1 -Write`).": (
        "autocad2027.native.approval_gate",
    ),
}


def evaluate(evidence: LiveSmokeEvidence) -> list[dict[str, Any]]:
    """Return one result per ledger item that this evidence's host and mode can speak to."""
    results: list[dict[str, Any]] = []
    for item in LEDGER_ITEMS:
        if item.host != evidence.host:
            continue
        if _MODE_RANK[evidence.mode] < _MODE_RANK[item.minimum_mode]:
            continue
        missing = [step for step in item.required_steps if not evidence.passed(step)]
        verification = item.check(evidence) if item.check else None
        reasons = [f"step {step} did not pass" for step in missing]
        if item.check is not None:
            if verification is None:
                reasons.append("verification could not be derived from the evidence")
            elif not verification.passed:
                reasons.append("verification cross-check failed")
        result: dict[str, Any] = {
            "key": item.key,
            "status": "failed" if reasons else "passed",
            "reasons": reasons,
        }
        if verification is not None:
            result["verification"] = verification.model_dump(mode="json")
        results.append(result)
    return results


# ---------------------------------------------------------------- ledger file


def empty_ledger() -> dict[str, Any]:
    return {"schema": LEDGER_SCHEMA, "entries": []}


def load_ledger(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return empty_ledger()
    ledger = json.loads(path.read_text(encoding="utf-8"))
    if ledger.get("schema") != LEDGER_SCHEMA or not isinstance(ledger.get("entries"), list):
        raise ValueError(f"{path} is not an {LEDGER_SCHEMA} file")
    return ledger


def record(
    ledger: Mapping[str, Any],
    evidence: LiveSmokeEvidence,
    evidence_sha256: str,
    *,
    evidence_path: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Append this run's ledger results. Re-recording the same run is a no-op."""
    entries = [dict(entry) for entry in ledger.get("entries", [])]
    seen = {(entry.get("key"), entry.get("run_id")) for entry in entries}
    added: list[dict[str, Any]] = []
    for result in evaluate(evidence):
        if (result["key"], evidence.run_id) in seen:
            continue
        entry = {
            **result,
            "run_id": evidence.run_id,
            "captured_at": evidence.captured_at,
            "mode": evidence.mode,
            "host": evidence.host,
            "git_commit": evidence.git_commit,
            "versions": dict(evidence.versions),
            "evidence_sha256": evidence_sha256,
        }
        if evidence_path:
            entry["evidence_path"] = evidence_path
        entries.append(entry)
        added.append(entry)
    entries.sort(key=lambda entry: (str(entry.get("captured_at")), str(entry.get("key"))))
    return {"schema": LEDGER_SCHEMA, "entries": entries}, added


def latest_status(entries: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    latest: dict[str, tuple[str, str]] = {}
    for entry in entries:
        key = str(entry.get("key"))
        stamp = str(entry.get("captured_at"))
        if key not in latest or stamp >= latest[key][0]:
            latest[key] = (stamp, str(entry.get("status")))
    return {key: status for key, (_, status) in latest.items()}


def promotable(ledger: Mapping[str, Any]) -> list[str]:
    status = latest_status(ledger.get("entries", []))
    return [
        line
        for line, keys in ROADMAP_PROMOTIONS.items()
        if all(status.get(key) == "passed" for key in keys)
    ]


def apply_roadmap(text: str, lines: Iterable[str]) -> tuple[str, list[str]]:
    """Tick ``- [ ] <line>`` for each line. Returns the new text and the lines ticked."""
    ticked: list[str] = []
    for line in lines:
        pattern = re.compile(r"^- \[ \] " + re.escape(line) + r"$", re.MULTILINE)
        # A literal replacement string would interpret backslashes; escape them.
        replacement = ("- [x] " + line).replace("\\", "\\\\")
        text, count = pattern.subn(replacement, text)
        if count:
            ticked.append(line)
    return text, ticked


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_evidence(path: Path) -> LiveSmokeEvidence:
    # utf-8-sig: Windows PowerShell may prepend a BOM.
    return LiveSmokeEvidence.model_validate_json(path.read_text(encoding="utf-8-sig"))


def ingest(
    evidence_path: Path,
    *,
    ledger_path: Path,
    roadmap_path: Path,
    apply: bool = False,
) -> dict[str, Any]:
    evidence = load_evidence(evidence_path)
    digest = sha256_file(evidence_path)
    ledger, added = record(
        load_ledger(ledger_path),
        evidence,
        digest,
        evidence_path=evidence_path.as_posix(),
    )
    lines = promotable(ledger)
    roadmap = roadmap_path.read_text(encoding="utf-8")
    new_roadmap, ticked = apply_roadmap(roadmap, lines)
    regressions = [
        line
        for line, keys in ROADMAP_PROMOTIONS.items()
        if f"- [x] {line}" in roadmap
        and any(latest_status(ledger["entries"]).get(key) == "failed" for key in keys)
    ]
    if apply:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if new_roadmap != roadmap:
            roadmap_path.write_text(new_roadmap, encoding="utf-8")
    return {
        "run_id": evidence.run_id,
        "mode": evidence.mode,
        "evidence_sha256": digest,
        "summary": evidence.summary.model_dump(),
        "ledger_entries_added": [
            {"key": entry["key"], "status": entry["status"], "reasons": entry["reasons"]}
            for entry in added
        ],
        "roadmap_ticked": ticked,
        "roadmap_regressions": regressions,
        "applied": apply,
    }
