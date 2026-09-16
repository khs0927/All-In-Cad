from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .architecture import (
    AnnotationBinding,
    OpeningHostRelation,
    RoomAdjacency,
    RoomCandidate,
)
from .inventory import FileRecord, InventoryManifest
from .readback import EntitySnapshot
from .semantic_graph import SemanticGraph


@dataclass(frozen=True, slots=True)
class IndexResult:
    path: str
    drawing_id: int
    changed: bool
    entity_count: int
    reference_count: int


@dataclass(frozen=True, slots=True)
class SemanticIndexResult:
    drawing_id: int
    node_count: int
    edge_count: int
    room_count: int
    opening_host_count: int
    room_adjacency_count: int
    annotation_binding_count: int


@dataclass(frozen=True, slots=True)
class SemanticSummary:
    path: str
    node_count: int
    edge_count: int
    room_count: int
    opening_host_count: int
    room_adjacency_count: int
    annotation_binding_count: int
    extraction_lane: str | None
    snapshot_digest: str | None


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

    def replace_semantics(
        self,
        drawing_id: int,
        graph: SemanticGraph,
        *,
        rooms: Iterable[RoomCandidate] = (),
        opening_hosts: Iterable[OpeningHostRelation] = (),
        room_adjacencies: Iterable[RoomAdjacency] = (),
        annotation_bindings: Iterable[AnnotationBinding] = (),
        extraction_lane: str | None = None,
        snapshot_digest: str | None = None,
        warnings: Iterable[str] = (),
        intermediate_path: str | None = None,
    ) -> SemanticIndexResult:
        room_items = list(rooms)
        opening_items = list(opening_hosts)
        adjacency_items = list(room_adjacencies)
        binding_items = list(annotation_bindings)

        with self.connection:
            for table in (
                "semantic_edges",
                "semantic_nodes",
                "rooms",
                "opening_hosts",
                "room_adjacencies",
                "annotation_bindings",
            ):
                self.connection.execute(f"DELETE FROM {table} WHERE drawing_id = ?", (drawing_id,))

            self.connection.executemany(
                """
                INSERT INTO semantic_nodes(drawing_id, node_id, kind, attributes_json)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (drawing_id, node.node_id, node.kind.value, _json(node.attributes))
                    for node in graph.nodes
                ],
            )
            self.connection.executemany(
                """
                INSERT INTO semantic_edges(drawing_id, source, target, kind, attributes_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        drawing_id,
                        edge.source,
                        edge.target,
                        edge.kind.value,
                        _json(edge.attributes),
                    )
                    for edge in graph.edges
                ],
            )
            self.connection.executemany(
                """
                INSERT INTO rooms(
                    drawing_id, room_id, area, polygon_json, boundary_handles_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        drawing_id,
                        room.room_id,
                        room.area,
                        _json(room.polygon),
                        _json(room.boundary_handles),
                    )
                    for room in room_items
                ],
            )
            self.connection.executemany(
                """
                INSERT INTO opening_hosts(
                    drawing_id, opening_handle, wall_handle, semantic, distance, anchor_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        drawing_id,
                        relation.opening_handle,
                        relation.wall_handle,
                        relation.opening_semantic.value,
                        relation.distance,
                        _json(relation.anchor),
                    )
                    for relation in opening_items
                ],
            )
            self.connection.executemany(
                """
                INSERT INTO room_adjacencies(drawing_id, room_a, room_b, shared_length)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (drawing_id, item.room_a, item.room_b, item.shared_length)
                    for item in adjacency_items
                ],
            )
            self.connection.executemany(
                """
                INSERT INTO annotation_bindings(
                    drawing_id, annotation_handle, target_handles_json, source
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        drawing_id,
                        binding.annotation_handle,
                        _json(binding.target_handles),
                        binding.source,
                    )
                    for binding in binding_items
                ],
            )
            if extraction_lane is not None:
                entity_count = self._count("entities", drawing_id)
                self.connection.execute(
                    """
                    INSERT INTO extraction_runs(
                        drawing_id, lane, entity_count, snapshot_digest,
                        warnings_json, intermediate_path
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        drawing_id,
                        extraction_lane,
                        entity_count,
                        snapshot_digest,
                        _json(tuple(warnings)),
                        intermediate_path,
                    ),
                )

        return SemanticIndexResult(
            drawing_id=drawing_id,
            node_count=len(graph.nodes),
            edge_count=len(graph.edges),
            room_count=len(room_items),
            opening_host_count=len(opening_items),
            room_adjacency_count=len(adjacency_items),
            annotation_binding_count=len(binding_items),
        )

    def semantic_summary(self, path: str) -> SemanticSummary | None:
        row = self.connection.execute(
            "SELECT id FROM drawings WHERE path = ?",
            (path,),
        ).fetchone()
        if row is None:
            return None
        drawing_id = int(row["id"])
        latest = self.connection.execute(
            """
            SELECT lane, snapshot_digest
            FROM extraction_runs
            WHERE drawing_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (drawing_id,),
        ).fetchone()
        return SemanticSummary(
            path=path,
            node_count=self._count("semantic_nodes", drawing_id),
            edge_count=self._count("semantic_edges", drawing_id),
            room_count=self._count("rooms", drawing_id),
            opening_host_count=self._count("opening_hosts", drawing_id),
            room_adjacency_count=self._count("room_adjacencies", drawing_id),
            annotation_binding_count=self._count("annotation_bindings", drawing_id),
            extraction_lane=str(latest["lane"]) if latest is not None else None,
            snapshot_digest=(
                str(latest["snapshot_digest"])
                if latest is not None and latest["snapshot_digest"] is not None
                else None
            ),
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
        return self._reference_query(
            "r.kind = 'block' AND lower(r.name) = lower(?)",
            (name,),
        )

    def xrefs(self, target_path: str | None = None) -> list[ReferenceRecord]:
        if target_path is None:
            return self._reference_query("r.kind = 'xref'", ())
        return self._reference_query(
            "r.kind = 'xref' AND lower(r.target_path) = lower(?)",
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
        allowed = {
            "entities",
            "refs",
            "semantic_nodes",
            "semantic_edges",
            "rooms",
            "opening_hosts",
            "room_adjacencies",
            "annotation_bindings",
        }
        if table not in allowed:
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
            CREATE TABLE IF NOT EXISTS semantic_nodes(
                drawing_id INTEGER NOT NULL REFERENCES drawings(id) ON DELETE CASCADE,
                node_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                attributes_json TEXT NOT NULL,
                PRIMARY KEY(drawing_id, node_id)
            );
            CREATE TABLE IF NOT EXISTS semantic_edges(
                id INTEGER PRIMARY KEY,
                drawing_id INTEGER NOT NULL REFERENCES drawings(id) ON DELETE CASCADE,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                kind TEXT NOT NULL,
                attributes_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rooms(
                drawing_id INTEGER NOT NULL REFERENCES drawings(id) ON DELETE CASCADE,
                room_id TEXT NOT NULL,
                area REAL NOT NULL,
                polygon_json TEXT NOT NULL,
                boundary_handles_json TEXT NOT NULL,
                PRIMARY KEY(drawing_id, room_id)
            );
            CREATE TABLE IF NOT EXISTS opening_hosts(
                drawing_id INTEGER NOT NULL REFERENCES drawings(id) ON DELETE CASCADE,
                opening_handle TEXT NOT NULL,
                wall_handle TEXT NOT NULL,
                semantic TEXT NOT NULL,
                distance REAL NOT NULL,
                anchor_json TEXT NOT NULL,
                PRIMARY KEY(drawing_id, opening_handle)
            );
            CREATE TABLE IF NOT EXISTS room_adjacencies(
                drawing_id INTEGER NOT NULL REFERENCES drawings(id) ON DELETE CASCADE,
                room_a TEXT NOT NULL,
                room_b TEXT NOT NULL,
                shared_length REAL NOT NULL,
                PRIMARY KEY(drawing_id, room_a, room_b)
            );
            CREATE TABLE IF NOT EXISTS annotation_bindings(
                drawing_id INTEGER NOT NULL REFERENCES drawings(id) ON DELETE CASCADE,
                annotation_handle TEXT NOT NULL,
                target_handles_json TEXT NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY(drawing_id, annotation_handle)
            );
            CREATE TABLE IF NOT EXISTS extraction_runs(
                id INTEGER PRIMARY KEY,
                drawing_id INTEGER NOT NULL REFERENCES drawings(id) ON DELETE CASCADE,
                lane TEXT NOT NULL,
                entity_count INTEGER NOT NULL,
                snapshot_digest TEXT,
                warnings_json TEXT NOT NULL,
                intermediate_path TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_entities_layer ON entities(layer);
            CREATE INDEX IF NOT EXISTS idx_refs_kind_name ON refs(kind, name);
            CREATE INDEX IF NOT EXISTS idx_refs_target_path ON refs(target_path);
            CREATE INDEX IF NOT EXISTS idx_semantic_edges_kind ON semantic_edges(kind);
            CREATE INDEX IF NOT EXISTS idx_opening_hosts_wall ON opening_hosts(wall_handle);
            CREATE INDEX IF NOT EXISTS idx_extraction_runs_drawing
            ON extraction_runs(drawing_id, id);
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
