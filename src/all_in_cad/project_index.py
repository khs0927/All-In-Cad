from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .inventory import FileRecord, InventoryManifest
from .readback import EntitySnapshot


@dataclass(frozen=True, slots=True)
class IndexResult:
    path: str
    drawing_id: int
    changed: bool
    entity_count: int
    reference_count: int


@dataclass(frozen=True, slots=True)
class ReferenceRecord:
    drawing_path: str
    source_handle: str
    kind: str
    name: str | None
    target_path: str | None


class ProjectIndex:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> ProjectIndex:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def index_drawing(
        self,
        record: FileRecord,
        entities: Iterable[EntitySnapshot],
    ) -> IndexResult:
        fingerprint = file_fingerprint(record)
        existing = self.connection.execute(
            "SELECT id, fingerprint FROM drawings WHERE path = ?",
            (record.path,),
        ).fetchone()
        if existing is not None and existing["fingerprint"] == fingerprint:
            drawing_id = int(existing["id"])
            entity_count = self._count("entities", drawing_id)
            reference_count = self._count("refs", drawing_id)
            return IndexResult(
                record.path,
                drawing_id,
                False,
                entity_count,
                reference_count,
            )

        snapshots = list(entities)
        references = _extract_references(snapshots)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO drawings(
                    path, root, relative_path, kind, fingerprint,
                    size_bytes, modified_ns, sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    root = excluded.root,
                    relative_path = excluded.relative_path,
                    kind = excluded.kind,
                    fingerprint = excluded.fingerprint,
                    size_bytes = excluded.size_bytes,
                    modified_ns = excluded.modified_ns,
                    sha256 = excluded.sha256,
                    indexed_at = CURRENT_TIMESTAMP
                """,
                (
                    record.path,
                    record.root,
                    record.relative_path,
                    record.kind.value,
                    fingerprint,
                    record.size_bytes,
                    record.modified_ns,
                    record.sha256,
                ),
            )
            drawing_id = int(
                self.connection.execute(
                    "SELECT id FROM drawings WHERE path = ?",
                    (record.path,),
                ).fetchone()["id"]
            )
            self.connection.execute("DELETE FROM entities WHERE drawing_id = ?", (drawing_id,))
            self.connection.execute("DELETE FROM refs WHERE drawing_id = ?", (drawing_id,))
            self.connection.executemany(
                """
                INSERT INTO entities(
                    drawing_id, handle, entity_type, layer, digest,
                    geometry_json, properties_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        drawing_id,
                        snapshot.handle.upper(),
                        snapshot.entity_type,
                        snapshot.layer,
                        snapshot.digest(),
                        _json(snapshot.geometry),
                        _json(snapshot.properties),
                    )
                    for snapshot in snapshots
                ],
            )
            self.connection.executemany(
                """
                INSERT INTO refs(
                    drawing_id, source_handle, kind, name, target_path
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        drawing_id,
                        item.source_handle,
                        item.kind,
                        item.name,
                        item.target_path,
                    )
                    for item in references
                ],
            )

        return IndexResult(
            record.path,
            drawing_id,
            True,
            len(snapshots),
            len(references),
        )

    def changed_records(self, manifest: InventoryManifest) -> list[FileRecord]:
        changed: list[FileRecord] = []
        for record in manifest.records:
            row = self.connection.execute(
                "SELECT fingerprint FROM drawings WHERE path = ?",
                (record.path,),
            ).fetchone()
            if row is None or row["fingerprint"] != file_fingerprint(record):
                changed.append(record)
        return changed

    def block_usages(self, name: str) -> list[ReferenceRecord]:
        return self._reference_query("kind = 'block' AND lower(name) = lower(?)", (name,))

    def xrefs(self, target_path: str | None = None) -> list[ReferenceRecord]:
        if target_path is None:
            return self._reference_query("kind = 'xref'", ())
        return self._reference_query(
            "kind = 'xref' AND lower(target_path) = lower(?)",
            (target_path,),
        )

    def drawing_count(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS count FROM drawings").fetchone()
        return int(row["count"])

    def _reference_query(
        self,
        where: str,
        parameters: tuple[object, ...],
    ) -> list[ReferenceRecord]:
        rows = self.connection.execute(
            f"""
            SELECT d.path AS drawing_path, r.source_handle, r.kind, r.name, r.target_path
            FROM refs r
            JOIN drawings d ON d.id = r.drawing_id
            WHERE {where}
            ORDER BY lower(d.path), r.source_handle
            """,
            parameters,
        ).fetchall()
        return [ReferenceRecord(**dict(row)) for row in rows]

    def _count(self, table: str, drawing_id: int) -> int:
        if table not in {"entities", "refs"}:
            raise ValueError("unsupported table")
        row = self.connection.execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE drawing_id = ?",
            (drawing_id,),
        ).fetchone()
        return int(row["count"])

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS drawings(
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL UNIQUE,
                root TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                kind TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                modified_ns INTEGER NOT NULL,
                sha256 TEXT,
                indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS entities(
                drawing_id INTEGER NOT NULL REFERENCES drawings(id) ON DELETE CASCADE,
                handle TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                layer TEXT NOT NULL,
                digest TEXT NOT NULL,
                geometry_json TEXT NOT NULL,
                properties_json TEXT NOT NULL,
                PRIMARY KEY(drawing_id, handle)
            );
            CREATE TABLE IF NOT EXISTS refs(
                id INTEGER PRIMARY KEY,
                drawing_id INTEGER NOT NULL REFERENCES drawings(id) ON DELETE CASCADE,
                source_handle TEXT NOT NULL,
                kind TEXT NOT NULL,
                name TEXT,
                target_path TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_entities_layer ON entities(layer);
            CREATE INDEX IF NOT EXISTS idx_refs_kind_name ON refs(kind, name);
            CREATE INDEX IF NOT EXISTS idx_refs_target_path ON refs(target_path);
            """
        )


def file_fingerprint(record: FileRecord) -> str:
    if record.sha256:
        return f"sha256:{record.sha256.lower()}"
    payload = (
        f"{record.kind.value}|{record.size_bytes}|{record.modified_ns}|"
        f"{record.relative_path.lower()}"
    )
    return "stat:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _extract_references(entities: list[EntitySnapshot]) -> list[ReferenceRecord]:
    results: list[ReferenceRecord] = []
    for entity in entities:
        properties = entity.properties
        entity_type = entity.entity_type.upper()
        block_name = _string(properties.get("block_name"))
        xref_path = _string(properties.get("xref_path")) or _string(
            properties.get("external_reference")
        )
        if xref_path:
            results.append(
                ReferenceRecord(
                    drawing_path="",
                    source_handle=entity.handle.upper(),
                    kind="xref",
                    name=block_name or _string(properties.get("xref_name")),
                    target_path=xref_path,
                )
            )
        elif block_name or entity_type in {"INSERT", "BLOCKREFERENCE"}:
            results.append(
                ReferenceRecord(
                    drawing_path="",
                    source_handle=entity.handle.upper(),
                    kind="block",
                    name=block_name,
                    target_path=None,
                )
            )
    return results


def _string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
