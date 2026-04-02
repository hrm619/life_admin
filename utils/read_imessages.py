import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"

# iMessage epoch: 2001-01-01 00:00:00 UTC, stored as nanoseconds
IMESSAGE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def _iso_to_imessage_timestamp(iso_timestamp: str) -> int:
    dt = datetime.fromisoformat(iso_timestamp)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = dt - IMESSAGE_EPOCH
    return int(delta.total_seconds() * 1e9)


def _imessage_timestamp_to_iso(ns: int) -> str:
    dt = IMESSAGE_EPOCH + timedelta(seconds=ns / 1e9)
    return dt.isoformat()


def read_imessages(since_timestamp: str) -> list[dict]:
    if not CHAT_DB.exists():
        print(f"[iMessage] Database not found at {CHAT_DB}")
        return []

    since_ns = _iso_to_imessage_timestamp(since_timestamp)

    query = """
        SELECT
            m.rowid,
            m.text,
            m.date,
            m.is_from_me,
            h.id AS sender_id,
            c.chat_identifier,
            c.display_name
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.rowid
        JOIN chat_message_join cmj ON cmj.message_id = m.rowid
        JOIN chat c ON c.rowid = cmj.chat_id
        WHERE m.date > ?
          AND m.text IS NOT NULL
          AND m.text != ''
        ORDER BY m.date ASC
    """

    conn = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    try:
        rows = conn.execute(query, (since_ns,)).fetchall()
    finally:
        conn.close()

    messages = []
    for row in rows:
        _, text, date_ns, is_from_me, sender_id, chat_id, display_name = row
        messages.append(
            {
                "sender": "me" if is_from_me else (sender_id or "unknown"),
                "date": _imessage_timestamp_to_iso(date_ns),
                "body": text,
                "chat_id": chat_id or "unknown",
                "is_from_me": bool(is_from_me),
                "group_name": display_name if display_name else None,
            }
        )

    return messages
