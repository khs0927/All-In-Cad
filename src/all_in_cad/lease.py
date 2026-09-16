from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


class LeaseConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class LeaseRecord:
    document_key: str
    owner: str


class DocumentLeaseManager:
    """Process-local exclusive write leases; one authoritative writer per physical DWG."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._leases: dict[str, LeaseRecord] = {}

    @contextmanager
    def acquire(self, document_key: str, owner: str) -> Iterator[LeaseRecord]:
        key = document_key.casefold()
        with self._lock:
            current = self._leases.get(key)
            if current is not None and current.owner != owner:
                raise LeaseConflict(f"document already leased by {current.owner}: {document_key}")
            record = LeaseRecord(document_key=document_key, owner=owner)
            self._leases[key] = record
        try:
            yield record
        finally:
            with self._lock:
                if self._leases.get(key) == record:
                    del self._leases[key]
