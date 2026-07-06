"""SQLite persistence for local conversation records."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ConversationStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save_answer(
        self,
        *,
        question: str,
        normalized_question: str,
        answer: dict[str, Any],
        model_identity: dict[str, Any],
        corpus_identity: str,
    ) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conversation_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        title = _title_from_question(question)
        with self._connect() as connection:
            self._ensure_schema(connection)
            connection.execute(
                """
                INSERT INTO conversations (
                    id,
                    title,
                    question,
                    normalized_question,
                    answer_json,
                    model_identity_json,
                    corpus_identity,
                    created_at_utc,
                    updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    title,
                    question,
                    normalized_question,
                    json.dumps(answer, sort_keys=True, ensure_ascii=False),
                    json.dumps(model_identity, sort_keys=True, ensure_ascii=False),
                    corpus_identity,
                    now,
                    now,
                ),
            )
            connection.commit()
        return self.get_conversation(conversation_id)

    def list_conversations(self) -> list[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)
            rows = connection.execute(
                """
                SELECT id, title, question, corpus_identity, created_at_utc, updated_at_utc
                FROM conversations
                WHERE deleted_at_utc IS NULL
                ORDER BY updated_at_utc DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)
            row = connection.execute(
                """
                SELECT *
                FROM conversations
                WHERE id = ? AND deleted_at_utc IS NULL
                """,
                (conversation_id,),
            ).fetchone()
        if row is None:
            raise KeyError(conversation_id)
        record = dict(row)
        record["answer"] = json.loads(record.pop("answer_json"))
        record["model_identity"] = json.loads(record.pop("model_identity_json"))
        return record

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                question TEXT NOT NULL,
                normalized_question TEXT NOT NULL,
                answer_json TEXT NOT NULL,
                model_identity_json TEXT NOT NULL,
                corpus_identity TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                deleted_at_utc TEXT
            )
            """
        )


def _title_from_question(question: str) -> str:
    compact = " ".join(question.split())
    if len(compact) <= 64:
        return compact
    return compact[:61].rstrip() + "..."
