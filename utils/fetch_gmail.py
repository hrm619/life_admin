import base64
from datetime import datetime, timezone
from email.utils import parseaddr

from googleapiclient.discovery import build

from utils.google_auth import get_google_creds

DEFAULT_MAX_EMAILS = 50


def _get_gmail_service():
    creds = get_google_creds()
    return build("gmail", "v1", credentials=creds)


def _get_header(headers: list[dict], name: str) -> str:
    """Extract a header value by name from Gmail message headers."""
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _decode_body(payload: dict) -> str:
    """Extract plain text body from Gmail message payload."""
    # Simple single-part message
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    # Multipart — look for text/plain part
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        # Nested multipart
        if part.get("parts"):
            result = _decode_body(part)
            if result:
                return result

    return ""


def fetch_gmail(since_timestamp: str, max_results: int = DEFAULT_MAX_EMAILS) -> list[dict]:
    """Fetch unread and starred emails since the given ISO timestamp.

    Returns a list of email dicts with: from, to, subject, date, snippet, body, labels, thread_id
    """
    service = _get_gmail_service()

    # Convert ISO timestamp to epoch seconds for Gmail query
    dt = datetime.fromisoformat(since_timestamp)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    epoch = int(dt.timestamp())

    query = f"after:{epoch} (is:unread OR is:starred)"

    # Fetch message IDs
    results = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )

    message_ids = [m["id"] for m in results.get("messages", [])]
    if not message_ids:
        return []

    # Fetch full message details
    emails = []
    for msg_id in message_ids:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_id, format="full")
            .execute()
        )

        headers = msg.get("payload", {}).get("headers", [])
        body = _decode_body(msg.get("payload", {}))

        # Truncate very long email bodies
        if len(body) > 5000:
            body = body[:5000] + "\n... (truncated)"

        _, from_addr = parseaddr(_get_header(headers, "From"))
        from_display = _get_header(headers, "From")

        emails.append(
            {
                "from": from_display,
                "to": _get_header(headers, "To"),
                "subject": _get_header(headers, "Subject"),
                "date": _get_header(headers, "Date"),
                "snippet": msg.get("snippet", ""),
                "body": body,
                "labels": msg.get("labelIds", []),
                "thread_id": msg.get("threadId", ""),
            }
        )

    return emails
