import json
import re
from datetime import datetime, timedelta, timezone

from pocketflow import Node

from utils.call_llm import call_llm
from utils.fetch_calendar import fetch_calendar_events
from utils.fetch_gmail import fetch_gmail
from utils.format_briefing import format_briefing
from utils.read_notes import read_apple_notes
from utils.read_imessages import read_imessages
from utils.state import read_last_run, write_last_run
from utils.vector_store import create_index, search_index


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response, handling markdown fences."""
    # Try raw parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting from markdown code block
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    snippet = text[:200] + "..." if len(text) > 200 else text
    raise ValueError(f"Could not extract JSON from LLM response: {snippet}")


class LoadLastRunNode(Node):
    def prep(self, shared):
        return shared.get("config", {}).get("lookback_hours", 24)

    def exec(self, prep_res):
        lookback_hours = prep_res
        timestamp = read_last_run()
        if timestamp is None:
            default = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
            timestamp = default.isoformat()
            print(f"[LoadLastRun] First run — looking back {lookback_hours} hours")
        else:
            print(f"[LoadLastRun] Last run: {timestamp}")
        return timestamp

    def post(self, shared, _, exec_res):
        shared["last_run_timestamp"] = exec_res
        shared["current_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return "default"


class FetchIMessageNode(Node):
    def prep(self, shared):
        config = shared.get("config", {})
        if not config.get("sources", {}).get("imessage", True):
            return None
        return shared["last_run_timestamp"]

    def exec(self, prep_res):
        if prep_res is None:
            print("[FetchIMessage] Disabled — skipping")
            return []
        print("[FetchIMessage] Reading messages...")
        return read_imessages(prep_res)

    def exec_fallback(self, prep_res, exc):
        print(f"[FetchIMessage] Warning: failed to read messages — {exc}")
        return []

    def post(self, shared, _, exec_res):
        shared["raw_messages"] = exec_res
        if exec_res:
            print(f"[FetchIMessage] Fetched {len(exec_res)} messages")
        return "default"


class FetchCalendarNode(Node):
    def prep(self, shared):
        config = shared.get("config", {})
        if not config.get("sources", {}).get("calendar", True):
            return None
        weeks = config.get("calendar_lookahead_weeks", 4)
        return {"current_date": shared["current_date"], "weeks": weeks}

    def exec(self, prep_res):
        if prep_res is None:
            print("[FetchCalendar] Disabled — skipping")
            return []
        print("[FetchCalendar] Fetching events...")
        start = prep_res["current_date"]
        end_dt = datetime.fromisoformat(start) + timedelta(weeks=prep_res["weeks"])
        end = end_dt.strftime("%Y-%m-%d")
        return fetch_calendar_events(start, end)

    def exec_fallback(self, prep_res, exc):
        msg = str(exc)
        if "client_secret.json" in msg or isinstance(exc, FileNotFoundError):
            print(
                "[FetchCalendar] Google Calendar not configured. "
                "Place client_secret.json in ~/.life_admin/ to enable — skipping"
            )
        else:
            print(f"[FetchCalendar] Warning: failed to fetch calendar — {exc}")
        return []

    def post(self, shared, _, exec_res):
        shared["raw_events"] = exec_res
        if exec_res:
            print(f"[FetchCalendar] Fetched {len(exec_res)} events")
        return "default"


class FetchGmailNode(Node):
    def prep(self, shared):
        config = shared.get("config", {})
        if not config.get("sources", {}).get("gmail", True):
            return None
        max_emails = config.get("max_emails", 50)
        return {"timestamp": shared["last_run_timestamp"], "max_emails": max_emails}

    def exec(self, prep_res):
        if prep_res is None:
            print("[FetchGmail] Disabled — skipping")
            return []
        print("[FetchGmail] Fetching emails...")
        return fetch_gmail(prep_res["timestamp"], max_results=prep_res["max_emails"])

    def exec_fallback(self, prep_res, exc):
        msg = str(exc)
        if "client_secret.json" in msg or isinstance(exc, FileNotFoundError):
            print(
                "[FetchGmail] Google API not configured. "
                "Place client_secret.json in ~/.life_admin/ to enable — skipping"
            )
        else:
            print(f"[FetchGmail] Warning: failed to fetch emails — {exc}")
        return []

    def post(self, shared, _, exec_res):
        shared["raw_emails"] = exec_res
        if exec_res:
            print(f"[FetchGmail] Fetched {len(exec_res)} emails")
        return "default"


class FetchNotesNode(Node):
    def prep(self, shared):
        config = shared.get("config", {})
        if not config.get("sources", {}).get("notes", True):
            return None
        return shared["last_run_timestamp"]

    def exec(self, prep_res):
        if prep_res is None:
            print("[FetchNotes] Disabled — skipping")
            return []
        print("[FetchNotes] Reading notes...")
        return read_apple_notes(prep_res)

    def exec_fallback(self, prep_res, exc):
        if isinstance(exc, (FileNotFoundError, PermissionError)):
            print(
                "[FetchNotes] Apple Notes not accessible "
                "(check Full Disk Access) — skipping"
            )
        else:
            print(f"[FetchNotes] Warning: failed to read notes — {exc}")
        return []

    def post(self, shared, _, exec_res):
        shared["raw_notes"] = exec_res
        if exec_res:
            print(f"[FetchNotes] Fetched {len(exec_res)} notes")
        return "default"


BRIEFING_SYSTEM_PROMPT = """You are Hank's personal assistant preparing his morning briefing.
Return ONLY valid JSON — no markdown fences, no commentary."""

BRIEFING_PROMPT = """Today's date: {current_date}

## SOURCE DATA

### iMessages (since last briefing)
{formatted_messages}

### Google Calendar (next 4 weeks)
{formatted_events}

### Gmail Emails (since last briefing)
{formatted_emails}

### Apple Notes (recently modified)
{formatted_notes}

## INSTRUCTIONS

Analyze all source data and produce a structured morning briefing as JSON.

Categorize every noteworthy item into these groups:
- "action_required": Messages or emails where someone asked Hank a question, made a
  request, or where Hank needs to respond or make a decision. Also include calendar
  events starting within 60 minutes (urgency "high") or events requiring preparation
  like interviews, presentations, or events with a location Hank needs to travel to
  (urgency "medium"). Assign urgency: "high" if time-sensitive or from repeated
  follow-ups, "medium" for normal requests, "low" for casual check-ins.
- "informational": Updates, FYI messages, newsletters, or group chat activity that
  Hank should know about but doesn't need to act on.
- "schedule": ALL calendar events in chronological order. For each event include
  the title, a human-readable time string (e.g. "9:00 AM - 10:00 AM" or "All day"),
  whether it is all-day, and location if present. Group today's events first.
- "tasks": Open to-do items, checklists, or action language found in Apple Notes.
  Extract specific actionable items from notes — not the entire note contents.
  Each task should reference which note it came from.

Do NOT include:
- Automated messages, delivery notifications, or spam
- Messages Hank already replied to (where is_from_me=true is the most recent in a thread)
- Trivial exchanges (emoji-only reactions, "ok", "thanks")
- Marketing emails, bulk newsletters, or promotional content (unless starred)

Return ONLY valid JSON matching this structure:
{{
    "action_required": [
        {{"source": "imessage|calendar|gmail", "summary": "...", "detail": "...",
         "people": ["..."], "urgency": "high|medium|low"}}
    ],
    "informational": [
        {{"source": "imessage|gmail", "summary": "...", "detail": "..."}}
    ],
    "schedule": [
        {{"title": "...", "time": "...", "all_day": false, "location": "...", "date": "YYYY-MM-DD"}}
    ],
    "tasks": [
        {{"title": "...", "detail": "...", "source_note": "..."}}
    ]
}}

If there is nothing noteworthy, return:
{{"action_required": [], "informational": [], "schedule": [], "tasks": []}}"""


class SummarizeBriefingNode(Node):
    def prep(self, shared):
        messages = shared["raw_messages"]
        events = shared.get("raw_events", [])
        emails = shared.get("raw_emails", [])
        notes = shared.get("raw_notes", [])
        current_date = shared["current_date"]

        if not messages and not events and not emails and not notes:
            return None

        max_chars = 80_000

        formatted = json.dumps(messages, indent=2, default=str) if messages else "No new messages."
        if len(formatted) > max_chars:
            formatted = formatted[:max_chars] + "\n... (truncated)"

        formatted_events = json.dumps(events, indent=2, default=str) if events else "No calendar events."

        formatted_emails = json.dumps(emails, indent=2, default=str) if emails else "No new emails."
        if len(formatted_emails) > max_chars:
            formatted_emails = formatted_emails[:max_chars] + "\n... (truncated)"

        formatted_notes = json.dumps(notes, indent=2, default=str) if notes else "No recently modified notes."
        if len(formatted_notes) > max_chars:
            formatted_notes = formatted_notes[:max_chars] + "\n... (truncated)"

        return {
            "current_date": current_date,
            "formatted_messages": formatted,
            "formatted_events": formatted_events,
            "formatted_emails": formatted_emails,
            "formatted_notes": formatted_notes,
        }

    def exec(self, prep_res):
        if prep_res is None:
            print("[SummarizeBriefing] No data to summarize")
            return {"action_required": [], "informational": [], "schedule": [], "tasks": []}

        print("[SummarizeBriefing] Generating briefing...")
        prompt = BRIEFING_PROMPT.format(**prep_res)
        response = call_llm(prompt, system_prompt=BRIEFING_SYSTEM_PROMPT)
        return _extract_json(response)

    def post(self, shared, _, exec_res):
        shared["briefing"] = exec_res
        return "default"


DIM = "\033[2m"
ANSI_RESET = "\033[0m"


class DisplayBriefingNode(Node):
    def prep(self, shared):
        return shared["briefing"]

    def exec(self, prep_res):
        return format_briefing(prep_res)

    def post(self, shared, _, exec_res):
        print(exec_res)
        print(f"{DIM}  Ask a question, draft a reply, or type 'refresh' / 'done'{ANSI_RESET}")
        return "default"


def _chunk_messages(messages: list[dict]) -> list[dict]:
    """Group messages by chat_id, combine messages within 5-min windows."""
    if not messages:
        return []

    # Sort by chat_id then date
    sorted_msgs = sorted(messages, key=lambda m: (m.get("chat_id", ""), m.get("date", "")))

    chunks = []
    current_group = []
    current_chat = None
    current_time = None

    for msg in sorted_msgs:
        chat_id = msg.get("chat_id", "unknown")
        msg_time = msg.get("date", "")

        # Start new group if chat changes or >5 min gap
        start_new = False
        if chat_id != current_chat:
            start_new = True
        elif current_time and msg_time:
            try:
                t1 = datetime.fromisoformat(current_time)
                t2 = datetime.fromisoformat(msg_time)
                if abs((t2 - t1).total_seconds()) > 300:
                    start_new = True
            except (ValueError, TypeError):
                pass

        if start_new and current_group:
            lines = [f"{m.get('sender', '?')}: {m.get('body', '')}" for m in current_group]
            participants = list({m.get("sender", "?") for m in current_group if not m.get("is_from_me")})
            group_name = current_group[0].get("group_name", "")
            label = group_name if group_name else current_chat
            chunks.append(
                {
                    "id": f"imsg-{len(chunks)}",
                    "text": f"[iMessage: {label}]\n" + "\n".join(lines),
                    "metadata": {
                        "source": "imessage",
                        "chat_id": current_chat or "",
                        "participants": ", ".join(participants),
                        "date": current_group[0].get("date", ""),
                    },
                }
            )
            current_group = []

        current_group.append(msg)
        current_chat = chat_id
        current_time = msg_time

    # Flush last group
    if current_group:
        lines = [f"{m.get('sender', '?')}: {m.get('body', '')}" for m in current_group]
        participants = list({m.get("sender", "?") for m in current_group if not m.get("is_from_me")})
        group_name = current_group[0].get("group_name", "")
        label = group_name if group_name else current_chat
        chunks.append(
            {
                "id": f"imsg-{len(chunks)}",
                "text": f"[iMessage: {label}]\n" + "\n".join(lines),
                "metadata": {
                    "source": "imessage",
                    "chat_id": current_chat or "",
                    "participants": ", ".join(participants),
                    "date": current_group[0].get("date", ""),
                },
            }
        )

    return chunks


def _split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text into chunks with overlap."""
    if len(text) <= chunk_size:
        return [text]
    parts = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        parts.append(text[start:end])
        start = end - overlap
    return parts


def _chunk_emails(emails: list[dict]) -> list[dict]:
    """Each email is one chunk; split long bodies with overlap."""
    chunks = []
    for i, email in enumerate(emails):
        header = f"From: {email.get('from', '?')}\nSubject: {email.get('subject', '')}\nDate: {email.get('date', '')}"
        body = email.get("body", "") or email.get("snippet", "")

        if len(body) <= 2000:
            chunks.append(
                {
                    "id": f"email-{i}",
                    "text": header + "\n\n" + body,
                    "metadata": {
                        "source": "gmail",
                        "from": email.get("from", ""),
                        "subject": email.get("subject", ""),
                        "date": email.get("date", ""),
                    },
                }
            )
        else:
            parts = _split_text(body, 1500, 200)
            for j, part in enumerate(parts):
                chunks.append(
                    {
                        "id": f"email-{i}-{j}",
                        "text": header + "\n\n" + part,
                        "metadata": {
                            "source": "gmail",
                            "from": email.get("from", ""),
                            "subject": email.get("subject", ""),
                            "date": email.get("date", ""),
                        },
                    }
                )
    return chunks


def _chunk_events(events: list[dict]) -> list[dict]:
    """Each event is one chunk."""
    chunks = []
    for i, event in enumerate(events):
        text = f"{event.get('title', '(No title)')} — {event.get('start', '')} to {event.get('end', '')}"
        if event.get("location"):
            text += f"\nLocation: {event['location']}"
        if event.get("description"):
            text += f"\n{event['description']}"
        chunks.append(
            {
                "id": f"cal-{i}",
                "text": text,
                "metadata": {
                    "source": "calendar",
                    "title": event.get("title", ""),
                    "date": event.get("start", ""),
                },
            }
        )
    return chunks


def _chunk_notes(notes: list[dict]) -> list[dict]:
    """Split notes into ~1000-char chunks with overlap."""
    chunks = []
    for i, note in enumerate(notes):
        header = f"Note: {note.get('title', '(Untitled)')} (folder: {note.get('folder', '')}, modified: {note.get('modified_date', '')})"
        body = note.get("body", "")
        parts = _split_text(body, 1000, 200)
        for j, part in enumerate(parts):
            chunks.append(
                {
                    "id": f"note-{i}-{j}",
                    "text": header + "\n\n" + part,
                    "metadata": {
                        "source": "notes",
                        "title": note.get("title", ""),
                        "folder": note.get("folder", ""),
                        "modified_date": note.get("modified_date", ""),
                    },
                }
            )
    return chunks


class IndexSourceDataNode(Node):
    def prep(self, shared):
        chunks = []
        chunks.extend(_chunk_messages(shared.get("raw_messages", [])))
        chunks.extend(_chunk_emails(shared.get("raw_emails", [])))
        chunks.extend(_chunk_events(shared.get("raw_events", [])))
        chunks.extend(_chunk_notes(shared.get("raw_notes", [])))

        if not chunks:
            return None

        return chunks

    def exec(self, prep_res):
        if prep_res is None:
            print("[Index] No data to index")
            return None
        print(f"[Index] Indexing {len(prep_res)} chunks...")
        return create_index(prep_res)

    def exec_fallback(self, prep_res, exc):
        print(f"[Index] Warning: failed to create index — {exc}")
        print("[Index] Follow-up agent will use truncated raw data as fallback")
        return None

    def post(self, shared, prep_res, exec_res):
        shared["vector_index"] = exec_res
        if exec_res is not None and prep_res is not None:
            sources = {c["metadata"]["source"] for c in prep_res}
            print(f"[Index] Indexed {len(prep_res)} chunks across {len(sources)} sources")
        return "default"


AGENT_SYSTEM_PROMPT = """You are Hank's personal assistant. He has received his morning briefing
and is now asking follow-up questions. Return ONLY valid JSON — no markdown fences, no commentary."""

AGENT_PROMPT = """Today's date: {current_date}

### Morning Briefing
{briefing_json}

### Retrieved Context
{retrieved_context}

### Conversation So Far
{conversation_history}

### Drafts Created This Session
{drafted_replies}

### Tasks Created This Session
{created_tasks}

## HANK'S INPUT
{user_input}

## ACTION SPACE

Decide the single best action. If Hank asks for specific details that aren't
in the briefing summary (exact wording of a message, full email body, specific
note contents), use search_context FIRST to retrieve the relevant data, then
answer on the next turn.

[1] answer
  Description: Answer Hank's question using the briefing and/or retrieved context
  Parameters:
    - response (str): Your answer

[2] search_context
  Description: Search the indexed source data for specific details before answering.
               Use this when the briefing summary doesn't have enough detail.
               After searching, you'll get another turn to answer with the results.
  Parameters:
    - query (str): Natural language search query (e.g. "Sarah's messages about dinner")
    - source_filter (str|null): Optional — limit search to one source: "imessage", "gmail", "calendar", or "notes". Use null to search all.
    - response (str): Brief message to show Hank while searching (e.g. "Let me look that up...")

[3] draft_reply
  Description: Draft a text message (iMessage) reply for Hank to send
  Parameters:
    - to (str): Recipient name
    - content (str): The draft message text
    - context (str): What this is replying to (one line)
    - response (str): Brief confirmation to show Hank

[4] draft_email
  Description: Draft an email reply for Hank to send
  Parameters:
    - to (str): Recipient email or name
    - subject (str): Email subject line
    - content (str): The draft email body
    - context (str): What this is replying to (one line)
    - response (str): Brief confirmation to show Hank

[5] create_task
  Description: Create a new task/to-do item based on what Hank said
  Parameters:
    - description (str): The task description
    - source (str): What triggered this
    - response (str): Brief confirmation to show Hank

[6] refresh
  Description: Re-pull all sources and regenerate the briefing
  Parameters:
    - response (str): Brief message confirming refresh

[7] done
  Description: Hank is finished with the briefing session
  Parameters:
    - response (str): Goodbye message with session summary

## RESPONSE FORMAT

Return ONLY valid JSON:
{{"action": "answer|search_context|draft_reply|draft_email|create_task|refresh|done", ...parameters from above}}"""

# Fallback prompt when vector index is not available — includes truncated raw data
AGENT_PROMPT_FALLBACK = """Today's date: {current_date}

### Morning Briefing
{briefing_json}

### Raw Messages (for detail lookups)
{raw_messages}

### Calendar Events
{raw_events}

### Gmail Emails
{raw_emails}

### Apple Notes
{raw_notes}

### Conversation So Far
{conversation_history}

### Drafts Created This Session
{drafted_replies}

### Tasks Created This Session
{created_tasks}

## HANK'S INPUT
{user_input}

## ACTION SPACE

Decide the single best action:

[1] answer
  Description: Answer Hank's question using the available context
  Parameters:
    - response (str): Your answer

[2] draft_reply
  Description: Draft a text message (iMessage) reply for Hank to send
  Parameters:
    - to (str): Recipient name
    - content (str): The draft message text
    - context (str): What this is replying to (one line)
    - response (str): Brief confirmation to show Hank

[3] draft_email
  Description: Draft an email reply for Hank to send
  Parameters:
    - to (str): Recipient email or name
    - subject (str): Email subject line
    - content (str): The draft email body
    - context (str): What this is replying to (one line)
    - response (str): Brief confirmation to show Hank

[4] create_task
  Description: Create a new task/to-do item based on what Hank said
  Parameters:
    - description (str): The task description
    - source (str): What triggered this
    - response (str): Brief confirmation to show Hank

[5] refresh
  Description: Re-pull all sources and regenerate the briefing
  Parameters:
    - response (str): Brief message confirming refresh

[6] done
  Description: Hank is finished with the briefing session
  Parameters:
    - response (str): Goodbye message with session summary

## RESPONSE FORMAT

Return ONLY valid JSON:
{{"action": "answer|draft_reply|draft_email|create_task|refresh|done", ...parameters from above}}"""

MAX_CONVERSATION_WINDOW = 20  # 10 exchanges (user + assistant pairs)


class FollowUpAgentNode(Node):
    def prep(self, shared):
        has_index = shared.get("vector_index") is not None

        # Window conversation history to last 10 exchanges
        full_history = shared["conversation_history"]
        if len(full_history) > MAX_CONVERSATION_WINDOW:
            omitted = (len(full_history) - MAX_CONVERSATION_WINDOW) // 2
            windowed = [{"role": "system", "content": f"(Earlier conversation omitted — {omitted} previous exchanges)"}]
            windowed.extend(full_history[-MAX_CONVERSATION_WINDOW:])
            history_json = json.dumps(windowed)
        else:
            history_json = json.dumps(full_history)

        context = {
            "current_date": shared["current_date"],
            "briefing_json": json.dumps(shared["briefing"], indent=2),
            "conversation_history": history_json,
            "drafted_replies": json.dumps(shared["drafted_replies"]),
            "created_tasks": json.dumps(shared["created_tasks"]),
            "_has_index": has_index,
        }

        if has_index:
            # RAG mode — include retrieved context only
            retrieved = shared.get("retrieved_context", [])
            context["retrieved_context"] = "\n\n".join(retrieved) if retrieved else "(No context retrieved yet — use search_context to look up details)"
        else:
            # Fallback mode — include truncated raw data
            max_chars = 60_000
            raw_messages = json.dumps(shared["raw_messages"], indent=2, default=str)
            raw_events = json.dumps(shared.get("raw_events", []), indent=2, default=str)
            raw_emails = json.dumps(shared.get("raw_emails", []), indent=2, default=str)
            raw_notes = json.dumps(shared.get("raw_notes", []), indent=2, default=str)
            if len(raw_messages) > max_chars:
                raw_messages = raw_messages[:max_chars] + "\n... (truncated)"
            if len(raw_events) > 20_000:
                raw_events = raw_events[:20_000] + "\n... (truncated)"
            if len(raw_emails) > max_chars:
                raw_emails = raw_emails[:max_chars] + "\n... (truncated)"
            if len(raw_notes) > max_chars:
                raw_notes = raw_notes[:max_chars] + "\n... (truncated)"
            context["raw_messages"] = raw_messages
            context["raw_events"] = raw_events
            context["raw_emails"] = raw_emails
            context["raw_notes"] = raw_notes

        # If we just did a search_context, skip prompting — auto-answer with retrieved context
        pending_query = shared.pop("_pending_search_query", None)
        if pending_query:
            context["_shortcut"] = None
            context["user_input"] = pending_query
            return context

        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            user_input = "done"

        if user_input.lower() in ("done", "exit", "quit"):
            context["_shortcut"] = "done"
            context["user_input"] = user_input
        elif user_input.lower() == "refresh":
            context["_shortcut"] = "refresh"
            context["user_input"] = user_input
        else:
            context["_shortcut"] = None
            context["user_input"] = user_input

        return context

    def exec(self, prep_res):
        if prep_res["_shortcut"] == "done":
            return {
                "action": "done",
                "response": "Session ended. Have a productive day!",
            }
        if prep_res["_shortcut"] == "refresh":
            return {
                "action": "refresh",
                "response": "Refreshing briefing — re-pulling all sources...",
            }

        print("[FollowUp] Thinking...")

        if prep_res["_has_index"]:
            prompt = AGENT_PROMPT.format(
                current_date=prep_res["current_date"],
                briefing_json=prep_res["briefing_json"],
                retrieved_context=prep_res["retrieved_context"],
                conversation_history=prep_res["conversation_history"],
                drafted_replies=prep_res["drafted_replies"],
                created_tasks=prep_res["created_tasks"],
                user_input=prep_res["user_input"],
            )
        else:
            prompt = AGENT_PROMPT_FALLBACK.format(
                current_date=prep_res["current_date"],
                briefing_json=prep_res["briefing_json"],
                raw_messages=prep_res["raw_messages"],
                raw_events=prep_res["raw_events"],
                raw_emails=prep_res["raw_emails"],
                raw_notes=prep_res["raw_notes"],
                conversation_history=prep_res["conversation_history"],
                drafted_replies=prep_res["drafted_replies"],
                created_tasks=prep_res["created_tasks"],
                user_input=prep_res["user_input"],
            )

        response = call_llm(prompt, system_prompt=AGENT_SYSTEM_PROMPT)
        return _extract_json(response)

    def post(self, shared, prep_res, exec_res):
        action = exec_res.get("action", "done")

        # Clear retrieved context on non-search actions
        if action != "search_context":
            shared["retrieved_context"] = []

        if action == "answer":
            print(f"\n{exec_res['response']}")
            shared["conversation_history"].append(
                {"role": "user", "content": prep_res["user_input"]}
            )
            shared["conversation_history"].append(
                {"role": "assistant", "content": exec_res["response"]}
            )
            return "answer"

        elif action == "search_context":
            query = exec_res.get("query", "")
            source_filter = exec_res.get("source_filter")
            print(f"\n{exec_res.get('response', 'Searching...')}")

            index = shared.get("vector_index")
            if index is not None:
                where = {"source": source_filter} if source_filter else None
                results = search_index(index, query, n_results=8, where=where)
                formatted = []
                for r in results:
                    src = r["metadata"].get("source", "unknown")
                    formatted.append(f"[{src}] {r['text']}")
                shared["retrieved_context"] = formatted
                print(f"{DIM}  Found {len(results)} relevant chunks{ANSI_RESET}")
            else:
                shared["retrieved_context"] = ["(Index not available — could not search)"]

            # Store the original user question so next loop auto-answers with context
            shared["_pending_search_query"] = prep_res["user_input"]

            shared["conversation_history"].append(
                {"role": "user", "content": prep_res["user_input"]}
            )
            shared["conversation_history"].append(
                {"role": "assistant", "content": f"(Searched for: {query} — found {len(shared['retrieved_context'])} results)"}
            )
            return "search_context"

        elif action == "draft_reply":
            print(f"\n📝 Draft to {exec_res.get('to', '?')}:")
            print(f"   {exec_res.get('content', '')}")
            print(f"   ({exec_res.get('context', '')})")
            shared["drafted_replies"].append(
                {
                    "type": "imessage",
                    "to": exec_res.get("to", ""),
                    "content": exec_res.get("content", ""),
                    "context": exec_res.get("context", ""),
                }
            )
            shared["conversation_history"].append(
                {"role": "user", "content": prep_res["user_input"]}
            )
            shared["conversation_history"].append(
                {"role": "assistant", "content": exec_res.get("response", "")}
            )
            return "draft_reply"

        elif action == "draft_email":
            print(f"\n📧 Draft email to {exec_res.get('to', '?')}:")
            print(f"   Subject: {exec_res.get('subject', '')}")
            print(f"   {exec_res.get('content', '')}")
            print(f"   ({exec_res.get('context', '')})")
            shared["drafted_replies"].append(
                {
                    "type": "email",
                    "to": exec_res.get("to", ""),
                    "subject": exec_res.get("subject", ""),
                    "content": exec_res.get("content", ""),
                    "context": exec_res.get("context", ""),
                }
            )
            shared["conversation_history"].append(
                {"role": "user", "content": prep_res["user_input"]}
            )
            shared["conversation_history"].append(
                {"role": "assistant", "content": exec_res.get("response", "")}
            )
            return "draft_email"

        elif action == "create_task":
            print(f"\n✅ Task created: {exec_res.get('description', '')}")
            shared["created_tasks"].append(
                {
                    "description": exec_res.get("description", ""),
                    "source": exec_res.get("source", ""),
                }
            )
            shared["conversation_history"].append(
                {"role": "user", "content": prep_res["user_input"]}
            )
            shared["conversation_history"].append(
                {"role": "assistant", "content": exec_res.get("response", "")}
            )
            return "create_task"

        elif action == "refresh":
            print(f"\n{exec_res.get('response', 'Refreshing...')}")
            return "refresh"

        else:  # done
            # Write last run timestamp on clean exit
            write_last_run(datetime.now(timezone.utc).isoformat())
            drafts = len(shared["drafted_replies"])
            tasks = len(shared["created_tasks"])
            print(f"\n{exec_res.get('response', 'Goodbye!')}")
            if drafts or tasks:
                print(f"Session summary: {drafts} draft(s), {tasks} task(s)")
            return "done"
