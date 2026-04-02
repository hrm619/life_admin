import gzip
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

NOTES_DB = (
    Path.home()
    / "Library"
    / "Group Containers"
    / "group.com.apple.notes"
    / "NoteStore.sqlite"
)

# Apple Notes epoch: 2001-01-01 00:00:00 UTC, stored as float seconds
CORE_DATA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def _iso_to_coredata_timestamp(iso_timestamp: str) -> float:
    dt = datetime.fromisoformat(iso_timestamp)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - CORE_DATA_EPOCH).total_seconds()


def _coredata_timestamp_to_iso(seconds: float) -> str:
    dt = CORE_DATA_EPOCH + timedelta(seconds=seconds)
    return dt.isoformat()


def _extract_text_from_zdata(zdata: bytes) -> str:
    """Decompress gzipped protobuf and extract plain text."""
    try:
        decompressed = gzip.decompress(zdata)
    except (gzip.BadGzipFile, OSError):
        return ""

    # Decode as UTF-8 and strip protobuf control characters
    text = decompressed.decode("utf-8", errors="replace")
    # Keep printable chars, tabs, and newlines; strip other control chars
    text = re.sub(r"[^\x20-\x7E\t\n\r\u00A0-\uFFFF]", "", text)
    # Collapse excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_apple_notes(since_timestamp: str) -> list[dict]:
    """Read Apple Notes modified since the given ISO timestamp."""
    if not NOTES_DB.exists():
        print(f"[Notes] Database not found at {NOTES_DB}")
        return []

    since_cd = _iso_to_coredata_timestamp(since_timestamp)

    query = """
        SELECT
            obj.ZTITLE1,
            nd.ZDATA,
            obj.ZMODIFICATIONDATE1,
            folder.ZTITLE2
        FROM ZICNOTEDATA nd
        JOIN ZICCLOUDSYNCINGOBJECT obj ON nd.ZNOTE = obj.Z_PK
        LEFT JOIN ZICCLOUDSYNCINGOBJECT folder
            ON obj.ZFOLDER = folder.Z_PK AND folder.Z_ENT = 14
        WHERE obj.Z_ENT = 11
          AND nd.ZDATA IS NOT NULL
          AND (obj.ZISPASSWORDPROTECTED IS NULL OR obj.ZISPASSWORDPROTECTED = 0)
          AND obj.ZMODIFICATIONDATE1 > ?
        ORDER BY obj.ZMODIFICATIONDATE1 DESC
    """

    conn = sqlite3.connect(f"file:{NOTES_DB}?mode=ro", uri=True)
    try:
        rows = conn.execute(query, (since_cd,)).fetchall()
    finally:
        conn.close()

    notes = []
    for title, zdata, mod_date, folder_name in rows:
        body = _extract_text_from_zdata(zdata) if zdata else ""
        if not body:
            continue

        # Truncate very long notes
        if len(body) > 5000:
            body = body[:5000] + "\n... (truncated)"

        notes.append(
            {
                "title": title or "(Untitled)",
                "body": body,
                "modified_date": _coredata_timestamp_to_iso(mod_date) if mod_date else "",
                "folder": folder_name or "",
            }
        )

    return notes
