from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build

from utils.google_auth import get_google_creds


def _get_calendar_service():
    creds = get_google_creds()
    return build("calendar", "v3", credentials=creds)


def fetch_calendar_events(start_date: str, end_date: str) -> list[dict]:
    """Fetch calendar events between start_date and end_date (ISO format strings)."""
    service = _get_calendar_service()

    # Convert date strings to RFC 3339 UTC
    start_dt = datetime.fromisoformat(start_date)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end_date)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    results = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=start_dt.isoformat(),
            timeMax=end_dt.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    events = []
    for item in results.get("items", []):
        start_raw = item.get("start", {})
        end_raw = item.get("end", {})
        all_day = "date" in start_raw and "dateTime" not in start_raw

        events.append(
            {
                "title": item.get("summary", "(No title)"),
                "start": start_raw.get("date") if all_day else start_raw.get("dateTime", ""),
                "end": end_raw.get("date") if all_day else end_raw.get("dateTime", ""),
                "location": item.get("location", ""),
                "description": item.get("description", ""),
                "all_day": all_day,
            }
        )

    return events
