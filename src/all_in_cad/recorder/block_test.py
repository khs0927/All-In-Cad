"""Tests for the block recorder: block definition + block insertion.

Run (PYTHONHOME must be cleared on this host, see block.py module docstring)::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\block_test.py

The tests deliberately read their own output back through the repository's real
chain (``extraction_runtime._normalize_ezdxf_entity`` ->
``topology.segments_from_entities`` -> ``topology.node_segments``) instead of
inspecting the DXF they just wrote. A recorder test that only checks its own
return value proves nothing about what a consumer will see.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:  # make `all_in_cad` importable without an install
    sys.path.insert(0, str(_SRC))

import ezdxf  # noqa: E402

from all_in_cad.extraction_runtime import _normalize_ezdxf_entity  # noqa: E402
from all_in_cad.readback import EntitySnapshot  # noqa: E402
from all_in_cad.recorder import block as block_mod  # noqa: E402
from all_in_cad.recorder.block import (  # noqa: E402
    ALLOWED_BLOCK_ENTITY_KINDS,
    CONVENTION_BLOCK_LAYERS,
    MEASURED_SEGMENT_CONTRIBUTION,
    OBSERVED_XICAD_BLOCK_LAYER,
    REJECTED_BLOCK_ENTITY_KINDS,
    BlockEntity,
    BlockDefinition,
    BlockInsertMode,
    BlockValidationError,
    arc_entity,
    line_entity,
    make_block_definition,
    make_block_insert,
    transform_point,
    write_block_definition,
    write_block_insert,
)
from all_in_cad.semantic_layers import LayerSemantic, classify_layer  # noqa: E402
from all_in_cad.topology import node_segments, segments_from_entities  # noqa: E402

TOL = 1e-6

#: Single resolution rule, identical in block_test.py, e2e_test.py and
#: text.py: ``FREECAD_EXE`` from the environment is the ONLY accepted input for
#: where the real binary lives. The previous module constant was a hardcoded
#: ``D:\CAD\FreeCAD\...`` path, which cannot exist on any other host, so this
#: test took its skip branch permanently and by construction rather than
#: intermittently -- it measured nothing anywhere except one machine.
#:
#: There is no detection helper in ``recorder/freecad/`` to reuse: the only
#: discovery there is ``run_freecad_script``'s inline
#: ``freecad_exe or os.environ.get("FREECAD_EXE", "FreeCADCmd.exe")``, which is
#: a defaulting rule, not a locator, and it runs *after* this decision point.
#: ``freecad_runner_test.py`` already follows the same env-var convention. So
#: there is deliberately no per-host fallback here: unset means a SKIP that
#: names the missing variable, never a silent pass and never a guessed path.
FREECAD_EXE_ENV = "FREECAD_EXE"
FREECAD_RUNNER_CANDIDATES = (
    Path(__file__).resolve().parent / "freecad" / "freecad_runner.py",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def doc() -> ezdxf.document.Drawing:
    return ezdxf.new(block_mod.DXF_WRITE_VERSION)


def _snapshot(entity, document_id: str = "d") -> EntitySnapshot:
    """Snapshot an entity exactly the way extraction_runtime does."""
    geometry, properties = _normalize_ezdxf_entity(entity)
    return EntitySnapshot(
        document_id=document_id,
        handle=entity.dxf.handle,
        entity_type=entity.dxftype(),
        layer=entity.dxf.layer,
        geometry=geometry,
        properties=properties,
    )


def measure(doc: ezdxf.document.Drawing) -> dict[str, object]:
    """Run the written modelspace through the repository's real chain."""
    entities = list(doc.modelspace())
    snapshots = [_snapshot(e) for e in entities]
    segments = segments_from_entities(snapshots)
    return {
        "entities": len(entities),
        "types": sorted({e.dxftype() for e in entities}),
        "segments": len(segments),
        "noded": len(node_segments(segments)),
        "length": sum(
            math.dist((s.start.x, s.start.y), (s.end.x, s.end.y)) for s in segments
        ),
    }


def _door_block(name: str = "OPEN1", base_point=(0, 0), with_arc: bool = False):
    """A 900x2100 opening outline: 4 straight edges, 6000 mm.

    ``with_arc=True`` adds one rounded corner, which is the interesting case
    for the unmeasurable-arc accounting. It is off by default so the (a)/(b)
    measurement counts only straight edges.
    """
    entities = [
        line_entity((0, 0), (900, 0), layer="DOOR"),
        line_entity((900, 0), (900, 2100), layer="DOOR"),
        line_entity((900, 2100), (0, 2100), layer="DOOR"),
        line_entity((0, 2100), (0, 0), layer="DOOR"),
    ]
    if with_arc:
        entities.append(arc_entity((900, 2100), 300, 0, 90, layer="DOOR_ELE"))
    return make_block_definition(name, entities, base_point=base_point)


# ---------------------------------------------------------------------------
# (a) vs (b): the measured decision, asserted against the real chain
# ---------------------------------------------------------------------------


def test_flatten_is_visible_and_insert_is_not_measured_not_guessed(
    doc: ezdxf.document.Drawing,
) -> None:
    """THE DECISION, AS A TEST.

    Same 4 straight edges, 6000 mm, two modes:

      (b) flatten (the DEFAULT) -> entities 4, segments 4, length 6000.0
      (a) insert                 -> entities 1, segments 0, length 0.0

    Both numbers are produced by ``segments_from_entities``, i.e. the function
    the rest of the repository actually calls. The insert case is asserted to
    be 0 on purpose: if a future change makes the normalizer traverse INSERT,
    this test fails and someone has to make that decision consciously.
    """
    definition = _door_block()

    flat_doc = ezdxf.new(block_mod.DXF_WRITE_VERSION)
    flat = write_block_insert(flat_doc, make_block_insert(definition, (0, 0)))
    flat_measure = measure(flat_doc)

    ins_doc = ezdxf.new(block_mod.DXF_WRITE_VERSION)
    ins = write_block_insert(
        ins_doc, make_block_insert(definition, (0, 0), mode=BlockInsertMode.INSERT)
    )
    ins_measure = measure(ins_doc)

    assert (flat_measure["entities"], flat_measure["segments"]) == (4, 4)
    assert flat_measure["length"] == pytest.approx(6000.0)
    assert flat.visible_downstream is True
    assert flat.contributed_segments == 4
    assert flat.contributed_length_mm == pytest.approx(6000.0)

    assert (ins_measure["entities"], ins_measure["segments"]) == (1, 0)
    assert ins_measure["length"] == pytest.approx(0.0)
    assert ins.visible_downstream is False
    assert ins.contributed_segments == 0


def test_insert_mode_reports_its_own_invisibility_as_a_contract(
    doc: ezdxf.document.Drawing,
) -> None:
    """"Invisible downstream" must be a reported contract, not a silence.

    The record carries what the INSERT hides, so a caller choosing (a) always
    learns the content's real size instead of finding out later.
    """
    definition = _door_block()
    record = write_block_insert(
        doc, make_block_insert(definition, (5000, 5000), mode=BlockInsertMode.INSERT)
    )

    assert record.mode is BlockInsertMode.INSERT
    assert record.contributed_segments == 0
    assert record.contributed_length_mm == 0.0
    # ... and what it hides, computed through the real chain
    assert record.hidden_segments == 4
    assert record.hidden_length_mm == pytest.approx(6000.0)

    contract = record.downstream_contract
    assert contract["visible_downstream"] is False
    assert contract["contributes_segments"] == 0
    assert contract["normalizer_geometry_keys"] == ["insert"]
    assert contract["normalizer_properties_keys"] == ["block_name"]
    assert "hidden_length_mm" in contract
    assert "segments_from_entities" in contract["reason"]


def test_require_downstream_visible_turns_the_invisibility_into_an_error(
    doc: ezdxf.document.Drawing,
) -> None:
    """The opt-in exists so a caller can forbid unmeasurable geometry outright."""
    definition = _door_block()
    with pytest.raises(BlockValidationError) as caught:
        write_block_insert(
            doc,
            make_block_insert(definition, (0, 0), mode=BlockInsertMode.INSERT),
            require_downstream_visible=True,
        )
    assert "require_downstream_visible" in str(caught.value)


def test_flatten_mode_is_the_default(doc: ezdxf.document.Drawing) -> None:
    assert make_block_insert(_door_block(), (0, 0)).mode is BlockInsertMode.FLATTEN


def test_measured_contribution_table_matches_the_real_normalizer(
    doc: ezdxf.document.Drawing,
) -> None:
    """The published table in the module docstring is re-measured here.

    A docstring table that drifts from behaviour is worse than no table, so the
    numbers are recomputed through ``_normalize_ezdxf_entity`` for every kind
    the module claims to know about.
    """
    expectations = {"LINE": 1, "ARC": 0}
    for kind, expected in expectations.items():
        assert MEASURED_SEGMENT_CONTRIBUTION[kind] == expected

    # LINE -> one segment
    line = doc.modelspace().add_line((0, 0), (10, 0), dxfattribs={"layer": "WAL1"})
    assert len(segments_from_entities([_snapshot(line)])) == 1

    # ARC -> zero, because the normalizer emits no start/end
    arc = doc.modelspace().add_arc((5, 5), 3, 0, 90, dxfattribs={"layer": "WAL1"})
    assert len(segments_from_entities([_snapshot(arc)])) == 0

    # the rejected kinds really do normalize to nothing usable
    for builder, kind in (
        (lambda d: d.add_ellipse((0, 0), major_axis=(1, 0)), "ELLIPSE"),
        (lambda d: d.add_spline([(0, 0), (1, 1), (2, 0)]), "SPLINE"),
    ):
        probe = ezdxf.new(block_mod.DXF_WRITE_VERSION)
        entity = builder(probe.modelspace())
        assert _snapshot(entity).geometry == {}
        assert len(segments_from_entities([_snapshot(entity)])) == 0
        assert kind in REJECTED_BLOCK_ENTITY_KINDS

    # a bulged LWPOLYLINE is the dangerous one: it DOES reach the chain, and
    # the chain drops the bulge. This is why it is rejected.
    bulged = ezdxf.new(block_mod.DXF_WRITE_VERSION)
    poly = bulged.modelspace().add_lwpolyline(
        [(0, 0, 0, 0, 0.5), (10, 0, 0, 0, 0.5)], dxfattribs={"layer": "WAL1"}
    )
    segments = segments_from_entities([_snapshot(poly)])
    assert len(segments) == 1
    chord = math.dist(
        (segments[0].start.x, segments[0].start.y),
        (segments[0].end.x, segments[0].end.y),
    )
    true_arc = math.pi * 5.0  # bulge 0.5 on a 10 mm chord is a semicircle, r=5
    assert chord == pytest.approx(10.0)
    assert true_arc > chord * 1.5  # ~15.7 mm vs 10.0 mm: silent under-measurement
    assert "LWPOLYLINE" in REJECTED_BLOCK_ENTITY_KINDS


# ---------------------------------------------------------------------------
# entity allowlist
# ---------------------------------------------------------------------------


def test_only_line_and_arc_are_allowed_kinds() -> None:
    assert {str(k) for k in ALLOWED_BLOCK_ENTITY_KINDS} == {"LINE", "ARC"}
    assert "LWPOLYLINE" in REJECTED_BLOCK_ENTITY_KINDS
    assert "HATCH" in REJECTED_BLOCK_ENTITY_KINDS
    assert "SPLINE" in REJECTED_BLOCK_ENTITY_KINDS
    assert "ELLIPSE" in REJECTED_BLOCK_ENTITY_KINDS
    for kind in REJECTED_BLOCK_ENTITY_KINDS:
        assert block_mod.REJECTION_REASONS[kind].strip(), kind


def test_a_rejected_kind_cannot_be_smuggled_into_a_definition() -> None:
    """Even if an object claims a rejected kind, construction refuses it."""
    for kind in ("LWPOLYLINE", "HATCH", "SPLINE", "ELLIPSE", "CIRCLE", "INSERT"):
        entity = BlockEntity(kind=kind, layer="WAL1", start=None, end=None)
        with pytest.raises(BlockValidationError) as caught:
            entity.validate()
        assert "not allowed" in str(caught.value)


def test_arc_in_a_block_is_reported_as_unmeasurable_rather_than_assumed_measurable(
    doc: ezdxf.document.Drawing,
) -> None:
    """ARC is allowed (wall/door/window already emit it) but its 0-segment
    contribution is stated in the record, not left for a consumer to find."""
    definition = _door_block(with_arc=True)
    record = write_block_definition(doc, definition)
    assert record.arc_count == 1
    assert record.arc_unmeasured_length_mm == pytest.approx(
        math.radians(90.0) * 300.0
    )
    assert record.measured_segments == 4  # the 5 entities, 4 of them straight
    assert record.measured_length_mm == pytest.approx(6000.0)


def test_full_circle_arc_is_refused_because_it_is_a_rejected_circle(
    doc: ezdxf.document.Drawing,
) -> None:
    with pytest.raises(BlockValidationError) as caught:
        arc_entity((0, 0), 10, 0, 360, layer="WAL1")
    assert "CIRCLE" in str(caught.value)


# ---------------------------------------------------------------------------
# definition -> insert round trip (reading our own output back)
# ---------------------------------------------------------------------------


def test_definition_then_insert_round_trips_through_the_file(
    doc: ezdxf.document.Drawing, tmp_path: Path
) -> None:
    """Define, insert, save, RELOAD, and measure the reloaded file."""
    definition = _door_block()
    write_block_definition(doc, definition)
    record = write_block_insert(doc, make_block_insert(definition, (5000, 5000)))

    assert doc.blocks.get("OPEN1") is not None
    assert record.entity_count == 4
    assert record.layer == "DOOR"
    assert record.layer_semantic is LayerSemantic.DOOR

    path = tmp_path / "blocks.dxf"
    doc.saveas(path)
    reloaded = ezdxf.readfile(path)

    # the block table entry survived with its content and its base point
    block = reloaded.blocks.get("OPEN1")
    assert block is not None
    assert len(list(block)) == 4
    assert tuple(block.block.dxf.base_point)[:2] == (0.0, 0.0)

    # and the flattened instance is still measurable after the round trip
    after = measure(reloaded)
    assert after["entities"] == 4
    assert after["segments"] == 4
    assert after["length"] == pytest.approx(6000.0)
    assert after["types"] == ["LINE"]


def test_insert_mode_round_trips_as_an_insert_and_stays_unmeasurable(
    doc: ezdxf.document.Drawing, tmp_path: Path
) -> None:
    definition = _door_block()
    record = write_block_insert(
        doc, make_block_insert(definition, (5000, 5000), mode=BlockInsertMode.INSERT)
    )
    assert record.entity_count == 1

    path = tmp_path / "insert.dxf"
    doc.saveas(path)
    reloaded = ezdxf.readfile(path)

    after = measure(reloaded)
    assert after["entities"] == 1
    assert after["segments"] == 0
    assert after["length"] == pytest.approx(0.0)

    reference = reloaded.modelspace().query("INSERT")[0]
    assert reference.dxf.name == "OPEN1"
    assert tuple(reference.dxf.insert)[:2] == (5000.0, 5000.0)
    assert reference.dxf.layer == "DOOR"


def test_definition_base_point_and_bbox_survive_the_round_trip(
    doc: ezdxf.document.Drawing, tmp_path: Path
) -> None:
    definition = _door_block(base_point=(450, 1050), with_arc=True)
    record = write_block_definition(doc, definition)
    assert (record.base_point.x, record.base_point.y) == (450.0, 1050.0)
    assert (record.bbox_min.x, record.bbox_min.y) == (0.0, 0.0)
    # the arc forces a conservative (full-circle) bound: centre x 900 +/- 300
    assert record.bbox_is_conservative is True
    assert record.bbox_max.x == pytest.approx(1200.0)
    assert record.bbox_max.y == pytest.approx(2400.0)

    path = tmp_path / "base.dxf"
    doc.saveas(path)
    reloaded = ezdxf.readfile(path)
    block = reloaded.blocks.get("OPEN1")
    assert tuple(block.block.dxf.base_point)[:2] == (450.0, 1050.0)


# ---------------------------------------------------------------------------
# instances: several, rotated, scaled, base-point offset
# ---------------------------------------------------------------------------


def test_several_instances_of_the_same_definition(
    doc: ezdxf.document.Drawing,
) -> None:
    definition = _door_block()
    write_block_definition(doc, definition)
    for x in (0.0, 3000.0, 6000.0, 9000.0):
        write_block_insert(doc, make_block_insert(definition, (x, 0)))

    after = measure(doc)
    assert after["entities"] == 16
    assert after["segments"] == 16
    assert after["length"] == pytest.approx(4 * 6000.0)
    # 16 independent copies: the accepted cost of (b), stated as a fact
    assert len(list(doc.blocks.get("OPEN1"))) == 4


def test_insert_mode_instances_share_one_definition(
    doc: ezdxf.document.Drawing,
) -> None:
    """The reason (a) exists: 4 instances, 1 block definition, 4 file entities."""
    definition = _door_block()
    write_block_definition(doc, definition)
    for x in (0.0, 3000.0, 6000.0, 9000.0):
        write_block_insert(
            doc, make_block_insert(definition, (x, 0), mode=BlockInsertMode.INSERT)
        )

    after = measure(doc)
    assert after["entities"] == 4
    assert after["segments"] == 0
    assert len(list(doc.blocks.get("OPEN1"))) == 4


def test_flatten_and_insert_agree_on_world_geometry(
    doc: ezdxf.document.Drawing, tmp_path: Path
) -> None:
    """The strongest assertion about (a): same world coordinates as (b).

    ezdxf's own ``Insert.virtual_entities()`` is the reference here -- that is
    the CAD application's real reading of the block reference, not a matrix
    this module assembled. If flatten mode ever drifted from the INSERT, (a)
    would be quietly a different drawing from (b) and this fails.
    """
    definition = _door_block(base_point=(450, 1050))
    write_block_definition(doc, definition)
    instance = make_block_insert(definition, (1000, 2000), rotation_deg=37.0, scale=1.5)
    write_block_insert(doc, instance)
    write_block_insert(
        doc,
        make_block_insert(
            definition,
            (1000, 2000),
            rotation_deg=37.0,
            scale=1.5,
            mode=BlockInsertMode.INSERT,
        ),
    )

    path = tmp_path / "agree.dxf"
    doc.saveas(path)
    reloaded = ezdxf.readfile(path)

    flat_lines = reloaded.modelspace().query("LINE")
    reference = reloaded.modelspace().query("INSERT")[0]
    assert len(flat_lines) == 4
    assert reference.dxf.rotation == pytest.approx(37.0)
    assert reference.dxf.xscale == pytest.approx(1.5)

    # what a CAD application sees when it resolves the reference
    resolved = [e for e in reference.virtual_entities() if e.dxftype() == "LINE"]
    assert len(resolved) == 4

    block_lines = [
        e for e in reloaded.blocks.get("OPEN1") if e.dxftype() == "LINE"
    ]
    assert len(block_lines) == 4

    base = (definition.base_point.x, definition.base_point.y)
    for block_line, flat, virtual in zip(block_lines, flat_lines, resolved, strict=True):
        predicted = transform_point(
            (block_line.dxf.start.x, block_line.dxf.start.y),
            base,
            (instance.location.x, instance.location.y),
            37.0,
            1.5,
        )
        # (1) our transform predicts the flatten output
        assert (flat.dxf.start.x, flat.dxf.start.y) == pytest.approx(
            (predicted.x, predicted.y), abs=1e-6
        )
        # (2) and the CAD application's own reading of the INSERT agrees
        assert (virtual.dxf.start.x, virtual.dxf.start.y) == pytest.approx(
            (predicted.x, predicted.y), abs=1e-6
        )


def test_rotation_moves_the_instance_as_predicted(
    doc: ezdxf.document.Drawing,
) -> None:
    definition = _door_block()
    record = write_block_insert(
        doc, make_block_insert(definition, (0, 0), rotation_deg=90.0)
    )
    lines = doc.modelspace().query("LINE")
    assert record.entity_count == 4
    # (900, 0) rotated 90 deg about the base point lands on (0, 900)
    rotated = {(round(e.dxf.start.x, 6), round(e.dxf.start.y, 6)) for e in lines}
    assert (0.0, 900.0) in rotated


def test_scale_multiplies_the_measured_length(
    doc: ezdxf.document.Drawing,
) -> None:
    definition = _door_block()
    for factor in (0.5, 1.0, 2.0):
        target = ezdxf.new(block_mod.DXF_WRITE_VERSION)
        write_block_insert(
            target, make_block_insert(definition, (0, 0), scale=factor)
        )
        assert measure(target)["length"] == pytest.approx(6000.0 * factor)


def test_base_point_offset_shifts_the_whole_instance(
    doc: ezdxf.document.Drawing,
) -> None:
    """With a non-zero base point, the BASE POINT lands on the insert location.

    block local (0, 0) is (-450, -1050) from the base point, so an insert at
    (1000, 2000) must place it at (550, 950) -- not at (1000, 2000). Getting
    this wrong is the classic block-recorder bug, so it is asserted on real
    coordinates rather than on a returned value.
    """
    definition = _door_block(base_point=(450, 1050))
    record = write_block_insert(doc, make_block_insert(definition, (1000, 2000)))
    lines = doc.modelspace().query("LINE")

    starts = {(round(e.dxf.start.x, 6), round(e.dxf.start.y, 6)) for e in lines}
    assert (550.0, 950.0) in starts          # world position of block local (0, 0)
    assert (1450.0, 3050.0) in starts       # world position of block local (900, 2100)
    assert (1000.0, 2000.0) not in starts    # the location is where the BASE POINT goes
    assert record.entity_count == 4
    # an offset placement does not change the size of the shape
    assert measure(doc)["length"] == pytest.approx(6000.0)


def test_non_positive_and_non_finite_transforms_are_refused(
    doc: ezdxf.document.Drawing,
) -> None:
    definition = _door_block()
    for kwargs in ({"scale": 0.0}, {"scale": -1.0}, {"rotation_deg": float("nan")}):
        with pytest.raises(BlockValidationError):
            make_block_insert(definition, (0, 0), **kwargs)


# ---------------------------------------------------------------------------
# rejections: empty, self-intersecting, duplicates, unknown layers
# ---------------------------------------------------------------------------


def test_empty_block_is_refused_and_says_why() -> None:
    with pytest.raises(BlockValidationError) as caught:
        make_block_definition("EMPTY", [])
    message = str(caught.value)
    assert "EMPTY" in message
    assert "zero" in message.lower()


def test_self_intersecting_block_is_refused() -> None:
    """A bow-tie does not bound one region, so it cannot be one definition."""
    bowtie = [
        line_entity((0, 0), (100, 100), layer="WAL1"),
        line_entity((0, 100), (100, 0), layer="WAL1"),
    ]
    assert make_block_definition("BT", bowtie, allow_self_intersection=True) is not None
    with pytest.raises(BlockValidationError) as caught:
        make_block_definition("BT", bowtie)
    assert "SELF-INTERSECTING" in str(caught.value)


def test_a_simple_ring_is_not_called_self_intersecting() -> None:
    """Guard against a false positive: the noder sees a closed rectangle as 4."""
    definition = _door_block()
    assert definition.is_self_intersecting() is False
    assert len(node_segments(definition.line_segments())) == 4


def test_zero_length_edge_is_refused() -> None:
    with pytest.raises(BlockValidationError) as caught:
        line_entity((5, 5), (5, 5), layer="WAL1")
    assert "zero length" in str(caught.value)


def test_duplicate_block_name_is_refused_unless_reuse_is_asked(
    doc: ezdxf.document.Drawing,
) -> None:
    definition = _door_block()
    write_block_definition(doc, definition)
    with pytest.raises(BlockValidationError) as caught:
        write_block_definition(doc, definition)
    assert "already exists" in str(caught.value)

    reused = write_block_definition(doc, definition, on_existing="reuse")
    assert reused.name == "OPEN1"
    assert reused.entity_count == 4


def test_unknown_layer_is_refused_and_no_new_name_is_invented() -> None:
    with pytest.raises(BlockValidationError) as caught:
        line_entity((0, 0), (10, 0), layer="OPEN1")
    message = str(caught.value)
    assert "UNKNOWN" in message
    assert "invents no new layer name" in message
    # every name this module offers is an existing project-convention layer
    for name in CONVENTION_BLOCK_LAYERS:
        assert classify_layer(name) is not LayerSemantic.UNKNOWN


def test_observed_layer_zero_is_opt_in_only(doc: ezdxf.document.Drawing) -> None:
    """The only block observation is layer "0"; it is usable, but only asked for."""
    assert OBSERVED_XICAD_BLOCK_LAYER == "0"
    with pytest.raises(BlockValidationError):
        make_block_definition(
            "Z", [line_entity((0, 0), (10, 0), layer="0")], base_point=(0, 0)
        )
    definition = make_block_definition(
        "Z",
        [line_entity((0, 0), (10, 0), layer="0", allow_observed_layer_zero=True)],
        allow_observed_layer_zero=True,
    )
    record = write_block_insert(doc, make_block_insert(definition, (0, 0)))
    # the opt-in is visible in the output, not hidden
    assert record.layer_semantic is LayerSemantic.UNKNOWN
    assert record.layer_counts() == {"0": 1}


def test_version_guard_rejects_a_non_r2018_document() -> None:
    old = ezdxf.new("R2000")
    with pytest.raises(ValueError) as caught:
        write_block_insert(old, make_block_insert(_door_block(), (0, 0)))
    assert "R2018" in str(caught.value)


# ---------------------------------------------------------------------------
# FreeCAD round trip: does the block survive a real CAD import?
# ---------------------------------------------------------------------------

_FREECAD_PROBE = '''\
import json
import os
import traceback

DXF_PATH = {dxf!r}
VERDICT_PATH = {verdict!r}

try:
    import FreeCAD
    import importDXF

    doc = FreeCAD.newDocument("aic_block")
    importDXF.insert(DXF_PATH, doc.Name)
    doc.recompute()

    shapes = []
    for obj in doc.Objects:
        if not obj.isDerivedFrom("Part::Feature"):
            continue
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        shapes.append({{"label": obj.Label, "length": float(shape.Length),
                        "edges": len(shape.Edges)}})
    labels = [o.Label for o in doc.Objects if not o.isDerivedFrom("Part::Feature")]

    record = {{
        "schema": "aic.verdict/1",
        "status": "ok",
        "reason": "",
        "run_token": {token!r},
        "script": "block_probe",
        "shapes": len(shapes),
        "total_length": sum(s["length"] for s in shapes),
        "total_edges": sum(s["edges"] for s in shapes),
        "shape_list": shapes,
        "layers": labels,
    }}
except Exception:
    record = {{
        "schema": "aic.verdict/1",
        "status": "failed",
        "reason": traceback.format_exc(),
        "run_token": {token!r},
        "script": "block_probe",
        "shapes": None,
        "total_length": None,
        "total_edges": None,
        "shape_list": [],
        "layers": [],
    }}

_tmp = VERDICT_PATH + ".part"
with open(_tmp, "w", encoding="utf-8") as _fh:
    json.dump(record, _fh, ensure_ascii=False)
os.replace(_tmp, VERDICT_PATH)
'''


def _load_freecad_runner(runner_path: Path):
    name = "aic_freecad_runner_block"
    spec = importlib.util.spec_from_file_location(name, runner_path)
    assert spec and spec.loader, "could not build a spec for the FreeCAD wrapper"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def _freecad_exe() -> Path | None:
    """The binary named by ``FREECAD_EXE``, or None when it is not usable."""
    raw = os.environ.get(FREECAD_EXE_ENV, "").strip()
    if not raw:
        return None
    exe = Path(raw)
    return exe if exe.is_file() else None


def _freecad_skip_reason() -> str | None:
    exe = _freecad_exe()
    if exe is None:
        raw = os.environ.get(FREECAD_EXE_ENV, "").strip()
        if not raw:
            return (
                f"{FREECAD_EXE_ENV} 미설정: real FreeCAD needed for the live "
                "import, and no detection helper exists to locate it"
            )
        return f"{FREECAD_EXE_ENV} points at {raw}, which is not a file"
    for candidate in FREECAD_RUNNER_CANDIDATES:
        if candidate.is_file():
            return None
    return "FreeCAD wrapper not found at any of: " + ", ".join(
        str(item) for item in FREECAD_RUNNER_CANDIDATES
    )


def _run_freecad(dxf: Path, work: Path, token: str) -> dict:
    reason = _freecad_skip_reason()
    if reason:
        pytest.skip(f"SKIPPED FOR LACK OF ENVIRONMENT: {reason}")
    work.mkdir(parents=True, exist_ok=True)
    exe = _freecad_exe()
    assert exe is not None, "checked by _freecad_skip_reason before this point"
    script = work / "probe.py"
    verdict = work / "verdict.json"
    script.write_text(
        _FREECAD_PROBE.format(dxf=str(dxf), verdict=str(verdict), token=token),
        encoding="utf-8",
    )
    runner = _load_freecad_runner(FREECAD_RUNNER_CANDIDATES[0])
    result = runner.run_freecad_script(
        script,
        freecad_exe=exe,
        timeout=300.0,
        workdir=work,
        verdict_path=verdict,
        expect_artifacts=[verdict],
        run_token=token,
    )
    if result.status == runner.STATUS_FAILED:
        if result.verdict and "freecad-executable-not-found" in " ".join(result.reasons):
            pytest.skip(f"SKIPPED FOR LACK OF ENVIRONMENT: {result.reasons}")
        pytest.fail(
            "FreeCAD run failed: "
            + json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
        )
    assert result.status == runner.STATUS_OK
    assert result.evidence == runner.EV_VERDICT, result.evidence
    assert result.verdict_fresh is True, "a stale verdict must not count"
    record = result.verdict or {}
    assert record["status"] == "ok", record.get("reason")
    return record


def test_freecad_import_keeps_the_block_shape_in_both_modes(
    tmp_path: Path,
) -> None:
    """[OBSERVED, real FreeCAD 1.1.3 headless] the block survives BOTH modes.

    MEASURED on this host with this block (4 straight edges, 6000 mm):

        our chain,  flatten : 4 entities, 4 segments, 6000.0 mm
        our chain,  insert  : 1 entity,  0 segments,    0.0 mm
        FreeCAD,   flatten  : 4 real edge shapes, 4 edges, 6000000 (internal units)
        FreeCAD,   insert   : 4 real edge shapes, 4 edges, 6000000 (internal units)

    and a second, less obvious measurement: if the flatten drawing ALSO carries
    an unreferenced block definition (``define_if_missing=True``, the default),
    FreeCAD instantiates that definition too and the drawing comes back with
    DOUBLE the geometry -- 8 shapes, 12000000 units, for the same 4 lines. The
    insertion resolves to exactly the same real geometry either way, so (a) is
    not a lesser drawing; but for a flatten-only file pass
    ``define_if_missing=False``.

    The point: "invisible downstream" is a property of THIS repository's
    normalizer, not of the DXF file. A CAD application resolves the INSERT into
    real geometry. So the recorder reports it as a contract on the record
    instead of pretending (a) writes a broken drawing -- and a caller who needs
    the geometry measurable HERE can demand it with
    ``require_downstream_visible=True``.
    """
    definition = _door_block()
    base_dir = tmp_path / "fx"
    base_dir.mkdir(parents=True, exist_ok=True)

    flat_path = base_dir / "flatten.dxf"
    flat_doc = ezdxf.new(block_mod.DXF_WRITE_VERSION)
    write_block_insert(
        flat_doc, make_block_insert(definition, (5000, 5000)), define_if_missing=False
    )
    flat_doc.saveas(flat_path)

    ins_path = base_dir / "insert.dxf"
    ins_doc = ezdxf.new(block_mod.DXF_WRITE_VERSION)
    write_block_insert(
        ins_doc, make_block_insert(definition, (5000, 5000), mode=BlockInsertMode.INSERT)
    )
    ins_doc.saveas(ins_path)

    flat = _run_freecad(flat_path, base_dir / "w_flat", "AIC-BLOCK-FLAT")
    ins = _run_freecad(ins_path, base_dir / "w_ins", "AIC-BLOCK-INS")

    # the block definition object survived the import in the insert file
    assert any(
        shape["label"].startswith("BLOCK_") for shape in ins["shape_list"]
    ), ins["shape_list"]

    # the instance geometry survived as real, non-null shapes in both
    flat_edges = [s for s in flat["shape_list"] if not s["label"].startswith("BLOCK_")]
    ins_edges = [s for s in ins["shape_list"] if not s["label"].startswith("BLOCK_")]
    assert len(flat_edges) == 4, flat_edges
    assert len(ins_edges) == 4, ins_edges
    assert sum(s["edges"] for s in flat_edges) == 4
    assert sum(s["edges"] for s in ins_edges) == 4
    # ... and they carry the SAME total length, so (a) is not a lesser drawing
    assert sum(s["length"] for s in flat_edges) == pytest.approx(
        sum(s["length"] for s in ins_edges), rel=1e-6
    )

    # the DOOR layer survives both; only the insert file also gets block layers
    assert "DOOR" in flat["layers"], flat["layers"]
    assert "DOOR" in ins["layers"], ins["layers"]

    # [MEASURED] carrying an UNREFERENCED block definition in a flatten-only
    # file makes FreeCAD instantiate it as well, so the same 4 lines come back
    # as twice the geometry. This is why write_block_insert documents
    # define_if_missing=False for flatten-only drawings.
    with_def_path = base_dir / "flatten_with_def.dxf"
    with_def_doc = ezdxf.new(block_mod.DXF_WRITE_VERSION)
    write_block_insert(
        with_def_doc, make_block_insert(definition, (5000, 5000))
    )  # default define_if_missing=True
    with_def_doc.saveas(with_def_path)
    with_def = _run_freecad(
        with_def_path, base_dir / "w_def", "AIC-BLOCK-DEF"
    )
    with_def_edges = [
        s for s in with_def["shape_list"] if not s["label"].startswith("BLOCK_")
    ]
    assert len(with_def_edges) == 2 * len(flat_edges), with_def_edges
    assert sum(s["length"] for s in with_def_edges) == pytest.approx(
        2 * sum(s["length"] for s in flat_edges), rel=1e-6
    )
    # ... and our own chain is unaffected: it never saw the definition either way
    assert measure(ezdxf.readfile(with_def_path))["segments"] == 4

    # the contrast that matters: OUR chain sees only the flattened one
    assert measure(ezdxf.readfile(flat_path))["segments"] == 4
    assert measure(ezdxf.readfile(ins_path))["segments"] == 0


# ---------------------------------------------------------------------------
# block name guard (added after this guard was found to survive mutation:
# replacing the ``if not isinstance(self.name, str) or not self.name.strip()``
# condition with a constant left all 28 tests in this file green)
# ---------------------------------------------------------------------------


def _valid_block_entities() -> tuple:
    """The simplest entity set that passes every other validation rule."""
    return (line_entity((0, 0), (100, 0), layer="DOOR_ELE"),)


def test_a_blank_block_name_is_refused() -> None:
    """An empty name is the silent-success this module exists to prevent.

    A BLOCK record with no name inserts nothing and measures as nothing, so
    writing it produces a file entry that looks like content and is not. The
    guard was real but untested, which is indistinguishable from it being
    absent as far as the suite was concerned.
    """
    for blank in ("", " ", "   ", "\t", "\n", " \t\n "):
        definition = BlockDefinition(name=blank, entities=_valid_block_entities())
        with pytest.raises(BlockValidationError) as excinfo:
            definition.validate()
        assert "non-empty string" in str(excinfo.value), (
            f"a blank name must be refused as a non-empty-string problem, got: "
            f"{excinfo.value}"
        )


def test_a_non_string_block_name_is_refused() -> None:
    """The other half of the same guard: type, not just emptiness.

    A name of ``123`` is truthy and would pass a bare ``not self.name`` check,
    so pinning only the empty string leaves the type half unprotected.
    """
    for wrong in (123, 0, None, 1.5, b"OPEN1", ["OPEN1"]):
        definition = BlockDefinition(name=wrong, entities=_valid_block_entities())
        with pytest.raises(BlockValidationError) as excinfo:
            definition.validate()
        assert "non-empty string" in str(excinfo.value)


def test_a_placeholder_name_that_looks_blank_is_still_refused() -> None:
    """Whitespace-only is not a name, and must not be auto-trimmed into one.

    If a future change starts stripping the name before comparing, a caller
    could write a definition under a name the drawing never shows. Pin the
    refusal so that change has to be deliberate.
    """
    definition = BlockDefinition(name="   ", entities=_valid_block_entities())
    with pytest.raises(BlockValidationError):
        definition.validate()
    # and the guard did not mutate the name on the way through
    assert definition.name == "   "


def test_a_real_block_name_still_validates() -> None:
    """The control: the guard above must not be satisfied by refusing everything.

    A defense that rejects all names passes every negative case above while
    making the module unusable, so the positive case is pinned too.
    """
    BlockDefinition(name="OPEN1", entities=_valid_block_entities()).validate()


def test_the_name_is_checked_before_the_entities() -> None:
    """A blank name on an otherwise empty definition must report the name.

    The name guard runs first so the error names the actual problem. If a
    reorder ever let the empty-entities check win, a caller with a blank name
    and no entities would be told to add geometry, add it, and still fail.
    """
    definition = BlockDefinition(name="", entities=())
    with pytest.raises(BlockValidationError) as excinfo:
        definition.validate()
    assert "non-empty string" in str(excinfo.value)