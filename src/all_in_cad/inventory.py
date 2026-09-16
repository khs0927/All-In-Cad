from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class FileKind(StrEnum):
    DWG = "dwg"
    DXF = "dxf"
    PDF = "pdf"


_SUFFIXES = {f".{item.value}": item for item in FileKind}


class FileRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: str
    path: str
    relative_path: str
    kind: FileKind
    size_bytes: int = Field(ge=0)
    modified_ns: int = Field(ge=0)
    sha256: str | None = None
    sidecar_group: str


class InventoryManifest(BaseModel):
    roots: list[str]
    records: list[FileRecord]

    @property
    def dwg_count(self) -> int:
        return sum(record.kind == FileKind.DWG for record in self.records)


def build_inventory(
    roots: list[str | Path],
    *,
    compute_hash: bool = False,
) -> InventoryManifest:
    normalized_roots = [Path(root).expanduser().resolve() for root in roots]
    records: list[FileRecord] = []
    for root in normalized_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            kind = _SUFFIXES.get(path.suffix.lower())
            if kind is None:
                continue
            stat = path.stat()
            relative = path.relative_to(root)
            records.append(
                FileRecord(
                    root=str(root),
                    path=str(path),
                    relative_path=str(relative),
                    kind=kind,
                    size_bytes=stat.st_size,
                    modified_ns=stat.st_mtime_ns,
                    sha256=_sha256(path) if compute_hash else None,
                    sidecar_group=str(relative.with_suffix("")).lower(),
                )
            )
    records.sort(key=lambda item: (item.root.lower(), item.relative_path.lower(), item.kind.value))
    return InventoryManifest(roots=[str(root) for root in normalized_roots], records=records)


def group_sidecars(manifest: InventoryManifest) -> dict[str, list[FileRecord]]:
    groups: dict[str, list[FileRecord]] = {}
    for record in manifest.records:
        groups.setdefault(record.sidecar_group, []).append(record)
    return groups


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
