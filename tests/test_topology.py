from all_in_cad.topology import Point2D, Segment2D, node_segments


def test_crossing_segments_are_noded_into_four_pieces() -> None:
    segments = [
        Segment2D("A", Point2D(0, 0), Point2D(10, 0)),
        Segment2D("B", Point2D(5, -5), Point2D(5, 5)),
    ]
    noded = node_segments(segments)
    assert len(noded) == 4
    assert Point2D(5, 0) in {item.start for item in noded} | {item.end for item in noded}


def test_collinear_endpoint_nodes_longer_segment() -> None:
    segments = [
        Segment2D("A", Point2D(0, 0), Point2D(10, 0)),
        Segment2D("B", Point2D(5, 0), Point2D(15, 0)),
    ]
    noded = node_segments(segments)
    keys = {item.undirected_key() for item in noded}
    assert (Point2D(0, 0), Point2D(5, 0)) in keys
    assert (Point2D(5, 0), Point2D(10, 0)) in keys
    assert (Point2D(10, 0), Point2D(15, 0)) in keys
    overlap = next(
        item
        for item in noded
        if item.undirected_key() == (Point2D(5, 0), Point2D(10, 0))
    )
    assert overlap.source_handles == ("A", "B")


def test_nearby_endpoints_snap_deterministically() -> None:
    segments = [
        Segment2D("A", Point2D(0, 0), Point2D(5.0001, 0)),
        Segment2D("B", Point2D(5, 0), Point2D(10, 0)),
    ]
    noded = node_segments(segments, tolerance=0.001)
    assert len(noded) == 2
    assert any(Point2D(5, 0) in item.undirected_key() for item in noded)
