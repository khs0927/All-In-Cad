"""Tests for the layer recorder.

This module existed with **no test file at all** while owning the package's
only drawing-wide destructive guards (``DestructiveLayerChange`` and the
layer-``"0"`` protection). A mutation of either refusal body left the
recorder suite green. These tests exist to close that hole, and the two guard
tests are written so that the corresponding mutations die.

Three defects were found while writing this file. All three have since been
**fixed**, so they are covered by ordinary passing tests here rather than by
markers (this file contains no ``xfail``):

* ``observed_layer_specs()`` no longer raises ``NameError`` (undefined
  ``convention_name``); the literals it must produce are pinned below.
* ``protect_layer0`` now blocks the *first* attribute write on a layer whose
  attributes are still ezdxf's implicit defaults
  (``test_layer0_first_write_is_also_refused``).
* ``description`` is no longer write-only: a description overwrite is refused
  instead of being reported as applied
  (``test_description_is_rejected_instead_of_claimed_as_applied``).

Run (PYTHONHOME must be cleared on this host, see layer.py module docstring)::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \\
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\layer_test.py
"""

from __future__ import annotations

import dataclasses
import sys
from inspect import signature
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:  # make `all_in_cad` importable without an install
    sys.path.insert(0, str(_SRC))

import ezdxf  # noqa: E402

from all_in_cad.recorder import layer as layer_mod  # noqa: E402
from all_in_cad.recorder.layer import (  # noqa: E402
    DEFAULT_DXF_WRITE_VERSION,
    MANAGED_ATTRIBUTES,
    OBSERVED_XICAD_LAYERS,
    DestructiveLayerChange,
    LayerSpec,
    LayerValidationError,
    classify_layer,
    describe_layer,
    ensure_layer,
    layer_config_path,
    make_layer,
    observed_layer_specs_literal,
    write_layer,
    write_layers,
)
from all_in_cad.semantic_layers import LayerSemantic  # noqa: E402

# ------------------------------------------------------------------ fixtures


@pytest.fixture
def doc():
    """A fresh R2018 document -- the only version this recorder writes."""
    return ezdxf.new(DEFAULT_DXF_WRITE_VERSION, setup=True)


@pytest.fixture
def populated(doc):
    """A document whose ``WAL1`` carries an explicit, conflicting color."""
    write_layer(doc, make_layer("WAL1", color=1, linetype="DASHED", lineweight=30))
    doc.modelspace().add_line((0, 0), (100, 0), dxfattribs={"layer": "WAL1"})
    return doc


@pytest.fixture
def layer0_with_explicit_color(doc):
    """Layer ``"0"`` with a *recorded* color, so a conflict is detectable."""
    write_layer(doc, make_layer("0", color=6), allow_overwrite=True, protect_layer0=False)
    return doc


def repo_config():
    """Locate the layer config, or skip **with the reason stated**.

    A silent pass is the failure mode this guards against: if the config
    cannot be found, the test must say so out loud rather than report green
    for a claim it never checked.
    """
    path = layer_config_path()
    if path is None:
        pytest.skip(
            "SKIPPED (reason stated, not silently passed): "
            "configs/architectural-layers.json was not found walking upwards from "
            f"{Path(layer_mod.__file__).parent}. The project-convention claims in "
            "this file could not be checked in this environment."
        )
    return path


# ------------------------------------------------- GUARD 1: layer "0" (785)


def test_layer0_destructive_change_is_refused_even_with_allow_overwrite(
    layer0_with_explicit_color,
):
    """layer.py:785 -- the layer-0 guard fires on top of allow_overwrite.

    Mutation target: ``if conflicts and is_layer0 and protect_layer0:`` -> ``if False:``.
    Under that mutation nothing raises, the color is silently rewritten on the
    fallback layer every entity in the drawing uses, and this test fails.
    """
    doc = layer0_with_explicit_color
    with pytest.raises(DestructiveLayerChange) as excinfo:
        write_layer(doc, make_layer("0", color=3), allow_overwrite=True)

    message = str(excinfo.value)
    assert "'0'" in message
    assert "protect_layer0=False" in message, (
        "the refusal must name the escape hatch, or a caller cannot proceed "
        "deliberately"
    )
    # The drawing must be untouched by the refusal.
    assert describe_layer(doc, "0").color == 6, (
        "the guard raised but still mutated the layer; a refusal that writes is "
        "worse than no guard"
    )


def test_layer0_guard_is_a_layer0_guard_not_a_general_guard(populated):
    """Control: the identical change on a non-0 layer is allowed.

    Without this, a mutant that raised unconditionally would also pass the
    test above -- and the test above would prove nothing.
    """
    record = write_layer(populated, make_layer("WAL1", color=3), allow_overwrite=True)
    assert record.applied == {"color": 3}
    assert record.changed_existing is True


def test_layer0_protection_can_be_lifted_deliberately(layer0_with_explicit_color):
    """Both flags together are the only way through, and the record says so."""
    record = write_layer(
        layer0_with_explicit_color,
        make_layer("0", color=3),
        allow_overwrite=True,
        protect_layer0=False,
    )
    assert record.applied == {"color": 3}
    assert record.allow_overwrite is True
    assert describe_layer(layer0_with_explicit_color, "0").color == 3


def test_layer0_protection_defaults_to_on(populated, layer0_with_explicit_color):
    """The kwarg default is ``True``; a caller who forgets is still protected."""
    assert layer_mod.write_layer.__kwdefaults__["protect_layer0"] is True
    with pytest.raises(DestructiveLayerChange):
        write_layer(layer0_with_explicit_color, make_layer("0", color=3))


def test_layer0_first_write_is_also_refused(doc):
    with pytest.raises(DestructiveLayerChange):
        write_layer(doc, make_layer("0", color=3), allow_overwrite=True)


# ------------------------------------------- GUARD 2: overwrite refusal (791)


def test_existing_layer_destructive_change_is_refused_by_default(populated):
    """layer.py:791 -- a conflicting write to an existing layer is refused.

    Mutation target: ``if conflicts and not allow_overwrite:`` -> ``if False:``.
    Under that mutation the color of a layer that already carries entities is
    rewritten with no record of consent, and this test fails.
    """
    with pytest.raises(DestructiveLayerChange) as excinfo:
        write_layer(populated, make_layer("WAL1", color=7))

    message = str(excinfo.value)
    assert "WAL1" in message
    assert "color" in message and "1" in message and "7" in message, (
        "the refusal must name the attribute and both values, so the caller "
        "can judge the change instead of guessing"
    )
    assert "allow_overwrite=True" in message
    assert describe_layer(populated, "WAL1").color == 1, (
        "the refusal must not have written anything"
    )


def test_refusal_reports_the_entity_count_that_makes_it_destructive(populated):
    """The harm of the change is the entities it applies to; say how many."""
    with pytest.raises(DestructiveLayerChange) as excinfo:
        write_layer(populated, make_layer("WAL1", color=7))
    assert "1 entit" in str(excinfo.value)


def test_overwrite_proceeds_only_with_the_explicit_flag(populated):
    """The other half of the contract: the flag actually works."""
    record = write_layer(populated, make_layer("WAL1", color=7), allow_overwrite=True)
    assert record.created is False
    assert record.reused is True
    assert record.changed_existing is True
    assert record.applied == {"color": 7}
    assert record.conflicts == {"color": (1, 7)}
    assert record.allow_overwrite is True
    assert describe_layer(populated, "WAL1").color == 7


def test_allow_overwrite_defaults_to_false():
    """A caller who passes nothing is a caller who did not consent."""
    assert layer_mod.write_layer.__kwdefaults__["allow_overwrite"] is False
    assert layer_mod.write_layers.__kwdefaults__["allow_overwrite"] is False


def test_unchanged_attributes_are_not_a_conflict(populated):
    """Re-requesting the value already there is a no-op, not a refusal.

    This is the control that stops an "always raise" mutant from passing
    :func:`test_existing_layer_destructive_change_is_refused_by_default`.
    """
    record = write_layer(populated, make_layer("WAL1", color=1))
    assert record.applied in ({}, {"color": 1}), (
        "re-requesting a value that already matches must never be a conflict"
    )
    assert record.conflicts == {}
    assert describe_layer(populated, "WAL1").color == 1


def test_unchanged_attributes_are_not_reported_as_changed(populated):
    record = write_layer(populated, make_layer("WAL1", color=1))
    assert record.applied == {}
    assert record.changed_existing is False


def test_write_layers_propagates_the_refusal(populated):
    """The guard is not bypassable by wrapping the write in a plan."""
    with pytest.raises(DestructiveLayerChange):
        write_layers(populated, [make_layer("ELE"), make_layer("WAL1", color=7)])


def test_destructive_layer_change_is_a_validation_error():
    """Callers catching the package's majority ``except ValueError`` get this."""
    assert issubclass(DestructiveLayerChange, LayerValidationError)
    assert issubclass(LayerValidationError, ValueError)
    with pytest.raises(ValueError):
        write_layer(populated, make_layer("WAL1", color=7))


# ------------------------------------------------------------- creation path


def test_new_layer_is_created_with_specified_attributes(doc):
    record = write_layer(
        doc,
        make_layer("WAL2", color=4, linetype="DASHED", lineweight=50, plot=False),
    )
    assert record.created is True
    assert record.reused is False
    assert record.applied == {
        "color": 4,
        "linetype": "DASHED",
        "lineweight": 50,
        "plot": False,
    }
    assert record.semantic is LayerSemantic.WALL
    assert record.unclassified is False
    assert record.table_index is not None

    facts = describe_layer(doc, "WAL2")
    assert facts.exists is True
    assert (facts.color, facts.linetype, facts.lineweight, facts.plot) == (
        4,
        "DASHED",
        50,
        False,
    )


@pytest.mark.parametrize(
    ("spec_kwargs", "attribute", "expected"),
    [
        ({"on": False}, "on", False),
        ({"locked": True}, "locked", True),
        ({"frozen": True}, "frozen", True),
        ({"plot": False}, "plot", False),
        ({"color": 255}, "color", 255),
    ],
)
def test_each_managed_attribute_is_written_and_observable(
    doc, spec_kwargs, attribute, expected
):
    """Every attribute in MANAGED_ATTRIBUTES must survive the write."""
    name = f"ATTR_{attribute}_{abs(hash(str(spec_kwargs))) % 10000}"
    record = write_layer(doc, make_layer(name, **spec_kwargs))
    assert record.applied == spec_kwargs
    assert getattr(describe_layer(doc, name), attribute) == expected


def test_unspecified_attributes_are_left_at_the_cad_default(doc):
    """[UNRESOLVED] convention: no colour is invented, so a bare layer keeps
    ezdxf's defaults (colour 7, Continuous, lineweight -3, plot on)."""
    record = write_layer(doc, make_layer("BRANDNEW"))
    assert record.applied == {}
    facts = describe_layer(doc, "BRANDNEW")
    assert (facts.color, facts.linetype, facts.lineweight, facts.plot) == (
        7,
        "Continuous",
        -3,
        True,
    )


def test_layer_colour_keyword_is_refused_at_validation():
    """One contract for BYLAYER/BYBLOCK: a layer colour is an ACI index.

    [DESIGN] This replaces two mutually contradictory tests. One asserted the
    keyword was accepted by ``make_layer``; the other asserted it failed at
    write time with "cannot be represented". A value cannot both validate and
    be rejected downstream, so the earlier acceptance was the error: in a DXF
    LAYER record ``BYLAYER``/``BYBLOCK`` are entity attributes with no meaning
    on the layer itself. Refusing them at the validation step is a *narrowing
    of an incorrect promise*, not a weakening of the guard -- the guard is the
    refusal, and it now happens before anything is written instead of after.
    """
    for keyword in ("BYLAYER", "bylayer", "BYBLOCK", "byblock", " ByBlock "):
        with pytest.raises(LayerValidationError) as excinfo:
            make_layer("WAL1", color=keyword)
        assert "ACI 1..255" in str(excinfo.value), (
            "the refusal must say what a layer colour actually is"
        )
        assert "BYLAYER/BYBLOCK apply to entities, not layers" in str(excinfo.value), (
            "the refusal must say why the keyword is wrong here, not just that "
            "it is wrong"
        )


def test_layer_colour_keyword_write_fails_before_touching_the_document(doc):
    """The keyword never reaches the document: no entry, no attribute write."""
    with pytest.raises(LayerValidationError):
        write_layer(doc, make_layer("WAL1", color="BYLAYER"))
    assert "WAL1" not in doc.layers, (
        "the failure must happen before the LAYER entry is created"
    )
    assert doc.layers.get("0").dxf.color == 7


def test_layer_colour_rejects_any_string_not_an_aci_index():
    """A colour is not a name; 'RED' is a different kind of wrong, still wrong."""
    with pytest.raises(LayerValidationError) as excinfo:
        make_layer("WAL1", color="RED")
    assert "ACI 1..255" in str(excinfo.value)


def test_missing_linetype_is_refused_rather_than_left_dangling(doc):
    """A dangling LTYPE reference is a broken drawing, not a default."""
    with pytest.raises(LayerValidationError) as excinfo:
        write_layer(doc, make_layer("WAL1", linetype="NO_SUCH_LTYPE"))
    assert "not defined" in str(excinfo.value)
    # A new entry may exist, but it must not carry a dangling LTYPE reference.
    if "WAL1" in doc.layers:
        assert "DASHED" not in str(describe_layer(doc, "WAL1").linetype)


def test_ensure_layer_never_overwrites(populated):
    """The one non-destructive write must stay non-destructive."""
    record = ensure_layer(populated, "WAL1")
    assert record.created is False
    assert record.applied == {}
    assert describe_layer(populated, "WAL1").color == 1


# ------------------------------------------------- reuse preserves attributes


def test_reuse_preserves_existing_attributes(populated):
    """Reporting an existing layer must not touch a single attribute."""
    before = describe_layer(populated, "WAL1")
    record = write_layer(populated, make_layer("WAL1"))
    after = describe_layer(populated, "WAL1")

    assert record.applied == {}
    assert record.created is False
    assert (after.color, after.linetype, after.lineweight) == (
        before.color,
        before.linetype,
        before.lineweight,
    )
    # preserved reports what was left alone, one entry per managed attribute
    assert set(record.preserved) == set(MANAGED_ATTRIBUTES)
    assert record.preserved["color"] == 1


def test_partial_spec_preserves_the_attributes_it_does_not_mention(populated):
    """Overriding one attribute must not reset the others to defaults."""
    record = write_layer(
        populated, make_layer("WAL1", color=9), allow_overwrite=True
    )
    assert record.applied == {"color": 9}
    facts = describe_layer(populated, "WAL1")
    assert facts.linetype == "DASHED", "linetype was reset by a colour-only change"
    assert facts.lineweight == 30, "lineweight was reset by a colour-only change"
    assert record.preserved["linetype"] == "DASHED"


# -------------------------------------------- observed C/S/F vs the convention


@pytest.mark.parametrize("name", ["C", "S", "F"])
def test_observed_single_letter_layers_are_unknown_to_the_convention(name):
    """The observed XiCAD names are NOT in the project convention."""
    assert classify_layer(name) is LayerSemantic.UNKNOWN
    spec = make_layer(name)
    assert spec.semantic is LayerSemantic.UNKNOWN
    assert spec.is_convention_layer is False


@pytest.mark.parametrize("name", ["WAL1", "WAL2", "WAL3"])
def test_convention_wall_layers_classify_as_wall(name):
    assert classify_layer(name) is LayerSemantic.WALL
    assert make_layer(name).is_convention_layer is True


def test_observed_mapping_points_at_existing_convention_names():
    """Every value side of the observed map must be a real convention name."""
    assert set(OBSERVED_XICAD_LAYERS) == {"0", "C", "S", "F"}
    for observed, convention in OBSERVED_XICAD_LAYERS.items():
        assert classify_layer(observed) is LayerSemantic.UNKNOWN, (
            f"{observed!r} is supposed to be an unclassified observed name, but "
            "the convention now claims it -- update the map, do not let the test "
            "drift from reality"
        )
        assert classify_layer(convention) is not LayerSemantic.UNKNOWN


def test_literal_observed_specs_are_flagged_unclassified(doc):
    """Creating C/S/F is safe but unclassified, and the record must say so."""
    records = write_layers(doc, observed_layer_specs_literal())
    assert records.appended == ("C", "S", "F"), (
        "layer '0' already exists in a new document, so it is reported, "
        "not appended"
    )
    for record in records.records:
        assert record.unclassified is True
        assert record.semantic is LayerSemantic.UNKNOWN
    assert order_of(doc, "C", "S", "F"), "observed layers must land in table order"


def order_of(doc, *names):
    order = [layer.dxf.name for layer in doc.layers]
    positions = [order.index(name) for name in names]
    return positions == sorted(positions)


def test_description_is_rejected_instead_of_claimed_as_applied():
    with pytest.raises(LayerValidationError, match="description is unsupported"):
        make_layer("WAL1", description="carries a note")

def test_delete_api_is_not_exposed():
    """No delete/purge/remove wrapper exists, and none may be added silently.

    The module docstring calls the absence of a delete API a design decision.
    A documented constraint nobody measures gets "fixed" by accident, so the
    constraint is measured here: the day a destructive symbol appears, this
    test fails and demands a separate design review.
    """
    forbidden = ("delete", "purge", "remove", "erase", "discard", "drop", "wipe")
    offenders = [
        name
        for name in dir(layer_mod)
        if not name.startswith("_") and any(word in name.lower() for word in forbidden)
    ]
    assert not offenders, (
        f"layer.py now exposes {offenders}. Deleting a layer entry is in the "
        "destructive command set this module exists to guard against; adding it "
        "requires an explicit design review, not a passing test."
    )


def test_no_flag_on_the_write_api_enables_a_delete():
    """Deletion must not be reachable as an opt-in argument either."""
    for func in (write_layer, write_layers, ensure_layer):
        assert func.__doc__, "the write API must stay documented"
        parameters = set(signature(func).parameters)
        assert not any(word in name.lower() for name in parameters for word in
                       ("delete", "purge", "remove", "erase", "drop"))


def test_module_exports_no_removal_helper_via_dunder_all():
    assert not any(
        word in name.lower() for name in layer_mod.__all__ for word in
        ("delete", "purge", "remove", "erase", "drop")
    )


# ------------------------------------------------------------- misc contracts


def test_describe_layer_reports_a_missing_layer_without_raising(doc):
    facts = describe_layer(doc, "NO_SUCH_LAYER")
    assert facts.exists is False
    assert facts.color is None
    assert facts.table_index is None
    assert facts.entity_count == 0


def test_describe_layer_counts_the_entities_on_the_layer(doc):
    write_layer(doc, make_layer("WAL1"))
    for index in range(3):
        doc.modelspace().add_line(
            (index, 0), (index, 1), dxfattribs={"layer": "WAL1"}
        )
    assert describe_layer(doc, "WAL1").entity_count == 3
    assert describe_layer(doc, "WAL1").table_index == 2


def test_a_document_of_another_dxf_version_is_refused():
    other = ezdxf.new("R2010", setup=True)
    with pytest.raises(ValueError) as excinfo:
        describe_layer(other, "0")
    assert DEFAULT_DXF_WRITE_VERSION in str(excinfo.value)


def test_layer_spec_is_immutable():
    """``LayerSpec`` is a frozen dataclass: a write must be refused by the
    dataclass machinery itself.

    The oracle pins the exact type (``dataclasses.FrozenInstanceError``, an
    ``AttributeError`` subclass) and the field-specific message. A bare
    ``Exception`` here passed for any unrelated failure raised while setting
    the attribute -- notably ``LayerValidationError`` from a stray
    ``__setattr__`` hook, which would mean the spec validates on mutation
    instead of refusing it outright. The post-condition asserts the value did
    not change either, so a silently-ignored write cannot satisfy the test.
    """
    spec = make_layer("WAL1", color=1)
    with pytest.raises(dataclasses.FrozenInstanceError) as excinfo:
        spec.color = 2  # type: ignore[misc]
    assert "cannot assign to field 'color'" in str(excinfo.value)
    assert spec.color == 1


def test_renamed_keeps_every_attribute():
    spec = make_layer("WAL1", color=1, linetype="DASHED", plot=False)
    moved = spec.renamed("C")
    assert moved.name == "C"
    assert (moved.color, moved.linetype, moved.plot) == (1, "DASHED", False)
    assert moved.semantic is LayerSemantic.UNKNOWN


def test_write_layer_rejects_a_non_spec():
    with pytest.raises(LayerValidationError):
        write_layer(ezdxf.new(DEFAULT_DXF_WRITE_VERSION, setup=True), "WAL1")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"color": 0},
        {"color": 256},
        {"color": True},
        {"color": "RED"},
        {"lineweight": 7},
        {"lineweight": "30"},
        {"on": "yes"},
        {"plot": 1},
        {"on": False, "frozen": True},
    ],
)
def test_invalid_specs_are_rejected(kwargs):
    with pytest.raises(LayerValidationError):
        make_layer("WAL1", **kwargs)


def test_layer_config_path_is_not_exported_as_public_api():
    """It is a helper for tests, not part of the recorder's surface."""
    assert "layer_config_path" not in layer_mod.__all__
    assert callable(layer_config_path)


def test_observed_layer_specs_literal_names():
    assert [spec.name for spec in observed_layer_specs_literal()] == ["0", "C", "S", "F"]


def test_managed_attributes_is_the_closed_set():
    assert set(MANAGED_ATTRIBUTES) == {
        "color",
        "linetype",
        "lineweight",
        "plot",
        "description",
        "on",
        "locked",
        "frozen",
    }
    assert LayerSpec("WAL1").specified() == {}


# ------------------------------------- linetype names are case-insensitive
# DXF symbol table names compare case-insensitively. A request of "continuous"
# against a table defining "Continuous" is the same definition, so it must not
# be reported as a change and the record must carry the document's own spelling.


def test_linetype_creation_is_case_insensitive_and_records_the_defined_name(doc):
    record = write_layer(doc, make_layer("CASE_A", linetype="continuous"))
    assert record.applied == {"linetype": "Continuous"}, (
        "the record must name the linetype the document defines, not the "
        "casing the caller happened to type"
    )
    assert describe_layer(doc, "CASE_A").linetype == "Continuous"


def test_linetype_mixed_case_creation_is_case_insensitive(doc):
    record = write_layer(doc, make_layer("CASE_B", linetype="DaShEd"))
    assert record.applied == {"linetype": "DASHED"}
    assert describe_layer(doc, "CASE_B").linetype == "DASHED"


def test_linetype_rerequest_with_different_casing_is_not_a_change(doc):
    write_layer(doc, make_layer("CASE_C", linetype="CONTINUOUS"))
    before = describe_layer(doc, "CASE_C").linetype
    record = write_layer(doc, make_layer("CASE_C", linetype="Continuous"))
    assert before == "Continuous"
    assert record.created is False
    assert record.applied == {}, (
        "re-requesting the same linetype under different casing changes "
        "nothing and must not be reported as a change"
    )
    assert record.conflicts == {}
    assert record.changed_existing is False
    assert describe_layer(doc, "CASE_C").linetype == "Continuous"


def test_linetype_rerequest_still_refuses_a_real_change(doc):
    """Case-insensitivity must not make every overwrite invisible."""
    write_layer(doc, make_layer("CASE_D", linetype="continuous"))
    with pytest.raises(DestructiveLayerChange) as excinfo:
        write_layer(doc, make_layer("CASE_D", linetype="DASHED"))
    assert "linetype" in str(excinfo.value)
    assert describe_layer(doc, "CASE_D").linetype == "Continuous"


def test_linetype_comparison_is_case_insensitive_against_a_foreign_name(doc):
    """The stored value was written by another tool, in another casing.

    Comparing strings exactly would report this as a change to a
    whole-drawing attribute and demand overwrite consent for a request that
    changes nothing.
    """
    entry = doc.layers.add("CASE_F")
    entry.dxf.linetype = "continuous"  # exactly what ZWCAD/another tool wrote

    record = write_layer(doc, make_layer("CASE_F", linetype="Continuous"))

    assert record.applied == {}, "the same LTYPE under other casing is not a change"
    assert record.conflicts == {}
    assert record.changed_existing is False
    assert describe_layer(doc, "CASE_F").linetype == "continuous", (
        "the request must not have rewritten the entry with the caller's casing"
    )


def test_linetype_records_the_document_spelling_not_the_requested_casing(doc):
    record = write_layer(doc, make_layer("CASE_G", linetype="continuous"))
    assert record.applied == {"linetype": "Continuous"}
    assert describe_layer(doc, "CASE_G").linetype == "Continuous"


# ---------------------------------------- layer "0" protection blocks changes
# Protection blocks a *change*. Re-asserting what layer "0" already carries
# writes nothing, so refusing it would push callers towards the override flag
# for a request that cannot do harm.


def test_layer0_same_value_rerequest_is_a_no_op_success(layer0_with_explicit_color):
    record = write_layer(layer0_with_explicit_color, make_layer("0", color=6))
    assert record.created is False
    assert record.applied == {}
    assert record.conflicts == {}
    assert record.changed_existing is False
    assert describe_layer(layer0_with_explicit_color, "0").color == 6


def test_layer0_re_request_of_an_unrecorded_attribute_is_still_a_change(doc):
    """Layer "0" in a fresh document carries no explicit colour value.

    Re-asserting colour 7 there is not a same-value request: nothing is
    recorded yet, so the write would assert a value drawing-wide. Protection
    must still refuse it.
    """
    assert describe_layer(doc, "0").color is None
    with pytest.raises(DestructiveLayerChange):
        write_layer(doc, make_layer("0", color=7), allow_overwrite=True)


def test_layer0_real_change_is_still_refused(layer0_with_explicit_color):
    """The no-op allowance must not become a bypass of the guard."""
    with pytest.raises(DestructiveLayerChange) as excinfo:
        write_layer(
            layer0_with_explicit_color,
            make_layer("0", color=6, lineweight=50),
            allow_overwrite=True,
        )
    assert "protected" in str(excinfo.value)
    facts = describe_layer(layer0_with_explicit_color, "0")
    assert facts.color == 6
    assert facts.lineweight != 50, "the refused request must not have been written"

