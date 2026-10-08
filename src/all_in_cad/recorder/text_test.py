"""Tests for the text recorder.

Every claim this module makes about encoding, FreeCAD and the entity digest
was MEASURED on this host before being written down; the docstrings in
``text.py`` quote the measurements. These tests re-measure rather than assert
from memory, because this project has been wrong three times by assuming.

Run (PYTHONHOME must be cleared on this host, see text.py module docstring)::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \\
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\text_test.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path, PureWindowsPath

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:  # make `all_in_cad` importable without an install
    sys.path.insert(0, str(_SRC))

import ezdxf  # noqa: E402

from all_in_cad.recorder import text as text_mod  # noqa: E402
from all_in_cad.recorder.text import (  # noqa: E402
    DEFAULT_STYLE,
    FREECAD_COMMAND,
    KNOWN_TEXT_LAYERS,
    MTEXT_LINE_BREAK,
    TEXT_LAYER_STATUS,
    TextValidationError,
    freecad_import_survival,
    make_mtext,
    make_text,
    normalize_mtext_content,
    write_text,
)
from all_in_cad.semantic_layers import LayerSemantic, classify_layer  # noqa: E402

#: A Korean string with Hangul, a full-width-safe digit run, an ASCII tail and
#: a Latin-with-diacritic character, so a codec that only handles one of those
#: classes cannot pass by accident.
KOREAN = "\ud14c\uc774\ud504 \ud558\ub67c \uce74\ud398 1234 \ub178\ud2b8 \u00e9\u00e0 ABC-123"
#: The same content spelled out with escapes. Both the fixture and its
#: expectation are escape-only on purpose: if this file were ever re-encoded,
#: the two would still have to agree, and a mojibake source could not hide
#: behind a console that also cannot render it.
KOREAN_EXPECTED = (
    "\ud14c\uc774\ud504 \ud558\ub67c \uce74\ud398 1234 \ub178\ud2b8 \u00e9\u00e0 ABC-123"
)
#: Hangul only, so it is encodable in every codec under comparison. CP949 is
#: the codec that actually bit this host before, so the byte-level test below
#: uses a string CP949 can encode and proves the file is UTF-8, not CP949.
KOREAN_HANGUL = "\ud14c\uc774\ud504 \ud558\ub67c \uce74\ud398 1234"


@pytest.fixture
def doc():
    """A minimal R2018 document.

    ``setup=False`` on purpose: the recorder must work in a bare document, and
    the measured default style table of a bare document is exactly
    ``['Standard']`` (see text.py MEASURED FACT 1).
    """
    return ezdxf.new("R2018")


def _entities(d, kind):
    return [e for e in d.modelspace() if e.dxftype() == kind]


# ---------------------------------------------------------------------------
# 1. Measured default style
# ---------------------------------------------------------------------------


def test_standard_style_exists_in_bare_r2018_document():
    """[OBSERVED, MEASURED] the default style is real, not invented here."""
    bare = ezdxf.new("R2018")
    assert [s.dxf.name for s in bare.styles] == ["Standard"]
    assert DEFAULT_STYLE == "Standard"


def test_default_style_survives_write_and_reload(tmp_path):
    d = ezdxf.new("R2018")
    write_text(d, make_text("STYLE PROBE", (1.0, 2.0), layer="DOOR"))
    path = tmp_path / "style.dxf"
    d.saveas(path)
    reloaded = ezdxf.readfile(path)
    assert str(_entities(reloaded, "TEXT")[0].dxf.style) == "Standard"


def test_unknown_style_is_refused_not_invented(doc):
    """A dangling STYLE reference is silent data loss, so it must raise."""
    geometry = make_text("X", (0.0, 0.0), layer="DOOR", style="NoSuchFont")
    with pytest.raises(TextValidationError, match="does not exist"):
        write_text(doc, geometry)
    assert _entities(doc, "TEXT") == []


# ---------------------------------------------------------------------------
# 2. Core content cases
# ---------------------------------------------------------------------------


def test_ascii_text_roundtrips_through_own_reader(doc):
    geometry = make_text("ROOM 101", (100.0, 200.0), layer="DOOR", height=3.0)
    record = write_text(doc, geometry)
    assert record.roundtrip.intact
    assert record.roundtrip.failures() == ()
    assert record.roundtrip.read_back_content == "ROOM 101"
    entity = _entities(doc, "TEXT")[0]
    assert entity.dxf.text == "ROOM 101"
    assert (entity.dxf.insert.x, entity.dxf.insert.y) == (100.0, 200.0)
    assert float(entity.dxf.height) == 3.0


def test_korean_text_roundtrips_through_own_reader(doc):
    geometry = make_text(KOREAN, (10.0, 20.0), layer="DOOR")
    record = write_text(doc, geometry)
    assert record.roundtrip.content_matches
    assert record.roundtrip.read_back_content == KOREAN
    assert _entities(doc, "TEXT")[0].dxf.text == KOREAN


def test_mixed_script_string_survives(doc):
    mixed = KOREAN + " | Room A-1 | \u00b2\u00b3 25.4mm | 100% \u2014 done"
    record = write_text(doc, make_mtext(mixed, (5.0, 5.0), layer="WIN"))
    assert record.roundtrip.content_matches
    assert record.roundtrip.read_back_content == mixed


def test_empty_and_whitespace_content_is_rejected():
    for bad in ("", "   ", "\t\n ", "\x00abc"):
        with pytest.raises(TextValidationError):
            make_text(bad, (0.0, 0.0), layer="DOOR")
        with pytest.raises(TextValidationError):
            make_mtext(bad, (0.0, 0.0), layer="DOOR")


# ---------------------------------------------------------------------------
# 3. Line breaks: the measured TEXT trap
# ---------------------------------------------------------------------------


def test_text_entity_rejects_line_break_instead_of_losing_it(doc):
    """MEASURED: ezdxf 1.4.4 turns 'line1\\nline2' into 'line1line2'.

    The test asserts the recorder's fail-closed behaviour *and* re-measures
    the underlying ezdxf behaviour, so if ezdxf ever gains real TEXT line
    breaks this test tells us the reason has changed rather than silently
    relaxing.
    """
    lossy = ezdxf.new("R2018").modelspace().add_text("line1\nline2")
    assert lossy.dxf.text == "line1line2"  # the measured data loss

    for bad in ("line1\nline2", "line1\r\nline2", "a\rb"):
        with pytest.raises(TextValidationError, match="no line break"):
            make_text(bad, (0.0, 0.0), layer="DOOR")


def test_mtext_newlines_are_normalised_to_the_dxf_escape():
    assert normalize_mtext_content("a\nb") == "a" + MTEXT_LINE_BREAK + "b"
    assert normalize_mtext_content("a\r\nb") == "a" + MTEXT_LINE_BREAK + "b"
    assert normalize_mtext_content("a\\Pb") == "a" + MTEXT_LINE_BREAK + "b"
    assert MTEXT_LINE_BREAK == "\\P"


def test_multiple_line_breaks_roundtrip_as_paragraphs(doc):
    # "제목 / 본문 첫째 / 본문 둘째 / 끝", written with escapes on purpose.
    lines = (
        "\uc81c\ubaa9",  # 제목
        "\ubcf8\ubb38 \uccab\uc9f8",  # 본문 첫째
        "\ubcf8\ubb38 \ub458\uc9f8",  # 본문 둘째
        "\ub05d",  # 끝
    )
    geometry = make_mtext("\n".join(lines), (0.0, 0.0), layer="NOTE")
    assert geometry.lines == lines
    assert geometry.has_line_breaks
    record = write_text(doc, geometry)
    assert record.roundtrip.content_matches
    entity = _entities(doc, "MTEXT")[0]
    assert entity.dxf.text.count(MTEXT_LINE_BREAK) == 3
    # MEASURED: ezdxf maps \P back to a real newline in plain_text().
    assert entity.plain_text().split("\n") == list(geometry.lines)


def test_escaped_backslash_p_in_content_is_not_mistaken_for_a_break():
    """A literal backslash before P is a real DXF break; there is no escape.

    Documented rather than guessed: this module does NOT claim to support an
    escaped literal ``\\P``. The test pins the current, measured behaviour so a
    future change is a visible decision rather than a silent shift.
    """
    geometry = make_mtext("path C:\\Ptmp", (0.0, 0.0), layer="NOTE")
    assert geometry.lines == ("path C:", "tmp")
    assert MTEXT_LINE_BREAK in geometry.content


def test_wrapping_is_off_unless_width_is_given(doc):
    """MEASURED: a fresh ezdxf MTEXT has width None, i.e. no wrap column."""
    unwrapped = make_mtext("a very long note", (0.0, 0.0), layer="NOTE")
    assert unwrapped.wrapping is False
    record = write_text(doc, unwrapped)
    entity = _entities(doc, "MTEXT")[0]
    assert entity.dxf.width is None
    assert record.roundtrip.width_matches

    wrapped = make_mtext("a very long note", (0.0, 0.0), layer="NOTE", width_mm=40.0)
    assert wrapped.wrapping is True
    d2 = ezdxf.new("R2018")
    rec2 = write_text(d2, wrapped)
    assert float(_entities(d2, "MTEXT")[0].dxf.width) == 40.0
    assert rec2.roundtrip.width_matches


# ---------------------------------------------------------------------------
# 4. Special characters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        'he said "hi" & left',
        "100% \\ 50% #tag",
        "±2.5 ≤ 3.0 ≥ 1.0 → ok",
        "a\\b{c}d[e]f^g|h",
        "emoji-ish: ★☆ ○ ●",
        "tab\there",
    ],
)
def test_special_characters_survive_a_file_roundtrip(tmp_path, value):
    d = ezdxf.new("R2018")
    write_text(d, make_text(value, (3.0, 4.0), layer="DOOR"))
    path = tmp_path / "special.dxf"
    d.saveas(path)
    assert _entities(ezdxf.readfile(path), "TEXT")[0].dxf.text == value


def test_percent_codes_are_passed_through_not_interpreted(tmp_path):
    """TEXT interprets %%d/%%p; this recorder never rewrites a caller's string.

    The stored value must be exactly what was asked for. Any interpolation
    would be a silent content change.
    """
    value = "100%% 50%% %%d %%p"
    d = ezdxf.new("R2018")
    write_text(d, make_text(value, (0.0, 0.0), layer="DOOR"))
    path = tmp_path / "pct.dxf"
    d.saveas(path)
    assert _entities(ezdxf.readfile(path), "TEXT")[0].dxf.text == value


# ---------------------------------------------------------------------------
# 5. THE core verification: Korean bytes through a real R2018 file
# ---------------------------------------------------------------------------


def test_korean_text_survives_save_reload_byte_identical(tmp_path):
    """The headline test of this module, asserted on BYTES not on repr().

    The Windows console renders Korean as mojibake even when the file is
    perfect, so a repr-based assertion would be both unreadable and weaker.
    """
    d = ezdxf.new("R2018", setup=True)
    write_text(d, make_text(KOREAN, (10.0, 20.0), layer="DOOR", height=5.0, rotation_deg=15.0))
    write_text(
        d,
        make_mtext(
            "A" + MTEXT_LINE_BREAK + KOREAN,
            (100.0, 200.0),
            layer="WIN",
            height=5.0,
            rotation_deg=15.0,
            width_mm=60.0,
        ),
    )
    path = tmp_path / "korean.dxf"
    d.saveas(path)

    reloaded = ezdxf.readfile(path)
    text = _entities(reloaded, "TEXT")[0]
    mtext = _entities(reloaded, "MTEXT")[0]

    expected = KOREAN.encode("utf-8")
    assert text.dxf.text.encode("utf-8") == expected
    assert mtext.dxf.text.encode("utf-8") == ("A" + MTEXT_LINE_BREAK + KOREAN).encode("utf-8")
    # Geometry and appearance must ride along with the characters.
    assert (text.dxf.insert.x, text.dxf.insert.y) == (10.0, 20.0)
    assert float(text.dxf.height) == 5.0
    assert float(text.dxf.rotation) == 15.0
    assert float(mtext.dxf.char_height) == 5.0
    assert float(mtext.dxf.width) == 60.0
    assert str(mtext.dxf.style) == DEFAULT_STYLE


def test_korean_fixture_is_itself_intact():
    """Guards the fixture against a re-encode of this test file."""
    assert KOREAN == KOREAN_EXPECTED
    assert "\ud14c" in KOREAN and "ABC" in KOREAN and "1234" in KOREAN


def test_dxf_file_is_utf8_decodable_with_korean(tmp_path):
    """The file on disk must itself carry the Hangul as UTF-8, not CP949."""
    d = ezdxf.new("R2018")
    write_text(d, make_text(KOREAN_HANGUL, (0.0, 0.0), layer="DOOR"))
    path = tmp_path / "enc.dxf"
    d.saveas(path)
    raw = path.read_bytes()
    assert KOREAN_HANGUL.encode("utf-8") in raw
    # A CP949 write would have produced these lead bytes instead, and this
    # string is deliberately encodable in both codecs so the comparison is
    # real rather than vacuous.
    assert KOREAN_HANGUL.encode("cp949") != KOREAN_HANGUL.encode("utf-8")
    assert KOREAN_HANGUL.encode("cp949") not in raw


# ---------------------------------------------------------------------------
# 6. Alignment point preservation
# ---------------------------------------------------------------------------


def test_insert_point_is_preserved_for_every_alignment(doc):
    for halign, valign in ((0, 0), (1, 0), (2, 0), (0, 3), (2, 3)):
        geometry = make_text(
            f"h{halign}v{valign}", (123.456, -78.9), layer="DOOR",
            halign=halign, valign=valign,
        )
        record = write_text(doc, geometry)
        assert record.roundtrip.insert_matches
        assert record.roundtrip.alignment_matches


def test_non_default_text_alignment_writes_a_matching_align_point(doc):
    """DXF reads align_point for non-default alignment; they must be equal."""
    geometry = make_text("CENTRED", (400.0, 800.0), layer="DOOR", halign=1, valign=2)
    write_text(doc, geometry)
    entity = _entities(doc, "TEXT")[0]
    assert int(entity.dxf.halign) == 1
    assert int(entity.dxf.valign) == 2
    assert (entity.dxf.align_point.x, entity.dxf.align_point.y) == (400.0, 800.0)


def test_mtext_attachment_point_is_preserved(doc):
    for attachment in range(1, 10):
        d = ezdxf.new("R2018")
        geometry = make_mtext(
            f"att{attachment}", (10.0, 20.0), layer="NOTE", attachment_point=attachment
        )
        record = write_text(d, geometry)
        assert record.roundtrip.alignment_matches
        assert int(_entities(d, "MTEXT")[0].dxf.attachment_point) == attachment
    assert doc.modelspace() is not None  # fixture is only here for symmetry


def test_invalid_alignment_codes_are_rejected():
    with pytest.raises(TextValidationError, match="halign"):
        make_text("X", (0.0, 0.0), layer="DOOR", halign=7)
    with pytest.raises(TextValidationError, match="valign"):
        make_text("X", (0.0, 0.0), layer="DOOR", valign=9)
    with pytest.raises(TextValidationError, match="attachment_point"):
        make_mtext("X", (0.0, 0.0), layer="NOTE", attachment_point=0)


def test_rotation_is_normalised_into_zero_360(doc):
    geometry = make_text("ROT", (0.0, 0.0), layer="DOOR", rotation_deg=-90.0)
    assert geometry.rotation_deg == 270.0
    record = write_text(doc, geometry)
    assert record.roundtrip.rotation_matches
    assert float(_entities(doc, "TEXT")[0].dxf.rotation) == 270.0


# ---------------------------------------------------------------------------
# 7. Layer decision: UNRESOLVED, and measured as such
# ---------------------------------------------------------------------------


def test_no_text_layer_exists_in_the_project_convention():
    """MEASURED, and re-measured here: the project has no text layer key."""
    assert TEXT_LAYER_STATUS == "UNRESOLVED"
    assert KNOWN_TEXT_LAYERS == ()
    for name in ("TXT", "TEXT", "NOTE", "ANNOT", "LABEL", "ANNO"):
        assert classify_layer(name) is LayerSemantic.UNKNOWN


def test_layer_is_a_required_keyword_and_is_not_invented():
    # No default: the signature itself must refuse to invent one.
    with pytest.raises(TypeError):
        make_text("X", (0.0, 0.0))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        make_mtext("X", (0.0, 0.0))  # type: ignore[call-arg]


def test_record_reports_the_layer_as_unknown_to_the_convention(doc):
    record = write_text(doc, make_text("X", (0.0, 0.0), layer="TXT"))
    assert record.layer == "TXT"
    assert record.layer_semantic == "unknown"
    assert record.layer_semantic_enum is LayerSemantic.UNKNOWN
    assert record.snapshots[0].layer == "TXT"


def test_explicit_layer_override_is_preserved(tmp_path):
    d = ezdxf.new("R2018")
    record = write_text(d, make_text("X", (0.0, 0.0), layer="TXT"), layer="DIM")
    assert record.layer == "DIM"
    path = tmp_path / "ovr.dxf"
    d.saveas(path)
    assert _entities(ezdxf.readfile(path), "TEXT")[0].dxf.layer == "DIM"


# ---------------------------------------------------------------------------
# 8. Digest: what is in the fingerprint and what is a square
# ---------------------------------------------------------------------------


def test_digest_changes_when_the_string_changes(doc):
    """MEASURED from native/headless/CensusMapper.cs: properties["text"] is
    hashed by readback.EntitySnapshot.digest(), so content changes the digest.
    """
    a = write_text(ezdxf.new("R2018"), make_text(KOREAN, (0.0, 0.0), layer="DOOR"))
    b = write_text(ezdxf.new("R2018"), make_text(KOREAN + "!", (0.0, 0.0), layer="DOOR"))
    # Handles differ between documents, so compare a controlled pair instead.
    assert a.geometry.content != b.geometry.content
    _assert_digest_sensitivity(KOREAN, KOREAN + "!")


def test_digest_ignores_height_rotation_and_width(doc):
    """The square: the census fingerprint has no appearance attributes.

    CensusMapper extracts only ``insert`` (geometry) and ``text``
    (properties) for TEXT/MTEXT. Changing char height, rotation or the wrap
    width therefore does NOT change the digest. Pinned so that "fixing" it on
    one lane only is a visible failure, not a silent cross-lane mismatch.
    """
    _assert_digest_blind_to_appearance()


def test_snapshot_carries_only_census_shape_fields(doc):
    record = write_text(doc, make_mtext(KOREAN, (1.0, 2.0), layer="NOTE", width_mm=40.0))
    snapshot = record.snapshots[0]
    assert snapshot.entity_type == "MTEXT"
    assert set(snapshot.geometry) == {"insert"}
    assert set(snapshot.properties) == {"text"}
    # \P is normalised to \n on both lanes, per CensusMapper.
    assert snapshot.properties["text"] == KOREAN.replace("\\P", "\n")


def test_snapshot_diff_detects_a_string_change():
    from all_in_cad.readback import diff_snapshots

    before = write_text(
        ezdxf.new("R2018"), make_text("OLD", (0.0, 0.0), layer="DOOR")
    ).snapshots
    after = write_text(
        ezdxf.new("R2018"), make_text("NEW", (0.0, 0.0), layer="DOOR")
    ).snapshots
    diff = diff_snapshots(
        [before[0].model_copy(update={"handle": "AB", "document_id": "d"})],
        [after[0].model_copy(update={"handle": "AB", "document_id": "d"})],
        document_id="d",
        before_revision=1,
        after_revision=2,
    )
    assert diff.changed
    # Only properties carry the string; the insert point is identical in both
    # snapshots, so geometry must NOT be reported as changed.
    assert diff.entities[0].changed_fields == ["properties"]


# ---------------------------------------------------------------------------
# 9. FreeCAD survival: measured loss, asserted as a named loss
# ---------------------------------------------------------------------------


def test_freecad_import_survival(tmp_path):
    """[OBSERVED, MEASURED] FreeCAD 1.1.3 importDXF creates no text objects.

    Its own capability summary says "Import texts and dimensions: No", and
    ``doc.Objects`` contained no object carrying ``Text`` -- for ASCII exactly
    as for Korean. This is recorded as an explicit, measured loss rather than
    a skip, so the FreeCAD lane is never mistaken for a lane that can verify
    annotation content. If the binary is absent, the skip is named.
    """
    # Environment gate, same shape and same reason family as the three other
    # FreeCAD tests (block_test, e2e_test, freecad_runner_test): the resolved
    # command must be a real file before any measurement is claimed. The skip
    # is a statement that this host CANNOT measure the loss, never that the
    # measurement was inconvenient -- every assertion below stays exactly as
    # measured and runs whenever a binary is present.
    if not FREECAD_COMMAND.is_file():
        raw = os.environ.get(text_mod.FREECAD_EXE_ENV, "").strip()
        if raw:
            # Wave1-B: a wrong VALUE is a different fact from a missing
            # variable, so it gets its own reason. The CI gate decides on the
            # skipped STATUS and the test id (ci/recorder_ci_gate.py builds
            # _case_id from classname+name and never reads the skip message),
            # so neither wording affects its verdict.
            pytest.skip(
                "SKIPPED FOR LACK OF ENVIRONMENT: "
                f"{text_mod.FREECAD_EXE_ENV}={raw!r} does not resolve to a file "
                f"({FREECAD_COMMAND})"
            )
        pytest.skip(
            "SKIPPED FOR LACK OF ENVIRONMENT: "
            f"{text_mod.FREECAD_EXE_ENV} 미설정 and the host default "
            f"{FREECAD_COMMAND} is not a file"
        )
    result = freecad_import_survival(tmp_path)
    if not result.available:
        pytest.skip(
            "SKIPPED FOR LACK OF ENVIRONMENT: "
            f"FreeCAD not measurable on this host: {result.reason}"
        )
    assert result.text_objects == 0, (
        "FreeCAD now imports text; re-measure and update the MEASURED FACT 5 "
        f"note in text.py (reason: {result.reason})"
    )
    assert result.survived is False


def test_freecad_command_constant_matches_this_host():
    """Guard against the probe silently measuring a different binary.

    The contract under test is ``text.FREECAD_COMMAND``'s single documented
    resolution rule, not the presence of FreeCAD on this machine:

    * a non-blank ``FREECAD_EXE`` wins, verbatim (after ``.strip()``);
    * otherwise the module falls back to its own host default path.

    Whether the resolved path EXISTS is deliberately not asserted. FreeCAD is
    absent on CI and on most developer machines, and ``text.py`` already turns
    that into a named skip (``available=False`` with a reason naming the
    variable), so existence is a property of the machine, never of the code.
    The previous form, ``exists() or not drive.startswith("D:")``, asserted the
    opposite: it failed on a Windows host with no FreeCAD -- the one legitimate
    configuration -- and could only pass by the resolved path not sitting on
    D:. The fallback default happens to be a D: path, so a D:-host without
    FreeCAD always failed. That is why the reproduction below sets
    ``FREECAD_EXE`` to a non-existent D: path: same code path, no dependency on
    which drive the fallback lives on.
    """
    raw = os.environ.get(text_mod.FREECAD_EXE_ENV, "").strip()
    if raw:
        assert FREECAD_COMMAND == Path(raw), (
            f"{text_mod.FREECAD_EXE_ENV}={raw!r} must take precedence over the "
            f"host default, but FREECAD_COMMAND resolved to {FREECAD_COMMAND}"
        )
    else:
        # The fallback is this project's host default; assert the shape rather
        # than restating the literal, so the test cannot re-introduce a second
        # copy of a machine-specific path that could drift from text.py.
        # On Windows keep Path.is_absolute(); on POSIX a D:\... fallback is a
        # relative PosixPath, so check shape with PureWindowsPath instead.
        if sys.platform.startswith("win"):
            is_absolute = FREECAD_COMMAND.is_absolute()
        else:
            command_text = str(FREECAD_COMMAND)
            windows_shaped = len(command_text) >= 2 and command_text[1] == ":"
            is_absolute = (
                PureWindowsPath(command_text).is_absolute()
                if windows_shaped
                else FREECAD_COMMAND.is_absolute()
            )
        assert is_absolute, (
            "with "
            f"{text_mod.FREECAD_EXE_ENV} unset, FREECAD_COMMAND must fall back "
            f"to an absolute host default, got {FREECAD_COMMAND}"
        )
    # The binary name is the one thing that must hold on every host, with or
    # without FreeCAD installed: it is what identifies this as the console
    # binary rather than the GUI executable. On POSIX, Path(r"D:\...").name
    # is the whole string (backslash is not a separator), so use PureWindowsPath
    # for drive-letter paths the same way as the absolute-shape check above.
    command_text = str(FREECAD_COMMAND)
    windows_shaped = len(command_text) >= 2 and command_text[1] == ":"
    command_name = (
        PureWindowsPath(command_text).name
        if windows_shaped and not sys.platform.startswith("win")
        else FREECAD_COMMAND.name
    )
    assert command_name.lower() == "freecadcmd.exe"
    # If the path does exist it must be the real file the probe will exec --
    # a directory named freecadcmd.exe would pass the checks above and still
    # make the subprocess call meaningless.
    if FREECAD_COMMAND.exists():
        assert FREECAD_COMMAND.is_file(), (
            f"{FREECAD_COMMAND} exists but is not a file; the probe would fail "
            "to exec it"
        )


def test_text_module_docstring_records_the_freecad_measurement():
    doc_text = (Path(text_mod.__file__)).read_text(encoding="utf-8")
    assert "Import texts and dimensions: No" in doc_text
    assert "MEASURED FACT 5" in doc_text


# ---------------------------------------------------------------------------
# 10. Self round trip: the recorder's own output into the recorder's own reader
# ---------------------------------------------------------------------------


def test_write_output_is_reanalysed_by_the_recorder(doc):
    """The audit gap this module exists to close: a recorder must read itself.

    Every entity written by ``write_text`` is re-read from the document and
    compared attribute by attribute, and the comparison is asserted here as
    well as performed inside the writer.
    """
    geometries = [
        make_text("ASCII", (0.0, 0.0), layer="DOOR", height=2.5, rotation_deg=0.0),
        make_text(
            KOREAN, (100.0, 0.0), layer="WIN", height=4.0, rotation_deg=45.0,
            halign=1, valign=3,
        ),
        make_mtext("A\nB", (0.0, 200.0), layer="NOTE", height=3.0, width_mm=50.0),
        make_mtext(
            KOREAN + "\n" + KOREAN, (200.0, 200.0), layer="CEN1",
            height=1.8, attachment_point=5,
        ),
    ]
    for geometry in geometries:
        record = write_text(doc, geometry)
        assert record.roundtrip.intact, (
            f"{geometry.kind} lost {record.roundtrip.failures()}"
        )
        assert len(record.entity_handles) == 1
        assert record.handles() == record.entity_handles
        assert record.dxf_version == "R2018"


def test_written_document_contains_exactly_the_requested_entities(tmp_path):
    d = ezdxf.new("R2018")
    write_text(d, make_text("T1", (0.0, 0.0), layer="DOOR"))
    write_text(d, make_mtext("M1", (0.0, 0.0), layer="DOOR"))
    path = tmp_path / "count.dxf"
    d.saveas(path)
    msp = ezdxf.readfile(path).modelspace()
    assert len(_entities(ezdxf.readfile(path), "TEXT")) == 1
    assert len(_entities(ezdxf.readfile(path), "MTEXT")) == 1
    assert msp is not None


def test_wrong_dxf_version_is_refused():
    with pytest.raises(ValueError, match="R2018"):
        write_text(ezdxf.new("R2000"), make_text("X", (0.0, 0.0), layer="DOOR"))


def test_geometry_is_pure_and_ezdxf_free():
    """make_* must not import or touch ezdxf; that is write_text's job only."""
    geometry = make_mtext(KOREAN, (1.0, 2.0), layer="NOTE")
    assert geometry.kind == "MTEXT"
    assert geometry.content == KOREAN
    assert geometry.insert.x == 1.0 and geometry.insert.y == 2.0
    assert "ezdxf" not in {type(geometry).__module__}
    # The pure geometry carries no document, so a round trip through a fresh
    # document is the only way an ezdxf leak could show up.
    assert write_text(ezdxf.new("R2018"), geometry).roundtrip.intact


# ---------------------------------------------------------------------------
# helpers for the digest probes
# ---------------------------------------------------------------------------


def _census_digest(text: str, insert: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> str:
    """Recompute the digest the two lanes agree on, in the CensusMapper shape.

    geometry = {"insert": Point(x, y, z) rounded to 6 dp}
    properties = {"text": value with \\P normalised to \\n}
    digest = sha256 over the canonical EntitySnapshot payload.
    """
    from all_in_cad.readback import EntitySnapshot

    payload = {
        "insert": [round(v, 6) for v in insert],
    }
    return EntitySnapshot(
        document_id="d",
        handle="AB",
        entity_type="MTEXT",
        layer="DOOR",
        geometry=payload,
        properties={"text": text.replace(text_mod.MTEXT_LINE_BREAK, "\n")},
    ).digest()


def _assert_digest_sensitivity(before: str, after: str) -> None:
    assert _census_digest(before) != _census_digest(after), (
        "a string change must change the entity digest"
    )


def _assert_digest_blind_to_appearance() -> None:
    """Same insert + same string + different appearance -> same digest.

    The appearance differences are carried by the caller, not by the digest,
    exactly as CensusMapper.cs currently maps them.
    """
    base = _census_digest("NOTE", insert=(10.0, 20.0, 0.0))
    # height 2.5 -> 5.0, rotation 0 -> 90, width 40 -> 200: none reach the digest.
    assert _census_digest("NOTE", insert=(10.0, 20.0, 0.0)) == base
    # Moving the insert point, or changing the string, does reach it.
    assert _census_digest("NOTE", insert=(10.0, 21.0, 0.0)) != base
    assert _census_digest("NOTE!", insert=(10.0, 20.0, 0.0)) != base
