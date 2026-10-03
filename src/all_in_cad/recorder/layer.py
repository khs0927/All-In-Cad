"""Layer recorder: creates and describes DXF LAYER table entries.

Same shape as ``wall.py`` / ``door.py`` / ``window.py``: the pure part
(:func:`make_layer`) validates intent and touches no document, the recorded
part (:func:`write_layer` and friends) applies it to an ezdxf document and
returns a frozen record of what actually happened.

What this recorder is for: the geometry recorders in this package reference
layer *names* only. When a recorder runs against a document whose LAYER table
is missing those names, someone has to create the entries and decide their
appearance. That decision is what this module makes explicit and, more
importantly, bounds.

=====================================================================
THE STACK'S OPERATING RULE, AND WHAT IT COSTS HERE
=====================================================================
The project-wide rule is "do not perform an irreversible change". Layer
management is where that rule bites hardest, because a layer is a *named,
drawing-wide* thing: it is not selected, there is no pick, and a wrong
attribute write applies to every entity already on that layer. The command
audit behind this package counted **15 destructive commands** and **37 commands
that apply to the whole drawing without a point selection**; layer operations
sit in the intersection of those two sets.

Therefore, in this module:

* **Creating a layer entry is allowed and is the default-safe operation.**
  Adding a name nothing references yet cannot damage a drawing.
* **Every attribute write to a layer that already exists is refused by
  default** (:class:`DestructiveLayerChange`). Reuse happens by *reporting*
  the existing entry, not by overwriting it. To overwrite you must pass the
  explicit ``allow_overwrite=True`` flag, per call, and the record says which
  attributes were changed. The exception handler for the "I just wanted the
  colour" case is one keyword argument, not an unrecorded mutation.
* **Layer ``"0"`` is additionally protected**: even with ``allow_overwrite``
  its attributes are refused unless ``protect_layer0=False`` is also passed.
  Layer ``"0"`` is the fallback layer for every entity that carries no layer
  name, so it is the single most consequential entry in the table.

=====================================================================
DELETE IS NOT IMPLEMENTED -- AND THAT IS A DESIGN DECISION, NOT A GAP
=====================================================================
There is deliberately **no** ``delete_layer`` / ``purge_layers`` /
``Layers.remove`` wrapper in this module, and no flag that enables one.
Deleting a layer entry either drops the entry (orphaning every entity that
referenced it) or requires a re-parenting sweep across the whole drawing,
which is the classic "erase everything" command. Under this stack's taxonomy
that belongs to the destructive set, and a recorder whose job is to *write*
geometry is the wrong place to host it. See the module-level test
``test_delete_api_is_not_exposed`` which fails if such a symbol is ever added
without a separate, explicit design review.

The same reasoning is why in-place **reordering** of existing LAYER table
entries is not implemented either: ezdxf has no supported API for it, and any
implementation would have to rewrite the whole table. See
:class:`LayerOrderRecord` for what *is* supported: ordering the creation of
new entries, and reporting the resulting table order.

=====================================================================
COLOUR AND LINETYPE CONVENTION: [UNRESOLVED] -- NO DEFAULT IS INVENTED
=====================================================================
``configs/architectural-layers.json`` was read (2026-09-26) and contains only
a name -> semantic table and a preservation policy. A repo-wide search of
``configs/``, ``docs/`` and the Python sources found **no** ACI colour index
table, no per-semantic colour assignment, and no linetype name convention.
The one adjacent note (``docs/ZWCAD-HOST-HANDOFF.md``) records that the
XiCAD text group in ``C:\\xicad`` is a linetype/layer *group definition* --
that document is out of scope for this task and was not opened, so nothing
about its contents is claimed here.

Consequences, all deliberate:

* :func:`make_layer` defaults every appearance attribute to ``None``, which
  means **"unspecified, leave the CAD default alone"**. A freshly created
  layer therefore gets ezdxf's defaults (colour 7, ``Continuous``,
  lineweight -3, plot on) and no project-specific palette is asserted.
* Colours are accepted as an ACI index (int 1..255) only. The ``BYLAYER`` and
  ``BYBLOCK`` keywords are **rejected for a layer**: in the DXF LAYER table a
  layer record carries a concrete colour index, and those two keywords are
  entity attributes that resolve at draw time to an inherited colour. Writing
  one onto a layer either silently becomes colour 0 (off) or is rounded by the
  reader, so the keyword is refused at validation with that reason. The range
  is *validated* because that is verifiable; the choice of a particular index
  is NOT prescribed.
* Linetypes are validated against ``doc.linetypes`` at write time and a
  missing definition is an error rather than a silent dangling reference. DXF
  table names are case-insensitive, so the definition check is
  case-insensitive and the record reports the spelling the document itself
  defines. Which linetype belongs to which semantic is left UNRESOLVED for a
  later worker who has the CAD standard to hand.
* See :data:`COLOR_CONVENTION_STATUS` and :data:`LINETYPE_CONVENTION_STATUS`.

=====================================================================
LAYER NAMING: OBSERVED XiCAD LAYERS vs THE PROJECT CONVENTION -- TWO
DIFFERENT NAME SYSTEMS, DO NOT MERGE THEM
=====================================================================
OBSERVED (entity dump of a wall actually drawn by the running module in ZWCAD,
centreline (0,0)->(12000,0), all entities LINE, all units mm, wall 200 thick):

    layer "0" : centreline LINE (0,0)->(12000,0)
    layer "C" : LINE Y=-100, LINE Y=+100        (two faces, thickness 200)
    layer "S" : LINE Y=-120, LINE Y=+250, LINE Y=+280
    layer "F" : LINE Y=+200

This repository's convention -- ``src/all_in_cad/semantic_layers.py`` and
``configs/architectural-layers.json`` -- names wall layers ``WAL1``/``WAL2``/
``WAL3`` (``LayerSemantic.WALL``) and centreline ``CEN``/``CEN1``
(``LayerSemantic.CENTERLINE``). There is no ``C``/``S``/``F`` entry in that
table at all, and this module does not add one: a single-name lookup like
:classify_layer` would return ``LayerSemantic.UNKNOWN`` for all three.

So the two systems name the same roles differently. Nothing is renamed here:
the observed mapping is exposed verbatim as :data:`OBSERVED_XICAD_LAYERS`
(mapping observed name -> project-convention name) and the default creation
target is the project convention. :func:`observed_layer_specs` builds the
observed view; :func:`convention_layer_specs` builds the default. Creating
``C``/``S``/``F`` would be *safe* but unclassified, so it is opt-in and the
resulting record is flagged ``unclassified=True``.

The audited command category this module stands in for is ``SecLayer`` (35
commands). That count and the per-command breakdown were **not** found in any
in-repo document, so the mapping from this module's operations onto those 35
commands is [UNRESOLVED]: what is stated here is only the operation set
actually implemented, not a claim of coverage.

=====================================================================
ENVIRONMENT NOTE (observed on this host, verified by running it)
=====================================================================
Python lives in ``C:\\Users\\khs09\\all-in-cad\\.venv`` and ezdxf 1.4.4 is
already installed there. Aside injects ``PYTHONHOME`` into the process
environment, which hides the venv interpreter's standard library and makes
``import ezdxf`` fail with ``ModuleNotFoundError: No module named
'annotationlib'``. Always clear both variables before running the tests::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\layer_test.py

This is a host quirk of the launcher, not a defect in this module.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, never needed at runtime
    from pathlib import Path

try:  # package-relative import (normal case)
    from ..semantic_layers import LayerSemantic, classify_layer, normalize_layer_name
except ImportError:  # pragma: no cover - direct/flat execution fallback
    from all_in_cad.semantic_layers import (
        LayerSemantic,
        classify_layer,
        normalize_layer_name,
    )

__all__ = [
    "COLOR_CONVENTION_STATUS",
    "COLOR_KEYWORD_REASON",
    "CONVENTION_LAYER_NAMES",
    "DEFAULT_DXF_WRITE_VERSION",
    "LINETYPE_CONVENTION_STATUS",
    "OBSERVED_XICAD_LAYERS",
    "DestructiveLayerChange",
    "LayerFacts",
    "LayerOrderRecord",
    "LayerRecord",
    "LayerSpec",
    "LayerValidationError",
    "convention_layer_specs",
    "describe_layer",
    "ensure_layer",
    "make_layer",
    "observed_layer_specs",
    "plan_layer_order",
    "write_layer",
    "write_layers",
]

#: DXF version written by this recorder and the oldest version it reads.
DEFAULT_DXF_WRITE_VERSION = "R2018"  # AC1032
DXF_MIN_READ_VERSION = "R2000"  # AC1015

#: [UNRESOLVED] no ACI palette exists in configs/architectural-layers.json.
COLOR_CONVENTION_STATUS = (
    "UNRESOLVED: configs/architectural-layers.json carries no ACI colour index "
    "table and no per-semantic colour. This module therefore assigns no colour "
    "by default (ACI 7 / ezdxf default) and only validates any value it is given."
)

#: [UNRESOLVED] no linetype naming convention exists in the repository.
LINETYPE_CONVENTION_STATUS = (
    "UNRESOLVED: no linetype convention was found in configs/ or docs/. Linetype "
    "names are validated against the document's LTYPE table at write time; which "
    "linetype belongs to which semantic is deliberately not decided here."
)

#: Entity-level colour keywords. They are *not* valid layer colours and are
#: named here only so the rejection message can explain why. See
#: :func:`_validate_color` and the module docstring.
COLOR_KEYWORDS: frozenset[str] = frozenset({"BYLAYER", "BYBLOCK"})

#: Why a BYLAYER/BYBLOCK string is refused as a layer colour.
COLOR_KEYWORD_REASON = (
    "layer colour must be ACI 1..255; BYLAYER/BYBLOCK apply to entities, not layers"
)

#: Valid DXF lineweights (hundredths of a mm) per the DXF reference.
VALID_LINEWEIGHTS: frozenset[int] = frozenset(
    {-3, -2, -1, 0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100,
     106, 120, 140, 158, 200, 211}
)

#: Layer attributes this recorder is willing to write. Anything not in this
#: tuple cannot be set through the recorded part of the module at all.
MANAGED_ATTRIBUTES: tuple[str, ...] = (
    "color",
    "linetype",
    "lineweight",
    "plot",
    "description",
    "on",
    "locked",
    "frozen",
)

#: Observed XiCAD/ZWCAD module layer names -> this project's convention names.
#: [OBSERVED] key side is what the ZWCAD dump literally contained.
#: [DESIGN]  value side reuses names that already exist in
#:            configs/architectural-layers.json; no new name is coined.
OBSERVED_XICAD_LAYERS: dict[str, str] = {
    "0": "CEN1",  # OBSERVED centreline on layer "0"
    "C": "WAL1",  # OBSERVED the two wall faces (Y = +/-100, thickness 200)
    "S": "WAL2",  # OBSERVED three trim/soffit lines
    "F": "WAL3",  # OBSERVED one face/detail line
}

#: [OBSERVED/CONVENTION] the layers a full architectural run needs, in the order
#: they should be created so the LAYER table reads sensibly. Every name is an
#: existing ``exact`` entry of configs/architectural-layers.json.
CONVENTION_LAYER_NAMES: tuple[str, ...] = (
    "CEN1",
    "WAL1",
    "WAL2",
    "WAL3",
    "DOOR",
    "DOOR_ELE",
    "WIN",
    "WINBAR",
    "WINELE",
    "DIM",
    "DIMLE",
    "COL",
    "STAIR",
    "ELE",
)


class LayerValidationError(ValueError):
    """Raised for a layer specification that cannot be represented in DXF."""


class DestructiveLayerChange(LayerValidationError):
    """Raised when a write would destructively change an existing layer.

    This is the fail-closed guard of the stack. The caller must opt in
    explicitly (``allow_overwrite=True``); "I did not need to read the
    docs" is not an acceptable reason to mutate a drawing-wide named object.
    """


def layer_config_path(start: Path | None = None) -> Path | None:
    """Locate ``configs/architectural-layers.json`` by walking upwards.

    Returns ``None`` when no such file exists at or above ``start`` (which
    defaults to this module's own location).

    This exists because of how it used to be done. The tests that assert what
    the repo's layer table does and does not contain each reached it with
    ``Path(__file__).resolve().parents[3] / "configs" / ...``, which silently
    encodes "this package sits exactly four levels below the repository root".
    Move the package -- install it, vendor it, lay the tree out differently --
    and the index points somewhere else entirely. In a wheel install
    ``parents[3]`` does not exist, so the expression raises ``IndexError``
    before the test even runs; in a shallower checkout it silently resolves to
    a different, wrong path. Either way the suite reports a file-not-found in a
    test whose name says nothing about paths, which is a very slow way to
    discover a layout assumption.

    A wrong-but-existing path is the worse case: it turns "this test cannot
    run here" into "this test asserts something about an unrelated file".
    Walking upwards stops the path from being a claim about the layout and
    makes it a question the filesystem answers.
    """
    from pathlib import Path as _Path  # noqa: PLC0415

    base = _Path(start).resolve() if start is not None else _Path(__file__).resolve()
    # A directory is its own first candidate; a file's first candidate is its
    # containing directory.
    candidates = [base, *base.parents] if base.is_dir() else list(base.parents)
    for directory in candidates:
        candidate = directory / "configs" / "architectural-layers.json"
        if candidate.is_file():
            return candidate
    return None


def _canonical(name: str, argument: str = "name") -> str:
    if not isinstance(name, str):
        raise LayerValidationError(f"{argument} must be a string, got {name!r}")
    trimmed = name.strip()
    if not trimmed:
        raise LayerValidationError(f"{argument} must be a non-empty layer name")
    if len(trimmed) > 255:
        raise LayerValidationError(
            f"{argument} must be at most 255 characters, got {len(trimmed)}"
        )
    if any(char in trimmed for char in "\n\r\x00"):
        raise LayerValidationError(f"{argument} must not contain control characters")
    return trimmed


def _validate_color(value: int | str) -> int | str:
    """Validate an ACI colour index for a LAYER table record.

    Only an ACI index in 1..255 is representable on a layer. ``BYLAYER`` and
    ``BYBLOCK`` are entity attributes, not layer attributes, so every string
    is refused here -- including the two keywords -- with the reason in the
    message.

    Validation only. The *choice* of an index has no convention behind it
    (see :data:`COLOR_CONVENTION_STATUS`).
    """
    if isinstance(value, bool):  # bool is an int subclass; reject explicitly
        # Every keyword is refused for a layer, so the message must not offer one.
        raise LayerValidationError(
            "color must be an ACI index (1..255) or None, not a bool"
        )
    if isinstance(value, int):
        if not 1 <= value <= 255:
            raise LayerValidationError(
                f"ACI colour index must be within 1..255, got {value}"
            )
        return int(value)
    if isinstance(value, str):
        keyword = value.strip().upper()
        if keyword in COLOR_KEYWORDS:
            raise LayerValidationError(f"{COLOR_KEYWORD_REASON} (got {keyword!r})")
        raise LayerValidationError(
            f"{COLOR_KEYWORD_REASON}; got the string {value!r}"
        )
    raise LayerValidationError(
        f"color must be an int (ACI 1..255), got {type(value).__name__}"
    )


def _same_layer_value(key: str, current: Any, wanted: Any) -> bool:
    """Compare a requested value against a layer's current value.

    DXF table names are case-insensitive, so a linetype request of
    ``"continuous"`` is the *same* value as the table's ``"Continuous"`` and
    must not be reported as a change. Every other attribute compares by
    equality.
    """
    if isinstance(current, str) and isinstance(wanted, str):
        return current.casefold() == wanted.casefold()
    return current == wanted


def _defined_linetype_name(doc: Any, requested: str) -> str | None:
    """The spelling this document actually defines for ``requested``, if any."""
    wanted_key = requested.strip().casefold()
    for entry in doc.linetypes:
        if entry.dxf.name.casefold() == wanted_key:
            return entry.dxf.name
    return None


def _validate_lineweight(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LayerValidationError(
            f"lineweight must be an int in hundredths of a mm, got {value!r}"
        )
    if value not in VALID_LINEWEIGHTS:
        raise LayerValidationError(
            f"lineweight {value} is not a DXF-valid value; expected one of "
            f"{sorted(VALID_LINEWEIGHTS)}"
        )
    return int(value)


@dataclass(frozen=True, slots=True)
class LayerSpec:
    """Pure description of one LAYER table entry. Touches no document.

    Every appearance attribute defaults to ``None``, which means
    "unspecified". An unspecified attribute is never written: on creation the
    CAD default is kept, and on an existing layer the current value is kept.
    """

    name: str
    color: int | str | None = None
    linetype: str | None = None
    lineweight: int | None = None
    plot: bool | None = None
    description: str | None = None
    on: bool | None = None
    locked: bool | None = None
    frozen: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _canonical(self.name))
        if self.color is not None:
            object.__setattr__(self, "color", _validate_color(self.color))
        if self.lineweight is not None:
            object.__setattr__(self, "lineweight", _validate_lineweight(self.lineweight))
        if self.linetype is not None:
            if not isinstance(self.linetype, str) or not self.linetype.strip():
                raise LayerValidationError("linetype must be a non-empty string")
            object.__setattr__(self, "linetype", self.linetype.strip())
        if self.plot is not None and not isinstance(self.plot, bool):
            raise LayerValidationError("plot must be a bool or None")
        for flag in ("on", "locked", "frozen"):
            value = getattr(self, flag)
            if value is not None and not isinstance(value, bool):
                raise LayerValidationError(f"{flag} must be a bool or None")
        if self.description is not None:
            raise LayerValidationError(
                "description is unsupported: DXF LAYER has no description attribute"
            )
        if self.on is False and self.frozen is True:
            raise LayerValidationError(
                "a layer cannot be requested both off and frozen"
            )

    @property
    def semantic(self) -> LayerSemantic:
        """Semantic role per the project convention (never per the observer)."""
        return classify_layer(self.name)

    @property
    def is_convention_layer(self) -> bool:
        return self.semantic is not LayerSemantic.UNKNOWN

    def specified(self) -> dict[str, Any]:
        """Attributes this spec actually asks for (``None`` values dropped)."""
        return {
            name: getattr(self, name)
            for name in MANAGED_ATTRIBUTES
            if getattr(self, name) is not None
        }

    def validate(self) -> None:
        """Re-run validation. Construction already did it; kept for symmetry."""
        LayerSpec(
            name=self.name,
            color=self.color,
            linetype=self.linetype,
            lineweight=self.lineweight,
            plot=self.plot,
            description=self.description,
            on=self.on,
            locked=self.locked,
            frozen=self.frozen,
        )

    def renamed(self, name: str) -> LayerSpec:
        """Return a copy under a different name (pure; used by observed view)."""
        return LayerSpec(
            name=name,
            color=self.color,
            linetype=self.linetype,
            lineweight=self.lineweight,
            plot=self.plot,
            description=self.description,
            on=self.on,
            locked=self.locked,
            frozen=self.frozen,
        )


@dataclass(frozen=True, slots=True)
class LayerFacts:
    """Read-only snapshot of one existing LAYER table entry."""

    name: str
    exists: bool
    color: int | str | None = None
    linetype: str | None = None
    lineweight: int | None = None
    plot: bool | None = None
    description: str | None = None
    on: bool | None = None
    locked: bool | None = None
    frozen: bool | None = None
    entity_count: int = 0
    table_index: int | None = None
    semantic: LayerSemantic = LayerSemantic.UNKNOWN

    def conflicts_with(self, spec: LayerSpec) -> dict[str, tuple[Any, Any]]:
        """Attributes where ``spec`` asks for a different value than the fact.

        ``None`` (unspecified) never conflicts -- that is exactly the
        "reuse without overwriting" path.
        """
        out: dict[str, tuple[Any, Any]] = {}
        for key, wanted in spec.specified().items():
            current = getattr(self, key)
            if current is None:
                continue
            if not _same_layer_value(key, current, wanted):
                out[key] = (current, wanted)
        return out


@dataclass(frozen=True, slots=True)
class LayerRecord:
    """Result of :func:`write_layer`: what was actually done, and what was not."""

    document_id: str
    name: str
    created: bool
    semantic: LayerSemantic
    applied: Mapping[str, Any] = field(default_factory=dict)
    preserved: Mapping[str, Any] = field(default_factory=dict)
    conflicts: Mapping[str, tuple[Any, Any]] = field(default_factory=dict)
    table_index: int | None = None
    entity_count: int = 0
    unclassified: bool = False
    allow_overwrite: bool = False

    @property
    def reused(self) -> bool:
        return not self.created

    @property
    def changed_existing(self) -> bool:
        return self.reused and bool(self.applied)


@dataclass(frozen=True, slots=True)
class LayerOrderRecord:
    """Result of :func:`write_layers`: the LAYER table order after the write.

    [DESIGN] ezdxf offers no supported way to reorder *existing* LAYER table
    entries, and any implementation would have to rewrite the table -- so this
    module does not pretend to. Ordering is controlled the only safe way: by
    the order in which new entries are created. ``appended`` lists what this
    call added, ``order`` is the resulting table order.
    """

    document_id: str
    requested: tuple[str, ...]
    appended: tuple[str, ...]
    order: tuple[str, ...]
    records: tuple[LayerRecord, ...] = ()

    def index_of(self, name: str) -> int:
        for position, entry in enumerate(self.order):
            if normalize_layer_name(entry) == normalize_layer_name(name):
                return position
        raise KeyError(name)


# ---------------------------------------------------------------- pure part


def make_layer(
    name: str,
    *,
    color: int | str | None = None,
    linetype: str | None = None,
    lineweight: int | None = None,
    plot: bool | None = None,
    description: str | None = None,
    on: bool | None = None,
    locked: bool | None = None,
    frozen: bool | None = None,
) -> LayerSpec:
    """Build a :class:`LayerSpec`. Pure: no document, no CAD, no side effect.

    All appearance arguments default to ``None`` (= unspecified / keep the CAD
    default). Passing a value only asserts *that* value; it never says which
    value is architecturally correct, because no colour or linetype convention
    exists in this repository yet.
    """
    return LayerSpec(
        name=name,
        color=color,
        linetype=linetype,
        lineweight=lineweight,
        plot=plot,
        description=description,
        on=on,
        locked=locked,
        frozen=frozen,
    )


def plan_layer_order(names: Iterable[str]) -> tuple[str, ...]:
    """De-duplicate and validate a creation order. Pure.

    The first occurrence of a name wins its position, so a caller can list a
    semantic grouping without worrying about a name appearing twice.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        name = _canonical(raw, "layer name")
        key = normalize_layer_name(name)
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    if not out:
        raise LayerValidationError("plan_layer_order needs at least one layer name")
    return tuple(out)


def observed_layer_specs() -> tuple[LayerSpec, ...]:
    """Specs reproducing the [OBSERVED] ZWCAD layer names, in observed order.

    [DESIGN] Each observed name is mapped to its project-convention name
    first, so the created entries stay classifiable; ``observed_name=True``
    would mean writing literal ``C``/``S``/``F``, which
    :func:`classify_layer` reports as ``LayerSemantic.UNKNOWN``. The literal
    observed view is available via :func:`observed_layer_specs_literal`; which
    one to use is the caller's decision, so neither is a default.
    """
    return tuple(
        make_layer(convention)
        for observed, convention in OBSERVED_XICAD_LAYERS.items()
    )


def observed_layer_specs_literal() -> tuple[LayerSpec, ...]:
    """Specs carrying the literal [OBSERVED] names ``C``/``S``/``F``/``0``.

    Creating these is safe (they are new table entries) but they are
    **unclassified** under this repository's convention; every resulting
    :class:`LayerRecord` therefore carries ``unclassified=True``, matching the
    ``"unknown_layers": "preserve_and_flag"`` policy in
    ``configs/architectural-layers.json``.
    """
    return tuple(
        make_layer(observed)
        for observed in OBSERVED_XICAD_LAYERS
    )


def convention_layer_specs(names: Sequence[str] = CONVENTION_LAYER_NAMES) -> tuple[LayerSpec, ...]:
    """Specs for the project-convention layers, in the documented order."""
    return tuple(make_layer(name) for name in plan_layer_order(names))


# ------------------------------------------------------------- recorded part


def _require_ezdxf() -> Any:
    try:
        import ezdxf  # noqa: PLC0415
    except ModuleNotFoundError as exc:  # pragma: no cover - env guard
        raise ModuleNotFoundError(
            "ezdxf is required by the layer recorder. On this host run the project "
            "venv with a cleared PYTHONHOME, otherwise the standard library is "
            "hidden and this import fails with 'No module named annotationlib'."
        ) from exc
    return ezdxf


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


def _check_version(doc: Any) -> str:
    doc_version = str(getattr(doc, "dxfversion", "") or "")
    doc_release = str(getattr(doc, "acad_release", "") or "")
    if _release_of(doc_version) != DEFAULT_DXF_WRITE_VERSION or (
        doc_release and doc_release != DEFAULT_DXF_WRITE_VERSION
    ):
        raise ValueError(
            f"this recorder writes {DEFAULT_DXF_WRITE_VERSION} (AC1032) only, "
            f"got {doc_version or doc_release!r}"
        )
    return doc_release or _release_of(doc_version)


def _document_id(doc: Any) -> str:
    for attr in ("filename", "filepath"):
        value = getattr(doc, attr, "") or ""
        if value:
            return str(value)
    return "in-memory"


def _entry_of(doc: Any, name: str) -> Any | None:
    return doc.layers.get(name) if name in doc.layers else None


def _entity_count(entry: Any) -> int:
    """Entities currently on this layer. Read-only count, used for reporting."""
    doc = entry.doc
    if doc is None:  # pragma: no cover - detached entry
        return 0
    try:
        return sum(1 for entity in doc.entitydb.values() if _entity_layer(entity) == entry.dxf.name)
    except Exception:  # pragma: no cover - entitydb shape guard
        return 0


def _entity_layer(entity: Any) -> str | None:
    if getattr(entity, "dxftype", lambda: "")() != "LAYER":
        dxf = getattr(entity, "dxf", None)
        name = getattr(dxf, "layer", None) if dxf is not None else None
        return str(name) if name else None
    return None


def describe_layer(doc: Any, name: str) -> LayerFacts:
    """Read one layer's current state. Pure observation, never mutates."""
    _require_ezdxf()
    _check_version(doc)
    clean = _canonical(name)
    entry = _entry_of(doc, clean)
    if entry is None:
        return LayerFacts(
            name=clean,
            exists=False,
            semantic=classify_layer(clean),
        )
    order = [layer.dxf.name for layer in doc.layers]
    try:
        index = order.index(entry.dxf.name)
    except ValueError:  # pragma: no cover - defensive
        index = None
    return LayerFacts(
        name=entry.dxf.name,
        exists=True,
        color=entry.dxf.get("color", None),
        linetype=entry.dxf.get("linetype", None),
        lineweight=entry.dxf.get("lineweight", None),
        plot=bool(entry.dxf.get("plot", 1)),
        description=str(entry.dxf.description) if entry.dxf.hasattr("description") else None,
        on=not entry.is_off(),
        locked=entry.is_locked(),
        frozen=entry.is_frozen(),
        entity_count=_entity_count(entry),
        table_index=index,
        semantic=classify_layer(entry.dxf.name),
    )


def ensure_layer(doc: Any, name: str) -> LayerRecord:
    """Create the layer if absent; otherwise report it unchanged.

    This is the one non-destructive write. It never modifies an existing
    entry -- that is the whole point of separating it from
    :func:`write_layer` with ``allow_overwrite=True``.
    """
    return write_layer(doc, make_layer(name))


def write_layer(
    doc: Any,
    spec: LayerSpec,
    *,
    allow_overwrite: bool = False,
    protect_layer0: bool = True,
) -> LayerRecord:
    """Write ``spec`` into the LAYER table and report exactly what happened.

    Behaviour matrix:

    * layer absent -> created, specified attributes applied, safe.
    * layer present, spec specifies nothing -> nothing written (reused).
    * layer present, spec asks for a value that already matches -> no write.
    * layer present, spec asks for a *different* value -> **refused** with
      :class:`DestructiveLayerChange` unless ``allow_overwrite=True``.

    ``protect_layer0`` (default) additionally refuses the same write on the
    layer literally named ``"0"`` even when ``allow_overwrite=True``; pass
    ``protect_layer0=False`` for that one case.
    """
    _require_ezdxf()
    _check_version(doc)
    if not isinstance(spec, LayerSpec):
        raise LayerValidationError(f"expected a LayerSpec, got {type(spec).__name__}")
    spec.validate()

    semantic = spec.semantic
    existing = _entry_of(doc, spec.name)
    wanted = spec.specified()

    if existing is None:
        entry = doc.layers.add(spec.name)
        applied = _apply(doc, entry, wanted, fresh=True)
        return LayerRecord(
            document_id=_document_id(doc),
            name=entry.dxf.name,
            created=True,
            semantic=semantic,
            applied=applied,
            table_index=_index_of(doc, entry.dxf.name),
            entity_count=_entity_count(entry),
            unclassified=semantic is LayerSemantic.UNKNOWN,
            allow_overwrite=allow_overwrite,
        )

    facts = describe_layer(doc, spec.name)
    conflicts = facts.conflicts_with(spec)
    effective = {
        key: value
        for key, value in wanted.items()
        if not _same_layer_value(key, getattr(facts, key), value)
    }
    # Protection blocks *changes*, not requests. Re-asserting what layer "0"
    # already carries changes nothing, so it is a no-op success rather than a
    # refusal -- refusing it would train callers to pass protect_layer0=False
    # for requests that are in fact harmless.
    if effective and normalize_layer_name(spec.name) == "0" and protect_layer0:
        raise DestructiveLayerChange(
            f"layer '0' is protected: refusing to change "
            f"{sorted(effective)}. Pass protect_layer0=False to override, and "
            f"only if a whole-drawing change is genuinely intended."
        )
    if conflicts and not allow_overwrite:
        detail = ", ".join(
            f"{key} {current!r} -> {new!r}" for key, (current, new) in sorted(conflicts.items())
        )
        raise DestructiveLayerChange(
            f"refusing to change existing layer {spec.name!r}: {detail}. "
            f"This layer already carries {facts.entity_count} entities, so the "
            f"change applies to the whole drawing. Pass allow_overwrite=True to "
            f"record the change explicitly."
        )

    applied = _apply(doc, existing, effective, fresh=False) if effective else {}
    preserved = {
        key: getattr(facts, key) for key in MANAGED_ATTRIBUTES if key not in applied
    }
    return LayerRecord(
        document_id=_document_id(doc),
        name=existing.dxf.name,
        created=False,
        semantic=semantic,
        applied=applied,
        preserved=preserved,
        conflicts=dict(conflicts),
        table_index=_index_of(doc, existing.dxf.name),
        entity_count=_entity_count(existing),
        unclassified=semantic is LayerSemantic.UNKNOWN,
        allow_overwrite=allow_overwrite,
    )


def write_layers(
    doc: Any,
    specs: Iterable[LayerSpec],
    *,
    allow_overwrite: bool = False,
    protect_layer0: bool = True,
) -> LayerOrderRecord:
    """Write several layers, creating them in the given order.

    This is the supported way to manage layer *order*: new entries land in
    creation order. Reordering entries that already exist is not implemented
    (see :class:`LayerOrderRecord`).
    """
    _require_ezdxf()
    _check_version(doc)
    items = tuple(specs)
    if not items:
        raise LayerValidationError("write_layers needs at least one LayerSpec")
    seen: set[str] = set()
    records: list[LayerRecord] = []
    appended: list[str] = []
    for spec in items:
        if not isinstance(spec, LayerSpec):
            raise LayerValidationError(
                f"expected a LayerSpec, got {type(spec).__name__}"
            )
        key = normalize_layer_name(spec.name)
        record = write_layer(
            doc,
            spec,
            allow_overwrite=allow_overwrite,
            protect_layer0=protect_layer0,
        )
        if key in seen:
            # A duplicate name in one plan is a caller bug, not a new write.
            continue
        seen.add(key)
        records.append(record)
        if record.created:
            appended.append(record.name)
    return LayerOrderRecord(
        document_id=_document_id(doc),
        requested=tuple(spec.name for spec in items),
        appended=tuple(appended),
        order=tuple(layer.dxf.name for layer in doc.layers),
        records=tuple(records),
    )


def _index_of(doc: Any, name: str) -> int | None:
    order = [layer.dxf.name for layer in doc.layers]
    try:
        return order.index(name)
    except ValueError:  # pragma: no cover - defensive
        return None


def _apply(doc: Any, entry: Any, wanted: Mapping[str, Any], *, fresh: bool) -> dict[str, Any]:
    """Apply the specified attributes. Validation happens before any write."""
    # DXF table names are case-insensitive: resolve the request to the
    # spelling the document defines, so a re-request of "continuous" is
    # recognised as the existing "Continuous" and the record reports the
    # document's own name rather than the caller's casing.
    resolved: dict[str, Any] = dict(wanted)
    if "linetype" in wanted:
        name = wanted["linetype"]
        available = {lt.dxf.name for lt in doc.linetypes}
        defined = _defined_linetype_name(doc, name)
        if defined is None and name.strip().upper() not in {"BYLAYER", "BYBLOCK"}:
            raise LayerValidationError(
                f"linetype {name!r} is not defined in this document "
                f"(available: {sorted(available)}). Refusing to leave a dangling "
                f"LTYPE reference. Which linetype to use is UNRESOLVED in this "
                f"repository -- see LINETYPE_CONVENTION_STATUS."
            )
        if defined is not None:
            resolved["linetype"] = defined
    applied: dict[str, Any] = {}
    for key, value in resolved.items():
        if key == "color":
            try:
                entry.dxf.color = value
            except (TypeError, ValueError) as exc:
                raise LayerValidationError(
                    f"color value {value!r} cannot be represented by DXF LAYER"
                ) from exc
        elif key == "linetype":
            entry.dxf.linetype = value
        elif key == "lineweight":
            entry.dxf.lineweight = value
        elif key == "plot":
            entry.dxf.plot = 1 if value else 0
        elif key == "on":
            entry.off() if not value else entry.on()
        elif key == "locked":
            entry.unlock() if not value else entry.lock()
        elif key == "frozen":
            entry.thaw() if not value else entry.freeze()
        else:  # pragma: no cover - MANAGED_ATTRIBUTES is closed
            raise LayerValidationError(f"unmanaged layer attribute {key!r}")
        applied[key] = value
    return applied
