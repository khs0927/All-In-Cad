from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DxfEntityEvidence:
    handle: str | None
    dxftype: str
    layer: str


@dataclass(frozen=True, slots=True)
class DxfEvidence:
    path: str
    entity_count: int
    layer_counts: dict[str, int]
    entities: tuple[DxfEntityEvidence, ...]
    auditor_has_errors: bool


def inspect_dxf(path: str | Path) -> DxfEvidence:
    """Read, recover/audit, and census a DXF without trusting the producer."""
    try:
        from ezdxf import recover
    except ImportError as exc:  # pragma: no cover - packaging/configuration error
        raise RuntimeError("install All-In-Cad with the 'headless' extra") from exc

    source = Path(path)
    document, auditor = recover.readfile(source)
    entities: list[DxfEntityEvidence] = []
    layers: Counter[str] = Counter()

    for entity in document.modelspace():
        layer = str(getattr(entity.dxf, "layer", "0"))
        handle_value = getattr(entity.dxf, "handle", None)
        handle = str(handle_value) if handle_value is not None else None
        evidence = DxfEntityEvidence(
            handle=handle,
            dxftype=entity.dxftype(),
            layer=layer,
        )
        entities.append(evidence)
        layers[layer] += 1

    return DxfEvidence(
        path=str(source),
        entity_count=len(entities),
        layer_counts=dict(sorted(layers.items())),
        entities=tuple(entities),
        auditor_has_errors=bool(auditor.has_errors),
    )
