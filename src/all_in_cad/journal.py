from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class JournalConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class JournalEntry:
    key: str
    request_hash: str
    status: str
    response: dict[str, Any] | None


class IdempotencyJournal:
    """Small durable journal for accepted/committed/failed write requests."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(path))
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS operations (
                key TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                response_json TEXT
            )
            """
        )
        self._connection.commit()

    def reserve(self, key: str, request_hash: str) -> JournalEntry:
        row = self._connection.execute(
            "SELECT request_hash, status, response_json FROM operations WHERE key = ?", (key,)
        ).fetchone()
        if row is not None:
            existing_hash, status, response_json = row
            if existing_hash != request_hash:
                raise JournalConflict("idempotency key reused for a different request")
            response = json.loads(response_json) if response_json else None
            return JournalEntry(key, request_hash, status, response)

        self._connection.execute(
            "INSERT INTO operations(key, request_hash, status) VALUES (?, ?, 'accepted')",
            (key, request_hash),
        )
        self._connection.commit()
        return JournalEntry(key, request_hash, "accepted", None)

    def commit(self, key: str, response: dict[str, Any]) -> None:
        self._connection.execute(
            "UPDATE operations SET status = 'committed', response_json = ? WHERE key = ?",
            (json.dumps(response, sort_keys=True), key),
        )
        self._connection.commit()

    def fail(self, key: str, error: str) -> None:
        self._connection.execute(
            "UPDATE operations SET status = 'failed', response_json = ? WHERE key = ?",
            (json.dumps({"error": error}), key),
        )
        self._connection.commit()
