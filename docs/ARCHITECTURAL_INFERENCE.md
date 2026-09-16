# Architectural inference baseline

The semantic layer deliberately separates deterministic geometry from host-specific CAD APIs.

## Topology

`topology.py` converts normalized line/polyline snapshots into 2D segments, snaps coordinates to a configured tolerance, finds pairwise intersections, and splits linework into deterministic noded segments. Collinear overlap is represented by splitting at shared endpoints and merging source handles for coincident pieces.

This is an O(n²) baseline intended to establish correctness before a spatial index is introduced for large drawings.

## Walls

`semantic_graph.py` already identifies candidate wall pairs using layer semantics, parallel-angle error, perpendicular distance, and projected overlap. This is a geometric candidate relation, not a claim that every pair is a construction wall.

## Openings

`architecture.py` associates DOOR and WINDOW entities with the nearest wall segment when the entity exposes a usable insertion point, position, center, point collection, or start/end midpoint. Relations outside the configured maximum distance are rejected.

## Rooms

Room candidates are bounded positive faces from noded WALL linework. The face walk is deterministic and supports shared-wall adjacency. This is a baseline for normalized architectural boundary linework; production DWGs may need wall-face generation, gap healing, arc support, and project-specific tolerance tuning.

## Annotation bindings

Dimension bindings are created only when normalized properties explicitly contain `target_handle` or `target_handles`. The baseline does not infer dimension ownership from text proximity because that would create ungrounded relationships.

## Project index

`project_index.py` stores drawing fingerprints, normalized entity digests, blocks, and xrefs in SQLite. Unchanged file fingerprints are skipped. Changed drawings replace their entity/reference rows transactionally.

Block/xref relations are conservative: they require normalized `block_name`, `xref_path`, or `external_reference` metadata (or a known block-reference entity type). Arbitrary drawing text is never promoted to a reference.

## Live validation still required

The algorithms are host-independent and covered by synthetic fixtures. Real AutoCAD 2027/ZWCAD 2026 drawings are still required to tune tolerances, validate native normalization, add arc/bulge handling, and measure precision/recall against representative architectural DWGs.
