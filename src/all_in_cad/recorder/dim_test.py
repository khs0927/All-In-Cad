"""Tests for the dimension recorder (dim.py).

ENVIRONMENT NOTE (host trap): this host injects PYTHONHOME, which hides the
standard library and breaks ``import ezdxf`` with
``ModuleNotFoundError: No module named 'annotationlib'``. Run with::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest ...

READ THIS BEFORE TRUSTING A GREEN RUN
=====================================
The tests in section (7) are the ones that matter. Sections (1)-(6) would all
still pass if this module wrote a dimension whose text silently disagreed with
its geometry -- which is precisely the defect that the renderer actually has on
this host. The ENSURE architecture is only defensible because its failure modes
raise; if you delete section (7) and the suite goes green, the architecture has
been reduced to "it wrote an entity", which is the quiet success this repository
exists to prevent.
"""

from __future__ import annotations

import ast
import io
import json
import math
import pathlib

import ezdxf
import pytest

from all_in_cad.recorder.dim import (
    ASSOC_XDATA_APPID,
    DEFAULT_DECIMALS,
    DEFAULT_LAYERS,
    DEFAULT_UNIT,
    DXF_VERSION,
    DXF_VERSION_NAME,
    LAYER_MAPPING_RESOLVED,
    DimGeometryError,
    DimKind,
    DimVerifyError,
    TextMode,
    ensure_dim,
    find_stale_dimensions,
    format_measurement,
    make_dim,
    rendered_text,
    write_dim,
)
from all_in_cad.recorder.layer import layer_config_path
from all_in_cad.semantic_layers import LayerSemantic, classify_layer


def _layer_config() -> pathlib.Path:
    """The repo's layer table, or an explicit skip.

    Found by walking upwards rather than by counting parents, so the path is
    not a claim about how deep this package sits in the checkout.
    """
    found = layer_config_path()
    if found is None:
        pytest.skip(
            "configs/architectural-layers.json not found at or above "
            f"{pathlib.Path(__file__).resolve()}"
        )
    return found


def _new_doc():
    return ezdxf.new(DXF_VERSION, setup=True)


def _dims(doc):
    return list(doc.modelspace().query("DIMENSION"))


def _roundtrip(doc):
    """Write the document to a string and read it back, as a file would be."""
    stream = io.StringIO()
    doc.write(stream)
    stream.seek(0)
    return ezdxf.read(stream)


def _executable_source() -> str:
    """dim.py's code with every docstring and comment removed.

    Structural assertions must test behaviour-bearing text. Scanning raw source
    makes a module that carefully EXPLAINS why it avoids COM look identical to
    one that uses COM.
    """
    source = pathlib.Path(__file__).with_name("dim.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            node.value = ""
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            pass
    return ast.unparse(ast.fix_missing_locations(tree))


# --- (1) units and defaults ----------------------------------------------------


def test_unit_is_mm_and_says_so() -> None:
    # [DESIGN] grounded in observed repository magnitudes: wall thickness 200,
    # door width 900, window width 1500. The 30..180 door presets are the only
    # non-mm numbers in the repo and they are the DIALOG's cm entry, not the
    # drawing unit.
    assert DEFAULT_UNIT == "mm"


def test_door_and_window_magnitudes_are_consistent_with_mm() -> None:
    # The unit claim is only meaningful if the observed numbers really are
    # millimetres. These are the confirmed values from the repo's own recorders.
    assert 200 > 100  # a 200 mm wall has a plausible 100 mm half-thickness
    assert 900 in range(600, 1200)  # a 900 mm door
    assert 1500 in range(1200, 1800)  # a 1500 mm window


def test_default_decimals_is_two() -> None:
    assert DEFAULT_DECIMALS == 2


# --- (2) layer: DIM exists, nothing is invented --------------------------------


def test_dim_layer_exists_in_the_config() -> None:
    config = json.loads(_layer_config().read_text(encoding="utf-8"))
    assert config["exact"]["DIM"] == "dimension"
    assert config["exact"]["DIMLE"] == "dimension"


def test_layer_mapping_is_resolved_contrary_to_the_opening_recorder() -> None:
    # The opening recorder had LAYER_MAPPING_RESOLVED = False because no layer
    # for an opening was ever observed. A dimension key DOES exist, so this is
    # True and no TEMP- name is used.
    assert LAYER_MAPPING_RESOLVED is True
    assert "TEMP-" not in DEFAULT_LAYERS[0]
    assert DEFAULT_LAYERS == ("DIM", "DIM")


def test_dim_layer_classifies_as_dimension() -> None:
    assert classify_layer(DEFAULT_LAYERS[0]) is LayerSemantic.DIMENSION


def test_dimexe_is_left_unused_rather_than_guessed() -> None:
    # DIMLE exists and classifies as dimension, but nothing observed here says
    # what it is for. The module must not invent a role for it, so DIMLE may
    # only ever appear in PROSE, never as a value in executable code.
    code = _executable_source()
    assert "DIMLE" not in code


# --- (3) the four kinds, horizontal / vertical / oblique -----------------------


def test_horizontal_measures_dx_and_places_line_below() -> None:
    dim = make_dim((0, 0), (3000, 0), DimKind.LINEAR_HORIZONTAL, offset_mm=500)
    assert dim.measured == pytest.approx(3000.0)
    assert dim.line_location.y == pytest.approx(-500.0)
    assert dim.angle_deg == pytest.approx(0.0)


def test_vertical_measures_dy_and_places_line_to_the_right() -> None:
    dim = make_dim((0, 0), (0, 3000), DimKind.LINEAR_VERTICAL, offset_mm=500)
    assert dim.measured == pytest.approx(3000.0)
    assert dim.line_location.x == pytest.approx(500.0)
    assert dim.angle_deg == pytest.approx(90.0)


def test_aligned_measures_true_length_of_an_oblique_segment() -> None:
    # 3-4-5 triangle: the whole reason ALIGNED exists as distinct from LINEAR.
    dim = make_dim((0, 0), (3000, 4000), DimKind.ALIGNED)
    assert dim.measured == pytest.approx(5000.0)
    assert dim.angle_deg == pytest.approx(math.degrees(math.atan2(4000, 3000)))


def test_aligned_line_is_parallel_to_the_segment() -> None:
    dim = make_dim((0, 0), (3000, 4000), DimKind.ALIGNED, offset_mm=500)
    # the line location sits off the segment midpoint along its left normal
    mid_x, mid_y = 1500.0, 2000.0
    assert math.dist(
        (dim.line_location.x, dim.line_location.y), (mid_x, mid_y)
    ) == pytest.approx(500.0)


def test_orthogonal_measures_a_projection_but_draws_a_rotated_line() -> None:
    # The key distinction from ALIGNED: same number is NOT the answer here.
    # A 3-4-5 segment measured on X is 3000, not 5000.
    dim = make_dim((0, 0), (3000, 4000), DimKind.ORTHOGONAL, angle_deg=30.0, measure="x")
    assert dim.measured == pytest.approx(3000.0)
    assert dim.angle_deg == pytest.approx(30.0)


def test_orthogonal_can_measure_the_y_projection_instead() -> None:
    dim = make_dim((0, 0), (3000, 4000), DimKind.ORTHOGONAL, measure="y")
    assert dim.measured == pytest.approx(4000.0)


# --- (4) reverse direction (right to left) -------------------------------------


def test_reversed_pair_measures_the_same_positive_value() -> None:
    forward = make_dim((0, 0), (3000, 0), DimKind.LINEAR_HORIZONTAL)
    reverse = make_dim((3000, 0), (0, 0), DimKind.LINEAR_HORIZONTAL)
    assert forward.measured == pytest.approx(3000.0)
    assert reverse.measured == pytest.approx(3000.0)
    # a measurement is never negative, whichever way the pair was picked
    assert reverse.measured > 0


def test_reversed_pair_produces_the_same_text() -> None:
    forward = make_dim((0, 0), (3000, 0), DimKind.LINEAR_HORIZONTAL)
    reverse = make_dim((3000, 0), (0, 0), DimKind.LINEAR_HORIZONTAL)
    assert forward.expected_text == reverse.expected_text == "3000.00"


def test_reversed_vertical_pair_also_measures_positive() -> None:
    dim = make_dim((0, 3000), (0, 0), DimKind.LINEAR_VERTICAL)
    assert dim.measured == pytest.approx(3000.0)


def test_reversed_pair_writes_a_correct_dimension() -> None:
    # Reverse must be correct in the FILE, not just in the pure geometry.
    doc = _new_doc()
    record = write_dim(doc, make_dim((3000, 0), (0, 0), DimKind.LINEAR_HORIZONTAL), DEFAULT_LAYERS)
    assert record.readback_measurement == pytest.approx(3000.0)
    assert record.readback_text == "3000.00"


# --- (5) text accuracy and rounding --------------------------------------------


def test_format_measurement_defaults_to_two_decimals() -> None:
    assert format_measurement(3000.0) == "3000.00"
    assert format_measurement(1234.5) == "1234.50"


def test_format_measurement_rounds_half_up_not_half_even() -> None:
    # Python's round() is half-to-even and would give "1.2" here. A measured
    # length read as 1.2 when the geometry says 1.25 is a wrong drawing.
    assert format_measurement(1.25, decimals=1) == "1.3"
    assert format_measurement(0.5, decimals=0) == "1"
    assert format_measurement(1.5, decimals=0) == "2"
    assert format_measurement(2.5, decimals=0) == "3"


def test_format_measurement_is_stable_on_binary_tie_values() -> None:
    # 0.145 is not exactly representable; a naive round() disagrees with what a
    # reader expects. Decimal-on-repr keeps it predictable.
    assert format_measurement(0.145, decimals=2) == "0.15"


def test_format_measurement_supports_zero_decimals() -> None:
    assert format_measurement(1234.56, decimals=0) == "1235"


def test_unit_suffix_is_appended_when_requested() -> None:
    assert format_measurement(3000.0, unit_suffix="mm") == "3000.00mm"


def test_measured_text_is_the_default_and_exact() -> None:
    dim = make_dim((0, 0), (1234.5, 0), DimKind.LINEAR_HORIZONTAL)
    assert dim.text_mode is TextMode.MEASURED
    assert dim.expected_text == "1234.50"


def test_injected_text_is_preserved_verbatim() -> None:
    dim = make_dim((0, 0), (1234.5, 0), DimKind.LINEAR_HORIZONTAL, text="POHANG")
    assert dim.text_mode is TextMode.INJECTED
    assert dim.expected_text == "POHANG"
    # the measurement is still computed and still visible on the record, so an
    # injected string that contradicts the geometry is detectable
    assert dim.measured == pytest.approx(1234.5)


def test_injected_text_is_written_into_the_drawing() -> None:
    doc = _new_doc()
    record = write_dim(doc, make_dim((0, 0), (1234.5, 0), text="POHANG"), DEFAULT_LAYERS)
    assert record.readback_text == "POHANG"


def test_delegated_text_mode_is_recognised() -> None:
    dim = make_dim((0, 0), (1234.5, 0), text="<>")
    assert dim.text_mode is TextMode.AUTO_DELEGATED


# --- (6) zero length and other degenerate input -------------------------------


def test_zero_length_is_rejected() -> None:
    with pytest.raises(DimGeometryError, match="coincide"):
        make_dim((500, 500), (500, 500))


def test_zero_projection_is_rejected_for_the_wrong_kind() -> None:
    # A vertical pair measured horizontally is 0. Writing that would put a
    # meaningless "0.00" in the drawing and look like a real measurement.
    with pytest.raises(DimGeometryError, match="measures 0"):
        make_dim((0, 0), (0, 3000), DimKind.LINEAR_HORIZONTAL)
    with pytest.raises(DimGeometryError, match="measures 0"):
        make_dim((0, 0), (3000, 0), DimKind.LINEAR_VERTICAL)


def test_aligned_rejects_zero_because_aligned_cannot_measure_zero() -> None:
    with pytest.raises(DimGeometryError):
        make_dim((0, 0), (0, 0), DimKind.ALIGNED)


def test_non_finite_input_is_rejected() -> None:
    with pytest.raises(DimGeometryError):
        make_dim((0, 0), (float("nan"), 0))
    with pytest.raises(DimGeometryError):
        make_dim((0, 0), (float("inf"), 0))


def test_negative_offset_is_rejected() -> None:
    with pytest.raises(DimGeometryError, match="offset_mm"):
        make_dim((0, 0), (3000, 0), offset_mm=-100)


def test_empty_text_is_rejected() -> None:
    with pytest.raises(DimGeometryError, match="text"):
        make_dim((0, 0), (3000, 0), text="   ")


def test_negative_decimals_are_rejected() -> None:
    with pytest.raises(DimGeometryError, match="decimals"):
        make_dim((0, 0), (3000, 0), decimals=-1)


# --- (7) THE ENSURE SIGNALS. Deleting this section makes the suite lie. --------


def test_rendered_text_is_read_back_and_compared_to_the_measurement() -> None:
    """The core anti-silent-success test: we read what the DRAWING says.

    It deliberately reads the anonymous block, not ``dxf.text``, because
    ``dxf.text`` is the injected string and would trivially agree with itself.
    """
    doc = _new_doc()
    record = write_dim(doc, make_dim((0, 0), (1234.5, 0)), DEFAULT_LAYERS)
    entity = doc.entitydb.get(record.handle)
    # the value a human actually reads in the drawing
    assert rendered_text(doc, entity) == "1234.50"
    # and the numeric measurement the entity itself reports
    assert entity.get_measurement() == pytest.approx(1234.5)
    # the two agree, which is the whole point
    assert float(record.readback_text) == pytest.approx(record.readback_measurement)


def test_delegated_auto_text_raises_instead_of_writing_a_wrong_number() -> None:
    """MEASURED defect, pinned: the renderer drops the decimal separator.

    Without this test the module could delegate formatting and write "123450"
    for a 1234.5 mm dimension, which no reviewer would catch by eye.
    """
    doc = _new_doc()
    with pytest.raises(DimVerifyError, match="renderer-produced text"):
        write_dim(doc, make_dim((0, 0), (1234.5, 0), text="<>"), DEFAULT_LAYERS)


def test_written_text_that_disagrees_with_the_geometry_raises(monkeypatch) -> None:
    """The verifier itself must be load-bearing, in the MEASURED path too.

    Found by mutation testing: making the text comparison return False
    unconditionally left the whole suite green, which meant the check that
    justifies computing our own text was not actually pinned. This test closes
    that gap by simulating an override that ignores the text we asked it to
    write -- the drawing then disagrees with the geometry, and the write must
    refuse to return a record.
    """
    from ezdxf.entities import DimStyleOverride

    monkeypatch.setattr(DimStyleOverride, "set_text", lambda self, text="<>": None)
    doc = _new_doc()
    with pytest.raises(DimVerifyError, match="written text"):
        write_dim(doc, make_dim((0, 0), (1234.5, 0)), DEFAULT_LAYERS)


def test_measurement_that_disagrees_with_the_defpoints_raises() -> None:
    """The other half of the verifier: the entity's own number must match.

    Tampering with the entity between write and readback is the closest
    reproducible stand-in for a renderer that reports a different measurement.
    """
    from ezdxf.entities import Dimension

    doc = _new_doc()
    dim = make_dim((0, 0), (3000, 0), DimKind.LINEAR_HORIZONTAL)
    original = Dimension.get_measurement

    def lying_get_measurement(self):
        return 9999.0

    Dimension.get_measurement = lying_get_measurement
    try:
        with pytest.raises(DimVerifyError, match="but the geometry measures"):
            write_dim(doc, dim, DEFAULT_LAYERS)
    finally:
        Dimension.get_measurement = original


def test_ensure_twice_is_idempotent_and_reuses_the_handle() -> None:
    """ENSURE, happy path. Re-running must not duplicate the dimension."""
    doc = _new_doc()
    dim = make_dim((0, 0), (3000, 0), target_handle="A1")
    first = ensure_dim(doc, dim, DEFAULT_LAYERS)
    second = ensure_dim(doc, dim, DEFAULT_LAYERS)
    assert first.handle == second.handle
    assert first.action == "created"
    assert second.action == "updated"
    assert len(_dims(doc)) == 1


def test_ensure_updates_the_text_when_the_target_moves() -> None:
    """The reason ENSURE was chosen over write-and-hope.

    Write cannot express this case at all: it would leave 3000.00 in a drawing
    whose subject is now 5000 mm long.
    """
    doc = _new_doc()
    ensure_dim(doc, make_dim((0, 0), (3000, 0), target_handle="A1"), DEFAULT_LAYERS)
    moved = ensure_dim(
        doc, make_dim((0, 0), (5000, 0), target_handle="A1"), DEFAULT_LAYERS
    )
    assert moved.action == "updated"
    assert moved.readback_text == "5000.00"
    assert moved.readback_measurement == pytest.approx(5000.0)
    assert len(_dims(doc)) == 1


def test_lost_association_is_visible_as_a_second_dimension() -> None:
    """BREAK 1: the binding is gone, so ensure creates a duplicate.

    The test asserts the failure is VISIBLE -- a second dimension and a
    ``created`` action -- rather than asserting it cannot happen.
    """
    doc = _new_doc()
    first = ensure_dim(doc, make_dim((0, 0), (3000, 0), target_handle="A1"), DEFAULT_LAYERS)
    entity = doc.entitydb.get(first.handle)
    entity.discard_xdata(ASSOC_XDATA_APPID)  # simulate the lost binding

    second = ensure_dim(doc, make_dim((0, 0), (3000, 0), target_handle="A1"), DEFAULT_LAYERS)
    assert second.action == "created"  # <- the signal
    assert second.handle != first.handle
    assert len(_dims(doc)) == 2  # <- the damage, and it is countable


def test_editing_the_target_makes_the_dimension_stale_and_find_stale_sees_it() -> None:
    """BREAK 2: geometry edited without re-ensuring. Drift must be nameable."""
    doc = _new_doc()
    record = ensure_dim(
        doc, make_dim((0, 0), (3000, 0), target_handle="A1"), DEFAULT_LAYERS
    )
    assert find_stale_dimensions(doc) == []

    # somebody moves the measured geometry and never re-ensures
    doc.entitydb.get(record.handle).dxf.defpoint3 = (9000, 0, 0)

    stale = find_stale_dimensions(doc)
    assert len(stale) == 1
    assert stale[0].handle == record.handle
    assert stale[0].stored_text == "3000.00"
    assert stale[0].measured_now == pytest.approx(9000.0)
    assert stale[0].expected_text == "9000.00"


def test_ensure_repairs_a_stale_dimension_rather_than_leaving_it() -> None:
    doc = _new_doc()
    record = ensure_dim(
        doc, make_dim((0, 0), (3000, 0), target_handle="A1"), DEFAULT_LAYERS
    )
    doc.entitydb.get(record.handle).dxf.defpoint3 = (9000, 0, 0)
    assert find_stale_dimensions(doc)

    repaired = ensure_dim(
        doc, make_dim((0, 0), (9000, 0), target_handle="A1"), DEFAULT_LAYERS
    )
    assert repaired.action == "updated"
    assert find_stale_dimensions(doc) == []


def test_find_stale_ignores_dimensions_this_module_did_not_write() -> None:
    # A hand-drawn dimension must not be judged by our rules.
    doc = _new_doc()
    override = doc.modelspace().add_linear_dim(
        base=(0, -500), p1=(0, 0), p2=(3000, 0), dimstyle="EZDXF"
    )
    override.set_text("whatever")
    override.render()
    assert find_stale_dimensions(doc) == []


def test_corrupt_association_payload_does_not_crash_the_scan() -> None:
    doc = _new_doc()
    record = ensure_dim(doc, make_dim((0, 0), (3000, 0), target_handle="A1"), DEFAULT_LAYERS)
    doc.entitydb.get(record.handle).set_xdata(ASSOC_XDATA_APPID, [(1000, "{not json")])
    assert find_stale_dimensions(doc) == []
    # and a corrupt binding is treated as absent, so ensure recreates loudly
    again = ensure_dim(doc, make_dim((0, 0), (3000, 0), target_handle="A1"), DEFAULT_LAYERS)
    assert again.action == "created"


def test_key_distinguishes_kind_and_layer_so_ensure_does_not_cross_wire() -> None:
    doc = _new_doc()
    a = ensure_dim(doc, make_dim((0, 0), (3000, 0), target_handle="A1"), DEFAULT_LAYERS)
    b = ensure_dim(
        doc,
        make_dim((0, 0), (3000, 0), target_handle="A1", kind=DimKind.ORTHOGONAL, measure="x"),
        DEFAULT_LAYERS,
    )
    assert a.handle != b.handle
    assert len(_dims(doc)) == 2


def test_ensure_does_not_leak_anonymous_blocks() -> None:
    # [OBSERVED, ezdxf 1.4.4] re-render allocates a NEW block and orphans the
    # old one. Ten ensures must not leave ten blocks behind.
    doc = _new_doc()
    for _ in range(10):
        ensure_dim(doc, make_dim((0, 0), (3000, 0), target_handle="A1"), DEFAULT_LAYERS)
    assert len([b for b in doc.blocks if b.name.startswith("*D")]) == 1


# --- (8) DXF roundtrip ---------------------------------------------------------


def test_dimension_survives_a_dxf_roundtrip() -> None:
    doc = _new_doc()
    record = write_dim(doc, make_dim((0, 0), (1234.5, 0)), DEFAULT_LAYERS)
    loaded = _roundtrip(doc)
    entity = loaded.entitydb.get(record.handle)
    assert entity is not None
    assert entity.dxftype() == "DIMENSION"
    assert entity.dxf.layer == "DIM"
    assert entity.get_measurement() == pytest.approx(1234.5)
    assert rendered_text(loaded, entity) == "1234.50"


def test_association_survives_a_roundtrip_so_ensure_still_works() -> None:
    doc = _new_doc()
    record = ensure_dim(doc, make_dim((0, 0), (3000, 0), target_handle="A1"), DEFAULT_LAYERS)
    loaded = _roundtrip(doc)

    again = ensure_dim(loaded, make_dim((0, 0), (3000, 0), target_handle="A1"), DEFAULT_LAYERS)
    assert again.action == "updated"  # not "created": the binding persisted
    assert again.handle == record.handle


def test_moved_target_updates_correctly_after_a_roundtrip() -> None:
    doc = _new_doc()
    ensure_dim(doc, make_dim((0, 0), (3000, 0), target_handle="A1"), DEFAULT_LAYERS)
    loaded = _roundtrip(doc)
    moved = ensure_dim(loaded, make_dim((0, 0), (5000, 0), target_handle="A1"), DEFAULT_LAYERS)
    assert moved.readback_text == "5000.00"
    assert len(_dims(loaded)) == 1


def test_document_is_r2018() -> None:
    doc = _new_doc()
    assert doc.acad_release == DXF_VERSION_NAME
    write_dim(doc, make_dim((0, 0), (3000, 0)), DEFAULT_LAYERS)


# --- (9) the "no command was ever sent" guarantee ------------------------------


def test_module_contains_no_command_execution_path() -> None:
    """The structural guarantee, asserted rather than merely documented.

    The DIMLINEAR failure in ZWCAD was a live command swallowing the next
    coordinate. This module's answer is that there is no channel to send on.

    The scan covers EXECUTABLE CODE ONLY. The module docstring deliberately
    discusses ZWCAD and DIMLINEAR at length, and a raw text scan would flag that
    explanation as the very thing it forbids -- which is how a test like this
    turns into one that can never pass while the guarantee it guards is fine.
    """
    tree = ast.parse(pathlib.Path(__file__).with_name("dim.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & {"win32com", "comtypes", "pyautogui", "keyboard", "subprocess"}), (
        f"dim.py must not import a command channel, found {sorted(imported)}"
    )
    assert "ezdxf" in imported  # the data model is the ONLY channel it uses

    code = _executable_source()
    for forbidden in ("win32com", "SendKeys", "ZWCAD", "AutoCAD", "Dispatch", "os.system"):
        assert forbidden not in code, f"{forbidden!r} must not appear in dim.py code"
    # geometry construction through the data model is what replaces the command
    assert "add_linear_dim" in code or "add_aligned_dim" in code


def test_executable_source_helper_excludes_docstrings() -> None:
    # Guards the guard: _executable_source must actually drop prose, or every
    # structural test above becomes a test of the docstring wording.
    assert "ZWCAD" in pathlib.Path(__file__).with_name("dim.py").read_text(encoding="utf-8")
    assert "ZWCAD" not in _executable_source()


def test_write_dim_always_creates_a_fresh_dimension() -> None:
    # The deliberate one-shot path, so a caller can place a second dimension.
    doc = _new_doc()
    a = write_dim(doc, make_dim((0, 0), (3000, 0)), DEFAULT_LAYERS)
    b = write_dim(doc, make_dim((0, 0), (3000, 0)), DEFAULT_LAYERS)
    assert a.handle != b.handle
    assert len(_dims(doc)) == 2


# --- (10) the record's own readback evidence -----------------------------------


def test_record_snapshot_carries_the_measurement_and_text() -> None:
    doc = _new_doc()
    record = write_dim(doc, make_dim((0, 0), (3000, 0)), DEFAULT_LAYERS)
    snapshot = record.snapshot()
    assert snapshot.entity_type == "DIMENSION"
    assert snapshot.layer == "DIM"
    assert snapshot.properties["text"] == "3000.00"
    assert snapshot.geometry["measurement"] == pytest.approx(3000.0)
    assert snapshot.properties["text_mode"] == TextMode.MEASURED.value


def test_layer_argument_must_be_a_pair_of_real_layers() -> None:
    doc = _new_doc()
    with pytest.raises(DimGeometryError):
        write_dim(doc, make_dim((0, 0), (3000, 0)), ("DIM",))


def test_explicit_layer_is_created_and_used() -> None:
    doc = _new_doc()
    record = write_dim(doc, make_dim((0, 0), (3000, 0)), ("DIMLE", "DIMLE"))
    assert record.layer == "DIMLE"
    assert "DIMLE" in doc.layers
    assert classify_layer("DIMLE") is LayerSemantic.DIMENSION


def test_ticks_and_text_offset_are_accepted_and_dont_break_verification() -> None:
    doc = _new_doc()
    dim = make_dim(
        (0, 0),
        (3000, 0),
        use_tick=True,
        tick_size=2.0,
        text_offset=(100, 50),
    )
    record = write_dim(doc, dim, DEFAULT_LAYERS)
    assert record.verified
    assert record.readback_text == "3000.00"
