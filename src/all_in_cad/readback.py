from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DiffKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    UNCHANGED = "unchanged"


class EntitySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1)
    handle: str = Field(min_length=1)
    entity_type: str = Field(min_length=1)
    layer: str = Field(min_length=1)
    geometry: dict[str, Any] = Field(default_factory=dict)
    properties: dict[str, Any] = Field(default_factory=dict)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "handle": self.handle.upper(),
            "entity_type": self.entity_type.upper(),
            "layer": self.layer,
            "geometry": _canonicalize(self.geometry),
            "properties": _canonicalize(self.properties),
        }

    def digest(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class EntityDiff(BaseModel):
    handle: str
    kind: DiffKind
    before_digest: str | None = None
    after_digest: str | None = None
    changed_fields: list[str] = Field(default_factory=list)


class SnapshotDiff(BaseModel):
    document_id: str
    before_revision: int = Field(ge=0)
    after_revision: int = Field(ge=0)
    entities: list[EntityDiff] = Field(default_factory=list)

    @property
    def changed(self) -> bool:
        return any(item.kind != DiffKind.UNCHANGED for item in self.entities)


def diff_snapshots(
    before: list[EntitySnapshot],
    after: list[EntitySnapshot],
    *,
    document_id: str,
    before_revision: int,
    after_revision: int,
) -> SnapshotDiff:
    before_map = _by_handle(before, document_id=document_id)
    after_map = _by_handle(after, document_id=document_id)
    items: list[EntityDiff] = []

    for handle in sorted(set(before_map) | set(after_map)):
        left = before_map.get(handle)
        right = after_map.get(handle)
        if left is None and right is not None:
            items.append(
                EntityDiff(
                    handle=handle,
                    kind=DiffKind.ADDED,
                    after_digest=right.digest(),
                    changed_fields=["entity"],
                )
            )
            continue
        if left is not None and right is None:
            items.append(
                EntityDiff(
                    handle=handle,
                    kind=DiffKind.REMOVED,
                    before_digest=left.digest(),
                    changed_fields=["entity"],
                )
            )
            continue
        assert left is not None and right is not None
        left_digest = left.digest()
        right_digest = right.digest()
        if left_digest == right_digest:
            items.append(
                EntityDiff(
                    handle=handle,
                    kind=DiffKind.UNCHANGED,
                    before_digest=left_digest,
                    after_digest=right_digest,
                )
            )
            continue
        changed_fields = [
            field
            for field in ("entity_type", "layer", "geometry", "properties")
            if getattr(left, field) != getattr(right, field)
        ]
        items.append(
            EntityDiff(
                handle=handle,
                kind=DiffKind.MODIFIED,
                before_digest=left_digest,
                after_digest=right_digest,
                changed_fields=changed_fields,
            )
        )

    return SnapshotDiff(
        document_id=document_id,
        before_revision=before_revision,
        after_revision=after_revision,
        entities=items,
    )


def _by_handle(
    items: list[EntitySnapshot],
    *,
    document_id: str,
) -> dict[str, EntitySnapshot]:
    result: dict[str, EntitySnapshot] = {}
    for item in items:
        if item.document_id != document_id:
            raise ValueError("snapshot entity belongs to a different document")
        handle = item.handle.upper()
        if handle in result:
            raise ValueError(f"duplicate handle in snapshot: {handle}")
        result[handle] = item
    return result


def _canonicalize(value: Any) -> Any:
    if isinstance(value, float):
        if value == 0:
            return 0.0
        return round(value, 9)
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    return value
