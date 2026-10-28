r"""Text / annotation recorder: writes plan-view text into a DXF as TEXT or MTEXT.

[OBSERVED] vs [DESIGN] labelling used throughout, as in ``wall.py`` / ``door.py``.

=====================================================================
EVIDENCE CLASSIFICATION -- READ THIS FIRST
=====================================================================
There is **no observed text data in this repository**. No drawing produced by
the original ZWCAD/XiCAD runtime was ever captured with a text entity on it,
no DCL dialog for text was restored, and no text command was traced. Therefore
**every design decision in this file is [DESIGN]**. The only [OBSERVED] items
are the ezdxf 1.4.4 / FreeCAD 1.1.3 behaviours that were *measured on this
host* and are quoted with their measurement below. Nothing below is inherited
from the original CAD runtime.

=====================================================================
MEASURED FACT 1 -- A REAL "Standard" TEXT STYLE EXISTS BY DEFAULT
=====================================================================
[OBSERVED on this host, ezdxf 1.4.4, CPython 3.14.5]

    >>> doc = ezdxf.new("R2018")              # no setup=
    >>> [s.dxf.name for s in doc.styles]
    ['Standard']
    >>> doc = ezdxf.new("R2018", setup=True)
    >>> len(list(doc.styles))
    27      # 'Standard' plus 26 ezdxf-supplied OpenSans/Liberation styles

``add_text`` and ``add_mtext`` both default ``dxf.style`` to ``'Standard'`` when
no ``style=`` is given, and the written entity really carries ``style
Standard`` after a save/reload. So ``"Standard"`` is a *measured* default of
the writer, not a name invented here. This module still verifies at write time
that the requested style actually exists in the target document and refuses to
write a dangling style reference -- a missing STYLE table entry is exactly the
kind of silent loss this module is meant to prevent.

=====================================================================
MEASURED FACT 2 -- ezdxf 1.4.4 SILENTLY DESTROYS "\n" IN A TEXT ENTITY
=====================================================================
[OBSERVED, MEASURED]

    >>> t = msp.add_text("line1\nline2")
    >>> t.dxf.text
    'line1line2'          # the newline is gone, with no error and no warning
    >>> t.plain_text()
    'line1line2'

A DXF ``TEXT`` entity has **no** line-break mechanism; ``\n`` is not a DXF
code there and ezdxf simply drops it. Writing a multi-line string through
``TEXT`` therefore loses lines **silently**. This module FAILS CLOSED instead:
:func:`make_text` rejects any content containing a line break, so the caller
must use :func:`make_mtext` for multi-line content. This is the single most
important guarantee in the file.

=====================================================================
MEASURED FACT 3 -- MTEXT "\P" IS THE REAL LINE BREAK, AND WRAP IS OFF BY DEFAULT
=====================================================================
[OBSERVED, MEASURED]

    >>> m = msp.add_mtext("A\\PB\\PC", dxfattribs={"width": 30.0})
    >>> repr(m.dxf.text)        -> 'A\\PB\\PC'   # stored as the DXF escape
    >>> repr(m.plain_text())    -> 'A\nB\nC'    # \P is the hard break
    >>> msp.add_mtext("q").dxf.width is None    -> True

So, with the evidence asked for:

* **Hard line break (default, explicit): ``\P``.** It is stored verbatim in
  the DXF string and is what every CAD consumer reads as "new paragraph". It
  needs no ``width`` at all.
* **Automatic wrapping is NOT the default.** A freshly created MTEXT has
  ``width is None`` (measured), i.e. no wrap column is defined and the text
  runs as one long line. Wrapping is therefore **opt-in** in this module:
  ``width_mm`` must be passed explicitly, and it is recorded on the geometry
  so the caller can see whether wrapping was requested.
* This module normalises ``"\n"``/``"\r\n"`` in the caller's string to ``\P``
  so callers can use ordinary Python newlines, and rejects a mix of an
  explicit ``width`` and an over-long expectation? No -- it does not: it
  records both and lets the consumer wrap. The DXF-level guarantee it *does*
  make is that every requested break is present as ``\P`` in the stored string.

=====================================================================
MEASURED FACT 4 -- KOREAN SURVIVES AN R2018 DXF ROUND TRIP IN THIS REPO
=====================================================================
[OBSERVED, MEASURED] ``ezdxf`` 1.4.4, ``R2018`` (AC1032), this host.

A document containing one ``TEXT`` and one ``MTEXT`` whose content is
``"타입 한글 카페 1234"`` (plus ``"A\\P타입 한글 카페 1234"`` for the MTEXT) was
written, saved to disk, and re-read with ``ezdxf.readfile``:

    text == korean            -> True
    mtext == korean           -> True
    text.encode('utf-8') == original.encode('utf-8')   -> True
    mtext.encode('utf-8') == original.encode('utf-8')  -> True

Insert point, height, rotation, style and MTEXT width also survived. The
regression test ``test_korean_text_survives_save_reload_byte_identical`` in
``text_test.py`` re-runs exactly this measurement, because this host has
repeatedly broken on CP949 console output and UTF-16LE string handling. A
passing ``repr()`` in a console is *not* evidence; the assertion is on
``.encode("utf-8")`` bytes, and the test is skipped loudly rather than passing
vacuously if it is ever removed. (Note: the Windows console renders Korean as
mojibake even when the round trip is byte-perfect. Mojibake in *console
output* is a display artifact, not data loss.)

=====================================================================
MEASURED FACT 5 -- FreeCAD 1.1.3 importDXF DROPS TEXT AND MTEXT ENTIRELY
=====================================================================
[OBSERVED, MEASURED] FreeCAD 1.1.3 (Windows x86_64, py311), ``freecadcmd.exe``
on this host.

A DXF containing one ASCII ``TEXT``, one ASCII ``MTEXT``, one Korean ``TEXT``
and one Korean ``MTEXT`` was imported with ``importDXF.insert``. The importer's
own summary printed::

    - Import texts and dimensions: No
    Entity counts:
      - MTEXT: 1
      - TEXT: 1
    FreeCAD objects created: 5

``doc.Objects`` afterwards contained only ``App::DocumentObjectGroup``
``_UnreferencedBlocks``, two ``App::FeaturePython`` layers, the layer
container, and the three FreeCAD *system blocks* (ARCHTICK, CLOSEDBLANK,
CLOSEDFILLED) plus their two ``Part::Feature`` shapes. **No text object of any
kind was created.** The "Entity counts: TEXT: 1 / MTEXT: 1" lines report what
the low-level reader *saw*, not what was turned into a document object.

**Therefore: text does NOT survive a FreeCAD importDXF round trip on this
host, for ASCII as well as for Korean.** This is a property of the importer
(option "Import texts and dimensions: No"), not an encoding problem, which is
why the ASCII control probe failed identically. Consequence for this project:
the FreeCAD lane must never be used to assert text survival or text equality.
It is a geometry lane. Anything that needs the annotation content must read the
DXF with ezdxf (or the ACadSharp census lane), not through FreeCAD. The test
``test_freecad_import_survival`` records this as an explicit, named skip
rather than a silent pass.

=====================================================================
IS THE TEXT IN THE ENTITY DIGEST? -- MEASURED FROM THE SOURCE
=====================================================================
The "entity fingerprint" the task refers to is built in two places that must
agree:

* ``src/all_in_cad/readback.py`` -- ``EntitySnapshot.digest()`` is
  ``sha256`` over the JSON of ``{document_id, handle, entity_type, layer,
  geometry, properties}`` with ``sort_keys=True`` and
  ``separators=(",", ":")`` and ``ensure_ascii=False`` (so the JSON is encoded
  UTF-8 and a Korean string changes the bytes, not just the length).
* ``native/headless/CensusMapper.cs`` -- the ACadSharp lane that must produce
  a matching fingerprint. Numbers there go through
  ``private static double Round(double value) => Math.Round(value, 6);``,
  which is the "6-decimal normalisation" the task mentions.

Reading ``CensusMapper.ExtractGeometry`` / ``ExtractProperties`` for the two
text types gives the answer:

    MText      -> geometry["insert"] = Point(x, y, z)     (rounded to 6 dp)
                   properties["text"] = mtext.Value?.Replace("\\P", "\n")
    TextEntity -> geometry["insert"] = Point(x, y, z)     (rounded to 6 dp)
                   properties["text"] = text.Value

Conclusions, and they are asymmetric:

1. **The string IS in the fingerprint.** It lands in ``properties["text"]``,
   and ``digest()`` hashes ``properties``. Editing a character of a Korean or
   ASCII string changes the digest. Test
   ``test_digest_changes_when_string_changes`` proves it.
2. **Height, rotation, alignment, wrap width and text style are NOT in the
   fingerprint.** They are absent from both ``ExtractGeometry`` and
   ``ExtractProperties`` for text entities. Two MTEXT entities with the same
   insert point, the same layer, and the same string but ``char_height`` 2.5
   vs 5.0, or ``rotation`` 0 vs 90, or ``width`` 40 vs 200, produce an
   **identical** digest. Test
   ``test_digest_ignores_height_rotation_and_width`` pins that square so it
   cannot be "fixed" by accident in one lane only.

The practical rule: the fingerprint is safe for *content* and *position*, and
is blind to *appearance*. Appearance changes must be verified by reading the
attributes, never by comparing digests. ``CensusMapper`` also normalises
``\P`` to ``\n`` on the ACadSharp side, so a cross-lane comparison must use the
``\P``-normalised string on both sides; this module's snapshots do that.

=====================================================================
LAYER NAME: UNRESOLVED, AND DELIBERATELY NOT INVENTED
=====================================================================
[MEASURED] ``src/all_in_cad/semantic_layers.py`` and
``configs/architectural-layers.json`` were both read:

* ``LayerSemantic`` has **no** ``TEXT``/``NOTE``/``ANNOTATION`` member.
* ``_EXACT`` and the JSON ``exact`` table contain
  ``COL WAL1 WAL2 WAL3 ELE DOOR DOOR_ELE WIN WINBAR WINELE STAIR DIM DIMLE CEN
  CEN1`` -- **no text key of any kind**.
* ``_PATTERNS`` has no text pattern either, so ``classify_layer("TXT")`` and
  ``classify_layer("NOTE")`` both return ``LayerSemantic.UNKNOWN``.

The measured set of project-convention text layers is therefore **EMPTY**, and
this module **invents no name**. :data:`TEXT_LAYER_STATUS` is ``"UNRESOLVED"``
and :data:`KNOWN_TEXT_LAYERS` is ``()``. Consequence for the API, identical to
``hatch.py``: ``layer`` is a REQUIRED keyword argument with no default. There
is no convenient fallback, because the two earlier substitute-layer attempts
in this project (openings, hatching) were measured to produce silent data
contamination -- text silently landing on an unrelated layer is worse than a
refusal. The record reports ``classify_layer(layer)`` so the caller can see
that the layer is UNKNOWN to the project convention and decide.

=====================================================================
ENVIRONMENT NOTE (observed on this host, verified by running it)
=====================================================================
Python lives in ``C:\\Users\\khs09\\all-in-cad\\.venv``, ezdxf 1.4.4. Aside
injects ``PYTHONHOME=C:\Users\khs09\.aside\runtime\python\runtime``,
which hides the venv standard library and makes ``import ezdxf`` fail with
``ModuleNotFoundError: No module named 'annotationlib'``. Always clear both::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\Users\khs09\all-in-cad\.venv\Scripts\python.exe -m pytest \
        C:\Users\khs09\all-in-cad\src\all_in_cad\recorder\text_test.py

This is a host quirk of the launcher, not a defect in this module.

FreeCAD is **not importable** inside that venv
(``ModuleNotFoundError: No module named 'FreeCAD'``). The measurements above
were taken by running
``D:\\CAD\\FreeCAD\\FreeCAD_1.1.3-Windows-x86_64-py311\\bin\\freecadcmd.exe``
as a separate process. The test module does exactly that when the executable
is present, and skips with a named reason when it is not.
"""

from __future__ import annotations

import math
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

try:  # package-relative import (normal case)
    from ..readback import EntitySnapshot
    from ..semantic_layers import LayerSemantic, classify_layer
    from ..topology import Point2D
except ImportError:  # pragma: no cover - direct/flat execution fallback
    from all_in_cad.readback import EntitySnapshot
    from all_in_cad.semantic_layers import LayerSemantic, classify_layer
    from all_in_cad.topology import Point2D

__all__ = [
    "DEFAULT_STYLE",
    "DXF_MIN_READ_VERSION",
    "DXF_WRITE_VERSION",
    "FREECAD_COMMAND",
    "KNOWN_TEXT_LAYERS",
    "MTEXT_LINE_BREAK",
    "TEXT_ALIGNMENTS",
    "TEXT_LAYER_STATUS",
    "TextEntitySpec",
    "TextGeometry",
    "TextRecord",
    "TextValidationError",
    "freecad_import_survival",
    "make_mtext",
    "make_text",
    "normalize_mtext_content",
    "write_text",
]

#: DXF version written by this recorder and the oldest version it reads.
DXF_WRITE_VERSION = "R2018"  # AC1032
DXF_MIN_READ_VERSION = "R2000"  # AC1015

#: [OBSERVED, MEASURED] ezdxf 1.4.4 creates this style in every R2018 document,
#: with or without ``setup=True``, and it is the default ``dxf.style`` of both
#: ``add_text`` and ``add_mtext``. It is a measured default of the writer, not
#: a name chosen by this module. ``write_text`` still checks that it exists in
#: the target document and refuses a dangling reference.
DEFAULT_STYLE = "Standard"

#: [DESIGN] The hard line break written into an MTEXT string. Chosen because it
#: is the DXF-native paragraph break and because it was MEASURED to round trip
#: through ezdxf (``plain_text()`` maps it to ``\n``). Newlines in the caller's
#: string are normalised to this.
MTEXT_LINE_BREAK = "\\P"

#: [MEASURED] ``LayerSemantic`` has no text member, neither layer table in this
#: project has a text key, and ``classify_layer`` has no text pattern. The
#: measured set of project-convention text layers is EMPTY, so no name is
#: invented and ``layer`` is a required argument.
TEXT_LAYER_STATUS = "UNRESOLVED"
KNOWN_TEXT_LAYERS: tuple[str, ...] = ()

#: [DESIGN] Allowed DXF TEXT horizontal alignment codes (0 left, 1 centre,
#: 2 right) and vertical codes (0 baseline, 1 bottom, 2 middle, 3 top), plus
#: the MTEXT attachment-point codes 1..9. These are DXF spec values, not
#: measurements of this project's runtime.
TEXT_ALIGNMENTS = {
    "halign": (0, 1, 2),
    "valign": (0, 1, 2, 3),
    "attachment": tuple(range(1, 10)),
}

#: Name of the environment variable that overrides the binary below. Shared by
#: block_test.py and e2e_test.py, which resolve it the same way.
FREECAD_EXE_ENV = "FREECAD_EXE"

#: [OBSERVED, MEASURED] FreeCAD 1.1.3 console binary on this host. FreeCAD is
#: not importable from the project venv, so a real survival check must run it
#: as a separate process; if this file is absent the check is skipped by name.
#:
#: This is PRODUCTION code, not a test default, so the hardcoded path is kept
#: as a last-resort fallback: removing it would change what a caller on this
#: host measures (``available`` would flip True to False) for no gain. The
#: environment variable is consulted FIRST, so another host can point the
#: measurement at its own binary without editing this module. The skip reason
#: names the variable, because "no FreeCAD here" and "this machine's FreeCAD is
#: missing" are different facts and only the second is a host defect.
FREECAD_COMMAND = Path(
    os.environ.get(
        FREECAD_EXE_ENV,
        r"D:\CAD\FreeCAD\FreeCAD_1.1.3-Windows-x86_64-py311\bin\freecadcmd.exe",
    ).strip()
    or r"D:\CAD\FreeCAD\FreeCAD_1.1.3-Windows-x86_64-py311\bin\freecadcmd.exe"
)

TextKind = Literal["TEXT", "MTEXT"]


class TextValidationError(ValueError):
    """Raised for a text request that cannot be written without losing data."""


@dataclass(frozen=True, slots=True)
class TextEntitySpec:
    """One planned text entity, independent of ezdxf."""

    role: str
    kind: TextKind
    content: str
    insert: Point2D
    height: float
    rotation_deg: float
    style: str
    halign: int = 0
    valign: int = 0
    attachment_point: int = 1
    width_mm: float | None = None


@dataclass(frozen=True, slots=True)
class TextGeometry:
    """Fully resolved text geometry, before it touches a document.

    ``content`` is the *stored* DXF string: for MTEXT every line break is the
    ``\\P`` escape, never a raw newline. ``lines`` is the caller's logical
    content split back out, so a caller can assert what it meant to write.
    """

    kind: TextKind
    role: str
    content: str
    lines: tuple[str, ...]
    insert: Point2D
    height: float
    rotation_deg: float
    style: str
    layer: str
    halign: int = 0
    valign: int = 0
    attachment_point: int = 1
    width_mm: float | None = None
    alignment_point: Point2D | None = None
    warnings: tuple[str, ...] = field(default=())

    @property
    def wrapping(self) -> bool:
        """True only when an explicit wrap column was requested.

        [OBSERVED, MEASURED] a fresh ezdxf MTEXT has ``width is None``, so the
        DXF default is *no* wrapping. Automatic wrapping is opt-in here.
        """
        return self.width_mm is not None

    @property
    def has_line_breaks(self) -> bool:
        return len(self.lines) > 1

    def validate(self) -> None:
        _require_non_empty(self.content, "content")
        if self.kind == "TEXT":
            if "\n" in self.content or "\r" in self.content:
                raise TextValidationError(
                    "a DXF TEXT entity has no line break: ezdxf 1.4.4 was "
                    "MEASURED to drop '\\n' from TEXT.dxf.text silently "
                    "(measured: add_text('line1\\nline2').dxf.text == "
                    "'line1line2'). Use make_mtext() with '\\P' or newlines "
                    "for multi-line content."
                )
        else:
            if self.width_mm is not None and self.width_mm <= 0:
                raise TextValidationError("width_mm must be > 0 when given")
        if not (math.isfinite(self.insert.x) and math.isfinite(self.insert.y)):
            raise TextValidationError("insert point must be finite")
        if not math.isfinite(self.height) or self.height <= 0:
            raise TextValidationError("height must be a positive finite number")
        if not math.isfinite(self.rotation_deg):
            raise TextValidationError("rotation_deg must be finite")
        if self.halign not in TEXT_ALIGNMENTS["halign"]:
            raise TextValidationError(f"halign must be one of {TEXT_ALIGNMENTS['halign']}")
        if self.valign not in TEXT_ALIGNMENTS["valign"]:
            raise TextValidationError(f"valign must be one of {TEXT_ALIGNMENTS['valign']}")
        if self.attachment_point not in TEXT_ALIGNMENTS["attachment"]:
            raise TextValidationError(
                f"attachment_point must be one of {TEXT_ALIGNMENTS['attachment']}"
            )
        _require_non_empty(self.style, "style")
        _require_non_empty(self.layer, "layer")


@dataclass(frozen=True, slots=True)
class TextRecord:
    """What was actually written, with readback evidence for every entity."""

    document_id: str
    dxf_version: str
    geometry: TextGeometry
    layer: str
    layer_semantic: str
    entity_handles: tuple[str, ...]
    snapshots: tuple[EntitySnapshot, ...]
    roundtrip: TextRoundtrip

    def handles(self) -> tuple[str, ...]:
        return self.entity_handles

    @property
    def layer_semantic_enum(self) -> LayerSemantic:
        return LayerSemantic(self.layer_semantic)


@dataclass(frozen=True, slots=True)
class TextRoundtrip:
    """The self-analysis result: what came back out of the live document.

    [DESIGN] This exists because the audit that motivated these recorders
    found that no test ever fed a recorder's own output back into its own
    reader. ``write_text`` re-reads the entity from the document it just wrote
    and compares every attribute it promised to preserve; ``text_test.py``
    asserts on this object *and* on a save/reload through a real file, so a
    silent loss cannot pass.
    """

    content_matches: bool
    insert_matches: bool
    height_matches: bool
    rotation_matches: bool
    style_matches: bool
    width_matches: bool
    alignment_matches: bool
    read_back_content: str

    @property
    def intact(self) -> bool:
        return all(
            (
                self.content_matches,
                self.insert_matches,
                self.height_matches,
                self.rotation_matches,
                self.style_matches,
                self.width_matches,
                self.alignment_matches,
            )
        )

    def failures(self) -> tuple[str, ...]:
        names = {
            "content": self.content_matches,
            "insert": self.insert_matches,
            "height": self.height_matches,
            "rotation": self.rotation_matches,
            "style": self.style_matches,
            "width": self.width_matches,
            "alignment": self.alignment_matches,
        }
        return tuple(name for name, ok in names.items() if not ok)


def normalize_mtext_content(text: str) -> str:
    """Normalise caller line breaks into the DXF ``\\P`` escape.

    Accepts ``\\n``, ``\\r\\n`` and a literal ``\\P`` and produces a string that
    contains only ``\\P`` as a line break, which is what a DXF consumer reads
    as a paragraph break. [DESIGN] driven by the measured fact that a raw
    ``\\n`` is not a DXF MTEXT break either: it is stored verbatim and the
    consumer sees a literal control character, not a break.
    """
    value = text.replace("\r\n", "\n").replace("\r", "\n").replace(MTEXT_LINE_BREAK, "\n")
    return value.replace("\n", MTEXT_LINE_BREAK)


def make_text(
    text: str,
    insert: tuple[float, float] | Point2D,
    *,
    layer: str,
    height: float = 2.5,
    rotation_deg: float = 0.0,
    style: str = DEFAULT_STYLE,
    halign: int = 0,
    valign: int = 0,
    role: str = "note",
) -> TextGeometry:
    """Build a single-line DXF ``TEXT`` entity geometry. Pure; ezdxf-free.

    ``layer`` is REQUIRED, with no default, because the project layer
    convention has no text key (:data:`TEXT_LAYER_STATUS`).

    Fails closed on a line break: a DXF ``TEXT`` entity cannot hold one and
    ezdxf was measured to drop it without complaint. Use :func:`make_mtext`
    for anything multi-line.
    """
    point = _as_point(insert, "insert")
    content = _require_non_empty(text, "text")
    geometry = TextGeometry(
        kind="TEXT",
        role=role,
        content=content,
        lines=tuple(content.split(MTEXT_LINE_BREAK)),
        insert=point,
        height=_positive(height, "height"),
        rotation_deg=normalize_deg(rotation_deg),
        style=_require_non_empty(style, "style"),
        layer=_require_non_empty(layer, "layer"),
        halign=int(halign),
        valign=int(valign),
        alignment_point=None,
    )
    geometry.validate()
    return geometry


def make_mtext(
    text: str,
    insert: tuple[float, float] | Point2D,
    *,
    layer: str,
    height: float = 2.5,
    rotation_deg: float = 0.0,
    style: str = DEFAULT_STYLE,
    width_mm: float | None = None,
    attachment_point: int = 1,
    role: str = "note",
) -> TextGeometry:
    """Build a possibly multi-line DXF ``MTEXT`` entity geometry. Pure.

    Line breaks in ``text`` may be given as ``\\n``, ``\\r\\n`` or ``\\P``; all
    three are normalised to ``\\P``, the DXF-native hard break.

    ``width_mm`` is the wrap column. It is **opt-in and off by default**,
    because a fresh ezdxf MTEXT carries ``width is None`` (measured) and a
    wrap column that nobody asked for is a silent change of the drawing. Pass
    it only when the annotation really should wrap, and then the geometry
    records ``wrapping is True`` so the intent is auditable.
    """
    point = _as_point(insert, "insert")
    raw = _require_non_empty(text, "text")
    content = normalize_mtext_content(raw)
    lines = tuple(
        raw.replace("\r\n", "\n").replace("\r", "\n").replace(MTEXT_LINE_BREAK, "\n").split("\n")
    )
    if width_mm is not None:
        width = _positive(width_mm, "width_mm")
    else:
        width = None
    geometry = TextGeometry(
        kind="MTEXT",
        role=role,
        content=content,
        lines=lines,
        insert=point,
        height=_positive(height, "height"),
        rotation_deg=normalize_deg(rotation_deg),
        style=_require_non_empty(style, "style"),
        layer=_require_non_empty(layer, "layer"),
        attachment_point=int(attachment_point),
        width_mm=width,
    )
    geometry.validate()
    return geometry


def write_text(doc: Any, geometry: TextGeometry, layer: str | None = None) -> TextRecord:
    """Write ``geometry`` into an ezdxf R2018 document and read it straight back.

    ``layer`` overrides ``geometry.layer`` when given. The layer table entry
    and the requested text style are created if the document does not have
    them, but a style is never created implicitly under a name the caller did
    not ask for -- an unknown style is a :class:`TextValidationError`, so a
    typo cannot become a silently mis-rendered annotation.

    The returned :class:`TextRecord` carries a :class:`TextRoundtrip` produced
    by re-reading the entity from the document just written. If any promised
    attribute did not survive, ``record.roundtrip.intact`` is ``False`` and
    ``record.roundtrip.failures()`` names what was lost.
    """
    _require_ezdxf()  # fail early with the actionable host-quirk message
    doc_version = str(getattr(doc, "dxfversion", "") or "")
    doc_release = str(getattr(doc, "acad_release", "") or "")
    release = _release_of(doc_version)
    if release != DXF_WRITE_VERSION or (doc_release and doc_release != DXF_WRITE_VERSION):
        raise ValueError(
            f"this recorder writes {DXF_WRITE_VERSION} (AC1032) only, "
            f"got {doc_version or doc_release}"
        )
    if release not in ("R2000", "R2004", "R2007", "R2010", "R2013", "R2018"):
        raise ValueError(f"read lower bound is R2000 (AC1015); got {doc_version}")

    target_layer = _require_non_empty(layer if layer is not None else geometry.layer, "layer")
    if not doc.layers.has_entry(target_layer):
        doc.layers.add(target_layer)

    styles = doc.styles
    if not styles.has_entry(geometry.style):
        raise TextValidationError(
            f"text style {geometry.style!r} does not exist in this document. "
            "A dangling STYLE reference is silently lost data, so it is "
            "refused rather than invented. Measured default that does exist: "
            f"{DEFAULT_STYLE!r}. Create the style explicitly if you need a font."
        )

    msp = doc.modelspace()
    attribs: dict[str, Any] = {
        "layer": target_layer,
        "style": geometry.style,
        "rotation": geometry.rotation_deg,
    }
    if geometry.kind == "TEXT":
        attribs["height"] = geometry.height
        attribs["halign"] = geometry.halign
        attribs["valign"] = geometry.valign
        if geometry.halign or geometry.valign:
            # A non-default alignment makes DXF read the alignment point
            # instead of the insert point, so the two must be kept equal or
            # the text silently jumps somewhere else on open.
            attribs["align_point"] = (
                geometry.alignment_point.x,
                geometry.alignment_point.y,
            ) if geometry.alignment_point else (geometry.insert.x, geometry.insert.y)
        entity = msp.add_text(geometry.content, dxfattribs=attribs)
        entity.set_placement((geometry.insert.x, geometry.insert.y))
    else:
        attribs["char_height"] = geometry.height
        attribs["attachment_point"] = geometry.attachment_point
        if geometry.width_mm is not None:
            attribs["width"] = geometry.width_mm
        entity = msp.add_mtext(geometry.content, dxfattribs=attribs)
        entity.set_location((geometry.insert.x, geometry.insert.y))

    handle = str(entity.dxf.handle)
    document_id = _document_id(doc)
    roundtrip = _roundtrip(entity, geometry)
    snapshot = _snapshot(entity, document_id)
    return TextRecord(
        document_id=document_id,
        dxf_version=doc_release or release,
        geometry=geometry,
        layer=target_layer,
        layer_semantic=str(classify_layer(target_layer)),
        entity_handles=(handle,),
        snapshots=(snapshot,),
        roundtrip=roundtrip,
    )


def _roundtrip(entity: Any, geometry: TextGeometry) -> TextRoundtrip:
    """Re-read the freshly written entity and compare it to the intent."""
    read_content = str(entity.dxf.text)
    insert = entity.dxf.insert
    read_insert = Point2D(float(insert.x), float(insert.y))
    if geometry.kind == "TEXT":
        read_height = float(entity.dxf.height)
        read_rotation = float(entity.dxf.rotation)
        read_width = None
        alignment_ok = (
            int(entity.dxf.halign) == geometry.halign
            and int(entity.dxf.valign) == geometry.valign
        )
    else:
        read_height = float(entity.dxf.char_height)
        read_rotation = float(entity.dxf.rotation)
        has_width = entity.dxf.hasattr("width") and entity.dxf.width is not None
        read_width = float(entity.dxf.width) if has_width else None
        alignment_ok = int(entity.dxf.attachment_point) == geometry.attachment_point

    return TextRoundtrip(
        content_matches=read_content == geometry.content,
        insert_matches=(
            math.isclose(read_insert.x, geometry.insert.x, abs_tol=1e-9)
            and math.isclose(read_insert.y, geometry.insert.y, abs_tol=1e-9)
        ),
        height_matches=math.isclose(read_height, geometry.height, abs_tol=1e-9),
        rotation_matches=math.isclose(read_rotation, geometry.rotation_deg, abs_tol=1e-9),
        style_matches=str(entity.dxf.style) == geometry.style,
        width_matches=read_width == geometry.width_mm,
        alignment_matches=alignment_ok,
        read_back_content=read_content,
    )


def _snapshot(entity: Any, document_id: str) -> EntitySnapshot:
    """Build a readback snapshot in the *census shape*, not a wider one.

    [MEASURED from native/headless/CensusMapper.cs] the ACadSharp lane emits
    only ``geometry["insert"]`` and ``properties["text"]`` for TEXT/MTEXT, and
    normalises ``\\P`` to ``\\n``. A snapshot carrying extra keys such as
    ``height`` or ``rotation`` would hash differently from that lane even for
    a byte-identical drawing, which is precisely the kind of false "cross-lane
    mismatch" this repository has been burned by. Appearance attributes are
    therefore available through the record, not through the fingerprint.
    """
    insert = entity.dxf.insert
    content = str(entity.dxf.text)
    if entity.dxftype() == "MTEXT":
        content = content.replace(MTEXT_LINE_BREAK, "\n")
    return EntitySnapshot(
        document_id=document_id,
        handle=str(entity.dxf.handle),
        entity_type=entity.dxftype(),
        layer=str(entity.dxf.layer),
        geometry={"insert": [float(insert.x), float(insert.y), float(insert.z)]},
        properties={"text": content},
    )


@dataclass(frozen=True, slots=True)
class FreecadSurvival:
    """Result of a real ``freecadcmd`` import of a DXF holding this text.

    [OBSERVED, MEASURED on this host] FreeCAD 1.1.3 ``importDXF`` reports
    "Import texts and dimensions: No" and creates **no** object for TEXT or
    MTEXT, for ASCII exactly as for Korean. So ``survived`` is expected to be
    ``False`` and the test asserts that as a named, documented loss rather
    than skipping it. ``skipped`` is only True when the FreeCAD binary is
    absent, and then ``reason`` says so.
    """

    available: bool
    survived: bool
    text_objects: int
    reason: str


def freecad_import_survival(
    tmp_path: str | os.PathLike[str],
    text: str = "FREECAD PROBE",
) -> FreecadSurvival:
    """Write a one-MTEXT DXF and measure whether FreeCAD's importer keeps it.

    Runs the real ``freecadcmd.exe`` as a separate process, because FreeCAD is
    not importable from the project venv. Returns a measured result; it never
    guesses. If the executable is missing the result is ``available=False``
    with the reason, so a caller can skip by name instead of passing silently.
    """
    if not FREECAD_COMMAND.exists():
        return FreecadSurvival(
            available=False,
            survived=False,
            text_objects=0,
            reason=(
                f"{FREECAD_EXE_ENV} 미설정 and the host default "
                f"{FREECAD_COMMAND} does not exist"
                if not os.environ.get(FREECAD_EXE_ENV, "").strip()
                else f"FreeCAD executable not found at {FREECAD_COMMAND}"
            ),
        )
    base = Path(tmp_path)
    dxf_path = base / "freecad_text_probe.dxf"
    doc = _require_ezdxf().new(DXF_WRITE_VERSION, setup=True)
    doc.modelspace().add_mtext(
        text, dxfattribs={"char_height": 5.0, "width": 60.0, "layer": "0"}
    ).set_location((0.0, 0.0))
    doc.saveas(dxf_path)

    script = base / "freecad_text_probe.py"
    result = base / "freecad_text_probe.json"
    script.write_text(
        "import os, json, FreeCAD, importDXF\n"
        f"p = r'{dxf_path}'\n"
        "doc = FreeCAD.newDocument('aic_text_probe')\n"
        "importDXF.insert(p, doc.Name)\n"
        "hits = [o.Name for o in doc.Objects "
        "if getattr(o, 'Text', None) is not None]\n"
        f"json.dump({{'objects': len(doc.Objects), 'text_objects': len(hits), "
        f"'names': hits}}, open(r'{result}', 'w', encoding='utf-8'))\n",
        encoding="utf-8",
    )
    if result.exists():
        result.unlink()
    try:
        completed = subprocess.run(  # noqa: S603
            [str(FREECAD_COMMAND), str(script)],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        return FreecadSurvival(False, False, 0, f"freecadcmd could not run: {exc}")
    if not result.exists():
        detail = (completed.stderr or completed.stdout or "").strip()[-400:]
        return FreecadSurvival(
            True,
            False,
            0,
            f"freecadcmd produced no result file "
            f"(exit {completed.returncode}): {detail}",
        )
    import json as _json  # noqa: PLC0415

    payload = _json.loads(result.read_text(encoding="utf-8"))
    count = int(payload.get("text_objects", 0))
    return FreecadSurvival(
        available=True,
        survived=count > 0,
        text_objects=count,
        reason=(
            f"freecadcmd exit {completed.returncode}, "
            f"{payload.get('objects')} objects total, {count} carrying text"
        ),
    )


def normalize_deg(value: float) -> float:
    """Normalise an angle in degrees into [0, 360)."""
    result = math.fmod(float(value), 360.0)
    if result < 0:
        result += 360.0
    if result == 360.0:
        result = 0.0
    return result


def _require_non_empty(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TextValidationError(f"{name} must be a str, got {type(value).__name__}")
    if not value.strip():
        raise TextValidationError(
            f"{name} must not be empty or whitespace-only: an empty annotation "
            "is a caller mistake, and a silently empty entity is unreadable"
        )
    if "\x00" in value:
        raise TextValidationError(f"{name} must not contain a NUL character")
    return value


def _positive(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise TextValidationError(f"{name} must be a positive finite number, got {value!r}")
    return number


def _as_point(value: tuple[float, float] | Point2D, name: str) -> Point2D:
    if isinstance(value, Point2D):
        return Point2D(float(value.x), float(value.y))
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise TextValidationError(f"{name} must be an (x, y) pair, got {value!r}")
    return Point2D(float(value[0]), float(value[1]))


def _document_id(doc: Any) -> str:
    for attribute in ("doc_id", "filename", "filepath"):
        value = getattr(doc, attribute, None)
        if isinstance(value, str) and value:
            return value
    return "in-memory"


_DXF_CODES: dict[str, str] = {
    "AC1009": "R12",
    "AC1012": "R13",
    "AC1014": "R14",
    "AC1015": "R2000",
    "AC1018": "R2004",
    "AC1021": "R2007",
    "AC1024": "R2010",
    "AC1027": "R2013",
    "AC1032": "R2018",
}


def _release_of(version: str) -> str:
    if version in _DXF_CODES:
        return _DXF_CODES[version]
    if version.startswith("R"):
        return version
    return version


def _require_ezdxf() -> Any:
    try:
        import ezdxf  # noqa: PLC0415
    except ModuleNotFoundError as exc:  # pragma: no cover - env guard
        raise ModuleNotFoundError(
            "ezdxf is required by the text recorder. On this host run the "
            "project venv with a cleared PYTHONHOME, otherwise the standard "
            "library is hidden and this import fails with "
            "'No module named annotationlib'."
        ) from exc
    return ezdxf
