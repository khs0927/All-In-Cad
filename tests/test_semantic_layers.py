from all_in_cad.semantic_layers import LayerSemantic, classify_layer


def test_office_layer_conventions() -> None:
    expected = {
        "COL": LayerSemantic.COLUMN,
        "WAL1": LayerSemantic.WALL,
        "WAL2": LayerSemantic.WALL,
        "WAL3": LayerSemantic.WALL,
        "ELE": LayerSemantic.ELEVATOR,
        "DOOR": LayerSemantic.DOOR,
        "DOOR_ELE": LayerSemantic.DOOR,
        "WIN": LayerSemantic.WINDOW,
        "WINBAR": LayerSemantic.WINDOW_BAR,
        "WINELE": LayerSemantic.WINDOW,
        "STAIR": LayerSemantic.STAIR,
        "DIM": LayerSemantic.DIMENSION,
        "DIMLE": LayerSemantic.DIMENSION,
        "CEN": LayerSemantic.CENTERLINE,
        "CEN1": LayerSemantic.CENTERLINE,
    }
    for layer, semantic in expected.items():
        assert classify_layer(layer) == semantic


def test_generic_fallback_patterns() -> None:
    assert classify_layer("A-WALL-EXST") == LayerSemantic.WALL
    assert classify_layer("A_DOOR_NEW") == LayerSemantic.DOOR
    assert classify_layer("random") == LayerSemantic.UNKNOWN
