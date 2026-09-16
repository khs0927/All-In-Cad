from all_in_cad.events import RevisionTracker, snapshot_fingerprint
from all_in_cad.models import DocumentRef, HostKind
from all_in_cad.readback import EntitySnapshot


def entity(x: float) -> EntitySnapshot:
    return EntitySnapshot(
        document_id="doc",
        handle="A",
        entity_type="LINE",
        layer="WAL1",
        geometry={"start": [x, 0], "end": [100, 0]},
    )


def test_revision_tracker_dedupes_same_fingerprint_and_increments_changes() -> None:
    tracker = RevisionTracker()
    document = DocumentRef(
        host=HostKind.ZWCAD,
        host_version="2026",
        document_id="doc",
        revision=7,
    )
    first = snapshot_fingerprint([entity(0)])
    tracker.open(document, first)
    assert tracker.observe("doc", first) is None
    changed = tracker.observe("doc", snapshot_fingerprint([entity(1)]))
    assert changed is not None
    assert changed.revision == 8
    assert tracker.saved("doc").revision == 8
    assert tracker.close("doc").revision == 8
