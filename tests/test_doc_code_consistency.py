"""Guard recorder documentation against drift from the verify implementation.

Every assertion here is deliberately total: extraction from ``cli.py`` fails
closed instead of skipping what it cannot read, so a refactor cannot silently
remove a check from the set that the documentation is compared against.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src/all_in_cad/recorder/cli.py"
PIPELINE = ROOT / "docs/RECORDER-PIPELINE.md"
README = ROOT / "src/all_in_cad/recorder/README.md"
HANDOFF = ROOT / "docs/ZWCAD-HOST-HANDOFF.md"
WALL = ROOT / "src/all_in_cad/recorder/wall.py"
BLOCK = ROOT / "src/all_in_cad/recorder/block.py"

#: Marker that introduces the committed list of extracted verify check names in
#: ``docs/RECORDER-PIPELINE.md``. That list is the one place where the docs
#: claim to enumerate the checks, so it is compared in BOTH directions instead
#: of only "every code name is documented somewhere".
DOC_CHECK_LIST_MARKER = "코드에서 추출한 검사 이름"

#: Marker / terminator for the observed wall entity dump in ``wall.py``.
OBSERVED_MARKER = "[OBSERVED] Entity dump"
OBSERVED_END_MARKER = "The project convention"

#: Committed lower bound on the number of ``_check`` names extracted from
#: ``cli.py``. The live tree yields **71** names (measured on this tree); the
#: floor sits well below that so a legitimate check removal does not trip it,
#: but far above the "extract a handful and compare against nothing" hole that
#: the previous ``assert checks`` (an effective floor of 1) left open.
#: ``SNAPSHOT_CHECK_NAMES`` is the exact, stricter companion.
MIN_CHECK_NAMES = 60

#: Every verify check name the project documents. If one disappears from the
#: code -- deleted, renamed, or routed through a module constant / helper so the
#: AST no longer sees a string literal -- this set stops being a subset of the
#: extraction and the test fails. That is the reverse direction: the docs are
#: longer than the code.
SNAPSHOT_CHECK_NAMES = frozenset(
    {
        "block_definition_present",
        "block_name_matches_expectation",
        "centerline_midway_between_faces",
        "dim_measurement_is_readable",
        "dim_measurement_matches_expectation",
        "dim_measurement_positive",
        "dim_present",
        "dim_text_matches_measurement",
        "door_center_matches_expectation",
        "door_frame_lines_parallel_to_opening_edges",
        "door_hinge_offset_is_half_width",
        "door_hinge_on_an_opening_edge",
        "door_opening_edges_identified",
        "door_opening_width_matches_swing_arc",
        "door_opening_width_positive",
        "door_present",
        "door_single_swing_arc",
        "door_width_matches_expectation",
        "entity_count_matches_expectation",
        "entity_count_reported",
        "hatch_area_matches_expectation",
        "hatch_area_positive",
        "hatch_boundary_is_closed",
        "hatch_pattern_matches_expectation",
        "hatch_pattern_name_present",
        "hatch_present",
        "hatch_vertex_count_at_least_three",
        "insert_downstream_visibility",
        "layer_names_match_expectation",
        "layer_semantics_mapped",
        "layers_table_has_content",
        "no_hatch_entity",
        "opening_boundary_crosses_wall_thickness",
        "opening_boundary_edges_centred_on_centreline",
        "opening_boundary_edges_identified",
        "opening_boundary_perpendicular_to_face_line",
        "opening_boundary_spans_equal_wall_thickness",
        "opening_boundary_symmetric_about_wall_centreline",
        "opening_centre_tick_is_half_thickness",
        "opening_layers_are_inert_temp_layers",
        "opening_present",
        "opening_ticks_are_45_degree",
        "opening_width_matches_expectation",
        "opening_width_matches_face_line",
        "text_content_matches_expectation",
        "text_content_non_empty",
        "text_height_is_positive_finite",
        "text_present",
        "wall_cap_length_equals_thickness",
        "wall_caps_present",
        "wall_centerline_reported",
        "wall_faces_equal_length",
        "wall_measurable",
        "wall_thickness_matches_expectation",
        "wall_thickness_positive",
        "window_bars_centred_on_centreline",
        "window_bars_perpendicular_to_width_axis",
        "window_bars_spaced_evenly_within_opening",
        "window_bars_within_opening_width",
        "window_casement_arc_hinges_on_the_jamb",
        "window_casement_arc_radius_matches_opening",
        "window_glazing_line_identified",
        "window_interior_face_line_present",
        "window_interior_face_offset_is_half_thickness",
        "window_interior_side_agrees_with_casement_sweep",
        "window_jamb_edges_centred_on_centreline",
        "window_jamb_edges_perpendicular_to_width_axis",
        "window_jamb_edges_span_equal_thickness",
        "window_opening_width_matches_glazing_line",
        "window_present",
        "window_single_casement_arc",
        "window_width_matches_expectation",
    }
)

#: The only helper-built check name in ``cli.py``. Its first argument is still a
#: string literal, so the check is extracted from the literal rather than from
#: the call result. Any *other* non-literal first argument is a hole and fails
#: closed in ``_check_names_and_forbidden_types``.
EXPECTATION_CHECK_NAME_HELPER = "expectation_check_name"


def _check_names_and_forbidden_types() -> tuple[set[str], set[str]]:
    tree = ast.parse(CLI.read_text(encoding="utf-8"))
    checks: set[str] = set()
    forbidden: set[str] | None = None
    unreadable: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_check"
        ):
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                checks.add(first.value)
            elif (
                isinstance(first, ast.Call)
                and isinstance(first.func, ast.Name)
                and first.func.id == EXPECTATION_CHECK_NAME_HELPER
                and first.args
                and isinstance(first.args[0], ast.Constant)
                and isinstance(first.args[0].value, str)
            ):
                # Repeatable expectation check: the bare name is still a literal.
                checks.add(first.args[0].value)
            else:
                rendered = ast.unparse(first) if first is not None else "<no arguments>"
                unreadable.append(f"cli.py:{node.lineno} _check({rendered})")
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == "FORBIDDEN_DXF_TYPES" for t in targets):
                value = node.value
                if isinstance(value, (ast.Tuple, ast.List)):
                    forbidden = {
                        elt.value
                        for elt in value.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    }
    assert not unreadable, (
        "every _check call in cli.py must take a string-literal name; these calls would "
        "silently drop their check from the documentation comparison: " + ", ".join(unreadable)
    )
    assert checks, "verify check names must be extractable from cli.py _check calls"
    assert len(checks) >= MIN_CHECK_NAMES, (
        f"only {len(checks)} verify check names extracted from cli.py, below the committed "
        f"floor of {MIN_CHECK_NAMES}; the extraction is partially broken"
    )
    assert forbidden is not None, "FORBIDDEN_DXF_TYPES must be extractable from cli.py"
    return checks, forbidden


def _documented_check_list(pipeline: str) -> set[str]:
    """Return the check names the pipeline document commits to enumerating."""
    lines = pipeline.splitlines()
    start = next((i for i, line in enumerate(lines) if DOC_CHECK_LIST_MARKER in line), None)
    assert start is not None, (
        f"docs/RECORDER-PIPELINE.md no longer contains the check-list marker "
        f"{DOC_CHECK_LIST_MARKER!r}; the reverse direction cannot be checked"
    )
    token = re.compile(r"`([a-z][a-z0-9_]*)`")
    names: set[str] = set()
    for line in lines[start + 1 :]:
        found = token.fullmatch(line.strip())
        if found:
            names.add(found.group(1))
            continue
        if not line.strip():
            # Blank lines are ignored so the list cannot be silently truncated by
            # reformatting; the first non-blank, non-token line ends it.
            continue
        break
    assert names, (
        "the committed verify check-name list in docs/RECORDER-PIPELINE.md is empty; the "
        "marker was found but no back-ticked names follow it"
    )
    return names


def _assert_consistent(pipeline: str, readme: str) -> None:
    checks, forbidden = _check_names_and_forbidden_types()
    combined = pipeline + "\n" + readme
    mentioned = set(re.findall(r"`([a-z][a-z0-9_]*)`", combined))
    missing = sorted(checks - mentioned)
    assert not missing, f"verify check names missing from recorder docs: {missing}"
    assert "no_insert_or_hatch" not in mentioned, "stale check name no_insert_or_hatch remains"

    # Reverse direction: every name the docs enumerate must exist in the code.
    documented = _documented_check_list(pipeline)
    undocumented = sorted(documented - checks)
    assert not undocumented, (
        "docs/RECORDER-PIPELINE.md lists verify check names that do not exist in cli.py: "
        f"{undocumented}"
    )
    unlisted = sorted(checks - documented)
    assert not unlisted, (
        "verify check names exist in cli.py but are absent from the committed list in "
        f"docs/RECORDER-PIPELINE.md: {unlisted}"
    )

    # The extraction must not have lost a check the project still documents.
    lost = sorted(SNAPSHOT_CHECK_NAMES - checks)
    assert not lost, (
        "verify check names documented in the project are no longer extractable from cli.py "
        f"_check calls: {lost}"
    )

    # Blanket prohibitions are contradictory when the live tuple permits INSERT.
    if "INSERT" not in forbidden:
        assert not re.search(
            r"(?:INSERT\s*(?:도\s*)?HATCH\s*(?:도\s*)?(?:없다|금지|전무)"
            r"|INSERT.{0,12}(?:금지|전무)|(?:없다|전무).{0,12}INSERT)",
            combined,
        ), "docs still claim INSERT is categorically forbidden"

    # The docs must state the policy in both directions, based on the live tuple.
    if "HATCH" in forbidden:
        assert re.search(r"HATCH.{0,60}(?:금지|거부)|(?:금지|거부).{0,60}HATCH", combined, re.S), \
            "FORBIDDEN_DXF_TYPES includes HATCH but docs do not say HATCH is forbidden"
    else:
        assert "HATCH" not in forbidden
    if "INSERT" in forbidden:
        assert re.search(
            r"INSERT.{0,60}(?:금지|거부)|(?:금지|거부).{0,60}INSERT", combined, re.S
        ), \
            "FORBIDDEN_DXF_TYPES includes INSERT but docs do not say INSERT is forbidden"
    else:
        assert re.search(r"INSERT.{0,100}(?:허용|allowed)", combined, re.S), \
            "INSERT is not forbidden in code but docs do not state that it is allowed"
        assert "flatten" in readme.lower(), "README must document flatten as the default"


def _module_constant(path: Path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            assert node.value is not None, f"{path.name}:{name} has no assigned value"
            return node.value
    raise AssertionError(f"{name} must be extractable from {path.name}")


def test_recorder_docs_match_live_verify_contract() -> None:
    _assert_consistent(PIPELINE.read_text(encoding="utf-8"), README.read_text(encoding="utf-8"))


def test_observed_wall_count_matches_source_inventory() -> None:
    """Count whole-word LINE records and cross-check the observed offsets table."""
    module_doc = ast.get_docstring(ast.parse(WALL.read_text(encoding="utf-8")))
    assert module_doc is not None, "wall.py module docstring must exist"
    assert OBSERVED_MARKER in module_doc, (
        f"wall.py docstring no longer contains the observed-dump marker {OBSERVED_MARKER!r}; "
        "the entity inventory cannot be located"
    )
    assert OBSERVED_END_MARKER in module_doc, (
        f"wall.py docstring no longer contains the dump terminator {OBSERVED_END_MARKER!r}; "
        "the observed slice would run to the end of the docstring"
    )
    observed = module_doc.split(OBSERVED_MARKER, 1)[1].split(OBSERVED_END_MARKER, 1)[0]
    # Whole-word LINE only: LWPOLYLINE / POLYLINE / XLINE are different entity
    # types and must not be credited as LINE evidence.
    entity_lines = [
        line
        for line in observed.splitlines()
        if re.search(r"\bLINE\b", line) and "layer" in line
    ]
    counts = [len(re.findall(r"\bLINE\b", line)) for line in entity_lines]
    assert counts == [1, 2, 3, 1], (
        "expected the four observed layer entries (1+2+3+1 whole-word LINE records) in the "
        f"wall.py dump, found {counts}"
    )
    # The four observed layer entries are 1+2+3+1. Validate documentation against
    # that inventory rather than copying a separate entity-count snapshot.
    observed_count = sum(counts)
    offsets = _module_constant(WALL, "OBSERVED_XICAD_LINE_OFFSETS_MM")
    assert isinstance(offsets, ast.Dict), "OBSERVED_XICAD_LINE_OFFSETS_MM must be a dict literal"
    offset_total = sum(len(v.elts) for v in offsets.values if isinstance(v, ast.Tuple))
    assert offset_total == 7, (
        "OBSERVED_XICAD_LINE_OFFSETS_MM no longer describes 7 observed LINE entities "
        f"({offset_total}); update the dump, the constant and the handoff together"
    )
    handoff = HANDOFF.read_text(encoding="utf-8")
    assert observed_count == offset_total, (
        "wall.py observed entity inventory changed: the dump lists "
        f"{observed_count} whole-word LINE records across {len(entity_lines)} layer lines but "
        f"OBSERVED_XICAD_LINE_OFFSETS_MM describes {offset_total}"
    )
    assert observed_count == 7, f"wall.py observed entity inventory changed: {observed_count}"
    assert re.search(r"7개 엔티티", handoff), "handoff must state the observed seven-entity count"
    assert "9개 엔티티 전부" not in handoff, "handoff repeats the disproven nine-entity observation"


def _layer_argument_keywords() -> dict[str, str]:
    """Map each recorder subcommand's layer flag to its add_argument keywords.

    Only the four layer-bearing recorders (hatch / text / block / dim) are
    attributed. ``add_parser("hatch")`` names the subparser positionally and
    the arguments live in a separate ``_add_hatch_arguments(parser)`` helper,
    so ownership is resolved through the call chain: helper function name ->
    subcommand, taken from the ``<var> = subparsers.add_parser(<name>)``
    assignment followed by ``_add_*_arguments(<var>)`` in the same builder.
    """
    tree = ast.parse(CLI.read_text(encoding="utf-8"))
    parser_vars: dict[str, str] = {}
    helper_owner: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not (isinstance(call.func, ast.Attribute) and call.func.attr == "add_parser"):
            continue
        sub = None
        for cand in list(call.args) + [kw.value for kw in call.keywords if kw.arg == "name"]:
            try:
                sub = ast.literal_eval(cand)
            except (ValueError, SyntaxError):
                continue
            if isinstance(sub, str):
                break
        if (
            not isinstance(sub, str)
            or len(node.targets) != 1
            or not isinstance(node.targets[0], ast.Name)
        ):
            continue
        var = node.targets[0].id
        parser_vars[var] = sub
    # Second pass: every call of the form _add_x_arguments(<parser var>).
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if not node.args or not isinstance(node.args[0], ast.Name):
            continue
        sub = parser_vars.get(node.args[0].id)
        if sub is not None:
            helper_owner.setdefault(node.func.id, sub)
    helpers = {
        n.name: n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
    }
    result: dict[str, str] = {}
    for helper, sub in helper_owner.items():
        if sub not in ("hatch", "text", "block", "dim"):
            continue
        body = helpers.get(helper)
        if body is None:
            continue
        for node in ast.walk(body):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
            ):
                continue
            flags = [
                a.value
                for a in node.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)
            ]
            if not any(f in ("--layer", "--entity-layer") for f in flags):
                continue
            result[sub] = ", ".join(
                f"{kw.arg}={ast.unparse(kw.value)}" for kw in node.keywords if kw.arg
            )
    return result


def test_block_layer_contract_is_classify_based_and_documented() -> None:
    """block refuses layers by classification; the 8 convention names are advice."""
    layers = _module_constant(BLOCK, "CONVENTION_BLOCK_LAYERS")
    assert isinstance(layers, ast.Tuple), "CONVENTION_BLOCK_LAYERS must be a tuple literal"
    names = [
        elt.value
        for elt in layers.elts
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
    ]
    assert len(names) == 8, f"CONVENTION_BLOCK_LAYERS must name 8 convention layers, found {names}"

    block_tree = ast.parse(BLOCK.read_text(encoding="utf-8"))
    # No membership test against the tuple: the refusal is driven by
    # classify_layer(), so the 8 names are only the recommended list in the error
    # message. If a membership check is added, this fails and the docs (which
    # state the classify-based rule) must be updated with it.
    membership = [
        f"block.py:{node.lineno}"
        for node in ast.walk(block_tree)
        if isinstance(node, ast.Compare)
        and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
        and "CONVENTION_BLOCK_LAYERS" in ast.unparse(node)
    ]
    assert not membership, (
        "block.py now validates layers by membership in CONVENTION_BLOCK_LAYERS "
        f"({membership}); docs/RECORDER-PIPELINE.md and recorder/README.md state the refusal "
        "is classify_layer() == UNKNOWN based and must be updated"
    )
    validator = next(
        (
            n
            for n in ast.walk(block_tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_validate_layer"
        ),
        None,
    )
    assert validator is not None, "block._validate_layer must exist"
    assert "classify_layer" in ast.unparse(validator), (
        "block._validate_layer must refuse layers via classify_layer(), not a list check"
    )

    options = _layer_argument_keywords()
    for sub in ("hatch", "text", "block", "dim"):
        assert sub in options, f"cli.py no longer has a --layer/--entity-layer argument for {sub}"
    for sub in ("hatch", "text", "block"):
        assert "required=True" in options[sub], (
            f"cli.py {sub} layer option must be required=True because its layer status is "
            f"UNRESOLVED: {options[sub]}"
        )
    assert "required=True" not in options["dim"], (
        "cli.py dim --layer must keep its observed default instead of becoming "
        f"required: {options['dim']}"
    )
    assert "default=" in options["dim"], f"cli.py dim --layer must keep a default: {options['dim']}"

    combined = PIPELINE.read_text(encoding="utf-8") + "\n" + README.read_text(encoding="utf-8")
    assert "CONVENTION_BLOCK_LAYERS" in combined, "docs must name CONVENTION_BLOCK_LAYERS"
    assert re.search(r"CONVENTION_BLOCK_LAYERS.{0,80}8", combined, re.S) or re.search(
        r"8.{0,40}CONVENTION_BLOCK_LAYERS", combined, re.S
    ), "docs must state that CONVENTION_BLOCK_LAYERS holds 8 names"
    assert re.search(
        r"classify_layer.{0,80}UNKNOWN|UNKNOWN.{0,80}classify_layer", combined, re.S
    ), (
        "docs must state that the layer refusal is driven by classify_layer() returning UNKNOWN"
    )


def test_insert_ban_mutation_is_rejected() -> None:
    """Mutation check: restore the obsolete blanket-ban wording in a temp copy."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "README.md"
        mutated = README.read_text(encoding="utf-8").replace(
            "INSERT 는 `block --mode insert` 에서 허용되지만 기본 동작은 `flatten` 이다.",
            "블록 INSERT 도 HATCH 도 없다. INSERT 는 금지다.",
        )
        copy.write_text(mutated, encoding="utf-8")
        try:
            _assert_consistent(PIPELINE.read_text(encoding="utf-8"), mutated)
        except AssertionError:
            return
        raise AssertionError("mutation survived: reintroduced INSERT ban was not detected")
