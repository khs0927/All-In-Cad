"""All-In-Cad core contracts."""

from .approval import ApprovalError, ApprovalSigner, plan_digest
from .architecture import (
    AnnotationBinding,
    OpeningHostRelation,
    RoomAdjacency,
    RoomCandidate,
    infer_annotation_bindings,
    infer_opening_hosts,
    infer_room_adjacency,
    infer_rooms,
)
from .executor import ExecutionError, GuardedExecutor, InMemoryNativeAdapter
from .journal import IdempotencyJournal, JournalConflict
from .lease import DocumentLeaseManager, LeaseConflict
from .models import (
    AdapterChannel,
    AdapterDescriptor,
    ChangeOperation,
    ChangePlan,
    DocumentRef,
    EntityRef,
    ExecutionReceipt,
    HostKind,
    VerificationFinding,
    VerificationReport,
)
from .project_index import IndexResult, ProjectIndex, ReferenceRecord, file_fingerprint
from .protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    FrameDecoder,
    NativeMethod,
    ProtocolError,
    RpcError,
    RpcRequest,
    RpcResponse,
    decode_body,
    encode_frame,
)
from .readback import DiffKind, EntityDiff, EntitySnapshot, SnapshotDiff, diff_snapshots
from .routing import CapabilityRouter, RoutingError
from .semantic_layers import LayerSemantic, classify_layer
from .topology import NodedSegment, Point2D, Segment2D, node_segments, segments_from_entities
from .worker import InMemoryWorkerBackend, RpcDispatcher, WorkerBackend

__all__ = [
    "AdapterChannel",
    "AdapterDescriptor",
    "AnnotationBinding",
    "ApprovalError",
    "ApprovalSigner",
    "CapabilityRouter",
    "ChangeOperation",
    "ChangePlan",
    "DiffKind",
    "DocumentLeaseManager",
    "DocumentRef",
    "EntityDiff",
    "EntityRef",
    "EntitySnapshot",
    "ExecutionError",
    "ExecutionReceipt",
    "FrameDecoder",
    "GuardedExecutor",
    "HostKind",
    "IdempotencyJournal",
    "InMemoryNativeAdapter",
    "InMemoryWorkerBackend",
    "IndexResult",
    "JournalConflict",
    "LayerSemantic",
    "LeaseConflict",
    "MAX_FRAME_BYTES",
    "NativeMethod",
    "NodedSegment",
    "OpeningHostRelation",
    "PROTOCOL_VERSION",
    "Point2D",
    "ProjectIndex",
    "ProtocolError",
    "ReferenceRecord",
    "RoomAdjacency",
    "RoomCandidate",
    "RoutingError",
    "RpcDispatcher",
    "RpcError",
    "RpcRequest",
    "RpcResponse",
    "Segment2D",
    "SnapshotDiff",
    "VerificationFinding",
    "VerificationReport",
    "WorkerBackend",
    "classify_layer",
    "decode_body",
    "diff_snapshots",
    "encode_frame",
    "file_fingerprint",
    "infer_annotation_bindings",
    "infer_opening_hosts",
    "infer_room_adjacency",
    "infer_rooms",
    "node_segments",
    "plan_digest",
    "segments_from_entities",
]
