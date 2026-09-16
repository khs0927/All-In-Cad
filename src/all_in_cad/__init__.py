"""All-In-Cad core contracts."""

from .approval import ApprovalError, ApprovalSigner, plan_digest
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
from .worker import InMemoryWorkerBackend, RpcDispatcher, WorkerBackend

__all__ = [
    "AdapterChannel",
    "AdapterDescriptor",
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
    "JournalConflict",
    "LayerSemantic",
    "LeaseConflict",
    "MAX_FRAME_BYTES",
    "NativeMethod",
    "PROTOCOL_VERSION",
    "ProtocolError",
    "RoutingError",
    "RpcDispatcher",
    "RpcError",
    "RpcRequest",
    "RpcResponse",
    "SnapshotDiff",
    "VerificationFinding",
    "VerificationReport",
    "WorkerBackend",
    "classify_layer",
    "decode_body",
    "diff_snapshots",
    "encode_frame",
    "plan_digest",
]
