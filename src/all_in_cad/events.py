from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .models import DocumentRef
from .readback import EntitySnapshot


class DocumentEventKind(StrEnum):
    OPENED = "opened"
    CHANGED = "changed"
    SAVED = "saved"
    CLOSED = "closed"


class DocumentRevisionEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    kind: DocumentEventKind
    fingerprint: str | None = None


class RevisionTracker:
    def __init__(self) -> None:
        self._documents: dict[str, tuple[int, str | None]] = {}

    def open(self, document: DocumentRef, fingerprint: str | None = None) -> DocumentRevisionEvent:
        self._documents[document.document_id] = (document.revision, fingerprint)
        return DocumentRevisionEvent(
            document_id=document.document_id,
            revision=document.revision,
            kind=DocumentEventKind.OPENED,
            fingerprint=fingerprint,
        )

    def observe(self, document_id: str, fingerprint: str) -> DocumentRevisionEvent | None:
        if document_id not in self._documents:
            raise KeyError(f"document is not open: {document_id}")
        revision, previous = self._documents[document_id]
        if previous == fingerprint:
            return None
        revision += 1
        self._documents[document_id] = (revision, fingerprint)
        return DocumentRevisionEvent(
            document_id=document_id,
            revision=revision,
            kind=DocumentEventKind.CHANGED,
            fingerprint=fingerprint,
        )

    def saved(self, document_id: str) -> DocumentRevisionEvent:
        revision, fingerprint = self._require(document_id)
        return DocumentRevisionEvent(
            document_id=document_id,
            revision=revision,
            kind=DocumentEventKind.SAVED,
            fingerprint=fingerprint,
        )

    def close(self, document_id: str) -> DocumentRevisionEvent:
        revision, fingerprint = self._require(document_id)
        del self._documents[document_id]
        return DocumentRevisionEvent(
            document_id=document_id,
            revision=revision,
            kind=DocumentEventKind.CLOSED,
            fingerprint=fingerprint,
        )

    def current_revision(self, document_id: str) -> int:
        revision, _ = self._require(document_id)
        return revision

    def _require(self, document_id: str) -> tuple[int, str | None]:
        try:
            return self._documents[document_id]
        except KeyError as exc:
            raise KeyError(f"document is not open: {document_id}") from exc


def snapshot_fingerprint(items: list[EntitySnapshot]) -> str:
    payload = sorted((item.handle.upper(), item.digest()) for item in items)
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
