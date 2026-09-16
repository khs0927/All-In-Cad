from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .inventory import FileKind, FileRecord


class ExtractionLane(StrEnum):
    ACADSHARP = "acadsharp"
    ODA = "oda"
    EZDXF = "ezdxf"
    LIBREDWG = "libredwg"


@dataclass(frozen=True, slots=True)
class ToolProbe:
    lane: ExtractionLane
    available: bool
    executable: str | None = None
    version: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractionStep:
    lane: ExtractionLane
    action: str
    input_format: FileKind
    output_format: FileKind | str
    required: bool = True


@dataclass(frozen=True, slots=True)
class ExtractionPlan:
    record: FileRecord
    steps: tuple[ExtractionStep, ...]
    missing_tools: tuple[ExtractionLane, ...] = ()

    @property
    def executable(self) -> bool:
        return not self.missing_tools and bool(self.steps)


@dataclass(frozen=True, slots=True)
class ExtractionAttempt:
    lane: ExtractionLane
    succeeded: bool
    message: str
    output_path: str | None = None


def plan_extraction(
    record: FileRecord,
    probes: dict[ExtractionLane, ToolProbe],
) -> ExtractionPlan:
    if record.kind == FileKind.PDF:
        return ExtractionPlan(record=record, steps=())

    if record.kind == FileKind.DXF:
        if _available(probes, ExtractionLane.EZDXF):
            return ExtractionPlan(
                record=record,
                steps=(
                    ExtractionStep(
                        lane=ExtractionLane.EZDXF,
                        action="parse-normalized-ir",
                        input_format=FileKind.DXF,
                        output_format="semantic-ir",
                    ),
                ),
            )
        return ExtractionPlan(record=record, steps=(), missing_tools=(ExtractionLane.EZDXF,))

    if _available(probes, ExtractionLane.ACADSHARP):
        return ExtractionPlan(
            record=record,
            steps=(
                ExtractionStep(
                    lane=ExtractionLane.ACADSHARP,
                    action="parse-dwg-direct",
                    input_format=FileKind.DWG,
                    output_format="semantic-ir",
                ),
            ),
        )

    converter: ExtractionLane | None = None
    if _available(probes, ExtractionLane.ODA):
        converter = ExtractionLane.ODA
    elif _available(probes, ExtractionLane.LIBREDWG):
        converter = ExtractionLane.LIBREDWG

    if converter is None:
        return ExtractionPlan(
            record=record,
            steps=(),
            missing_tools=(
                ExtractionLane.ACADSHARP,
                ExtractionLane.ODA,
                ExtractionLane.LIBREDWG,
            ),
        )

    if not _available(probes, ExtractionLane.EZDXF):
        return ExtractionPlan(
            record=record,
            steps=(),
            missing_tools=(ExtractionLane.EZDXF,),
        )

    return ExtractionPlan(
        record=record,
        steps=(
            ExtractionStep(
                lane=converter,
                action="convert-dwg-to-dxf",
                input_format=FileKind.DWG,
                output_format=FileKind.DXF,
            ),
            ExtractionStep(
                lane=ExtractionLane.EZDXF,
                action="parse-normalized-ir",
                input_format=FileKind.DXF,
                output_format="semantic-ir",
            ),
        ),
    )


def default_probe_paths() -> dict[ExtractionLane, tuple[str, ...]]:
    return {
        ExtractionLane.ACADSHARP: ("dotnet",),
        ExtractionLane.ODA: ("ODAFileConverter.exe",),
        ExtractionLane.EZDXF: ("python", "ezdxf"),
        ExtractionLane.LIBREDWG: ("dwg2dxf",),
    }


def record_for_path(path: str | Path) -> FileRecord:
    resolved = Path(path).expanduser().resolve()
    suffix = resolved.suffix.lower()
    try:
        kind = FileKind(suffix.removeprefix("."))
    except ValueError as exc:
        raise ValueError(f"unsupported extraction input: {resolved}") from exc
    stat = resolved.stat()
    return FileRecord(
        root=str(resolved.parent),
        path=str(resolved),
        relative_path=resolved.name,
        kind=kind,
        size_bytes=stat.st_size,
        modified_ns=stat.st_mtime_ns,
        sidecar_group=resolved.stem.lower(),
    )


def _available(probes: dict[ExtractionLane, ToolProbe], lane: ExtractionLane) -> bool:
    probe = probes.get(lane)
    return bool(probe and probe.available)
