"""SQLite persistence for local conversation records."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


class ConversationStore:
    def __init__(
        self,
        path: str | Path,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self.fault_injector = fault_injector

    def save_answer(
        self,
        *,
        question: str,
        normalized_question: str,
        answer: dict[str, Any],
        model_identity: dict[str, Any],
        corpus_identity: str,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            self._ensure_schema(connection)
            if conversation_id is None:
                conversation_id = uuid.uuid4().hex
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
                        _title_from_question(question),
                        question,
                        normalized_question,
                        json.dumps(answer, sort_keys=True, ensure_ascii=False),
                        json.dumps(model_identity, sort_keys=True, ensure_ascii=False),
                        corpus_identity,
                        now,
                        now,
                    ),
                )
                self._inject_write_fault("after_conversation_header")
            else:
                self._require_conversation(connection, conversation_id)

            turn_index = self._next_turn_index(connection, conversation_id)
            connection.execute(
                """
                INSERT INTO conversation_turns (
                    id,
                    conversation_id,
                    turn_index,
                    question,
                    normalized_question,
                    answer_json,
                    model_identity_json,
                    corpus_identity,
                    answered_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    conversation_id,
                    turn_index,
                    question,
                    normalized_question,
                    json.dumps(answer, sort_keys=True, ensure_ascii=False),
                    json.dumps(model_identity, sort_keys=True, ensure_ascii=False),
                    corpus_identity,
                    now,
                ),
            )
            self._inject_write_fault("after_turn_insert")
            connection.execute(
                """
                UPDATE conversations
                SET updated_at_utc = ?
                WHERE id = ?
                """,
                (now, conversation_id),
            )
            connection.commit()
        return self.get_conversation(conversation_id)

    def list_conversations(self) -> list[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)
            rows = connection.execute(
                """
                SELECT
                    conversations.id,
                    conversations.title,
                    conversations.created_at_utc,
                    conversations.updated_at_utc,
                    COALESCE(latest_turn.corpus_identity, conversations.corpus_identity)
                        AS corpus_identity,
                    COALESCE(turn_counts.turn_count, 0) AS turn_count
                FROM conversations
                LEFT JOIN (
                    SELECT conversation_id, COUNT(*) AS turn_count
                    FROM conversation_turns
                    GROUP BY conversation_id
                ) AS turn_counts
                    ON turn_counts.conversation_id = conversations.id
                LEFT JOIN conversation_turns AS latest_turn
                    ON latest_turn.conversation_id = conversations.id
                    AND latest_turn.turn_index = (
                        SELECT MAX(turn_index)
                        FROM conversation_turns
                        WHERE conversation_id = conversations.id
                    )
                WHERE conversations.deleted_at_utc IS NULL
                    AND COALESCE(turn_counts.turn_count, 0) > 0
                ORDER BY conversations.updated_at_utc DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)
            row = connection.execute(
                """
                SELECT id, title, created_at_utc, updated_at_utc, deleted_at_utc
                FROM conversations
                WHERE id = ? AND deleted_at_utc IS NULL
                """,
                (conversation_id,),
            ).fetchone()
            turn_rows = connection.execute(
                """
                SELECT *
                FROM conversation_turns
                WHERE conversation_id = ?
                ORDER BY turn_index
                """,
                (conversation_id,),
            ).fetchall()
        if row is None:
            raise KeyError(conversation_id)
        record = dict(row)
        turns = [_turn_from_row(turn_row) for turn_row in turn_rows]
        if not turns:
            raise KeyError(conversation_id)
        latest_turn = turns[-1]
        record["turns"] = turns
        record["question"] = latest_turn["question"]
        record["normalized_question"] = latest_turn["normalized_question"]
        record["answer"] = latest_turn["answer"]
        record["model_identity"] = latest_turn["model_identity"]
        record["corpus_identity"] = latest_turn["corpus_identity"]
        record["answered_at_utc"] = latest_turn["answered_at_utc"]
        record["answered_at_display"] = latest_turn["answered_at_display"]
        return record

    def export_conversation(self, conversation_id: str) -> dict[str, Any]:
        record = self.get_conversation(conversation_id)
        return _export_record(record)

    def export_conversations(self) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)
            rows = connection.execute(
                """
                SELECT id
                FROM conversations
                WHERE deleted_at_utc IS NULL
                ORDER BY updated_at_utc DESC
                """
            ).fetchall()
        return {
            "export_schema": "danish-rag.conversation-records.v1",
            "exported_at_utc": datetime.now(UTC).isoformat(),
            "conversations": [
                self.export_conversation(str(row["id"]))["conversation"]
                for row in rows
            ],
        }

    def delete_conversation(self, conversation_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)
            cursor = connection.execute(
                """
                UPDATE conversations
                SET deleted_at_utc = ?, updated_at_utc = ?
                WHERE id = ? AND deleted_at_utc IS NULL
                """,
                (now, now, conversation_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(conversation_id)
            connection.commit()

    def delete_all_conversations(self) -> int:
        now = datetime.now(UTC).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)
            cursor = connection.execute(
                """
                UPDATE conversations
                SET deleted_at_utc = ?, updated_at_utc = ?
                WHERE deleted_at_utc IS NULL
                """,
                (now, now),
            )
            connection.commit()
            return int(cursor.rowcount)

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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_turns (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                question TEXT NOT NULL,
                normalized_question TEXT NOT NULL,
                answer_json TEXT NOT NULL,
                model_identity_json TEXT NOT NULL,
                corpus_identity TEXT NOT NULL,
                answered_at_utc TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id),
                UNIQUE (conversation_id, turn_index)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_conversation_turns_conversation
            ON conversation_turns(conversation_id, turn_index)
            """
        )
        self._migrate_legacy_conversations(connection)

    def _migrate_legacy_conversations(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            INSERT INTO conversation_turns (
                id,
                conversation_id,
                turn_index,
                question,
                normalized_question,
                answer_json,
                model_identity_json,
                corpus_identity,
                answered_at_utc
            )
            SELECT
                conversations.id || ':turn:1',
                conversations.id,
                1,
                conversations.question,
                conversations.normalized_question,
                conversations.answer_json,
                conversations.model_identity_json,
                conversations.corpus_identity,
                conversations.updated_at_utc
            FROM conversations
            WHERE conversations.question != ''
                AND NOT EXISTS (
                    SELECT 1
                    FROM conversation_turns
                    WHERE conversation_turns.conversation_id = conversations.id
                )
            """
        )

    def _require_conversation(
        self,
        connection: sqlite3.Connection,
        conversation_id: str,
    ) -> None:
        row = connection.execute(
            """
            SELECT id
            FROM conversations
            WHERE id = ? AND deleted_at_utc IS NULL
            """,
            (conversation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(conversation_id)

    def _next_turn_index(self, connection: sqlite3.Connection, conversation_id: str) -> int:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(turn_index), 0) + 1 AS next_turn_index
            FROM conversation_turns
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()
        return int(row["next_turn_index"])

    def _inject_write_fault(self, phase: str) -> None:
        if self.fault_injector is not None:
            self.fault_injector(phase)


def _title_from_question(question: str) -> str:
    compact = " ".join(question.split())
    if len(compact) <= 64:
        return compact
    return compact[:61].rstrip() + "..."


def _turn_from_row(row: sqlite3.Row) -> dict[str, Any]:
    turn = dict(row)
    turn["answer"] = json.loads(turn.pop("answer_json"))
    turn["model_identity"] = json.loads(turn.pop("model_identity_json"))
    turn["answered_at_display"] = _timestamp_display(turn["answered_at_utc"])
    return turn


def _timestamp_display(timestamp: str) -> str:
    if len(timestamp) >= 16:
        return timestamp[:16].replace("T", " ")
    return timestamp


def _export_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "export_schema": "danish-rag.conversation-record.v1",
        "exported_at_utc": datetime.now(UTC).isoformat(),
        "conversation": {
            "id": record["id"],
            "title": record["title"],
            "created_at_utc": record["created_at_utc"],
            "updated_at_utc": record["updated_at_utc"],
            "turns": [_export_turn(turn) for turn in record["turns"]],
        },
    }


def _export_turn(turn: dict[str, Any]) -> dict[str, Any]:
    trust = dict(turn["answer"].get("trust") or {})
    return {
        "turn_index": turn["turn_index"],
        "question": turn["question"],
        "normalized_question": turn["normalized_question"],
        "answered_at_utc": turn["answered_at_utc"],
        "model_identity": turn["model_identity"],
        "corpus_version": turn["corpus_identity"],
        "corpus_identity": turn["corpus_identity"],
        "answer": turn["answer"],
        "citations": turn["answer"].get("citations", []),
        "trust_indicators": {
            "evidence_confidence": trust.get("evidence_confidence"),
            "evidence_confidence_reason": trust.get("evidence_confidence_reason"),
            "fresh_tomato_score": trust.get("fresh_tomato_score"),
            "fresh_tomato_reason": trust.get("fresh_tomato_reason"),
        },
    }
