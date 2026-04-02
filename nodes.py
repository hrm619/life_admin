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


AGENT_SYSTEM_PROMPT = """You are Hank's personal assistant. He has received his morning briefing
and is now asking follow-up questions. Return ONLY valid JSON — no markdown fences, no commentary."""

AGENT_PROMPT = """Today's date: {current_date}

### Morning Briefing
{briefing_json}

### Raw Messages (for detail lookups)
{raw_messages}

### Calendar Events (next 4 weeks)
{raw_events}

### Gmail Emails (since last briefing)
{raw_emails}

### Apple Notes (recently modified)
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


class FollowUpAgentNode(Node):
    def prep(self, shared):
        context = {
            "current_date": shared["current_date"],
            "briefing_json": json.dumps(shared["briefing"], indent=2),
            "raw_messages": json.dumps(shared["raw_messages"], indent=2, default=str),
            "raw_events": json.dumps(shared.get("raw_events", []), indent=2, default=str),
            "raw_emails": json.dumps(shared.get("raw_emails", []), indent=2, default=str),
            "raw_notes": json.dumps(shared.get("raw_notes", []), indent=2, default=str),
            "conversation_history": json.dumps(shared["conversation_history"]),
            "drafted_replies": json.dumps(shared["drafted_replies"]),
            "created_tasks": json.dumps(shared["created_tasks"]),
        }

        # Truncate raw data if too large
        max_chars = 60_000
        if len(context["raw_messages"]) > max_chars:
            context["raw_messages"] = context["raw_messages"][:max_chars] + "\n... (truncated)"
        if len(context["raw_events"]) > 20_000:
            context["raw_events"] = context["raw_events"][:20_000] + "\n... (truncated)"
        if len(context["raw_emails"]) > max_chars:
            context["raw_emails"] = context["raw_emails"][:max_chars] + "\n... (truncated)"
        if len(context["raw_notes"]) > max_chars:
            context["raw_notes"] = context["raw_notes"][:max_chars] + "\n... (truncated)"

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
        prompt = AGENT_PROMPT.format(
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

        if action == "answer":
            print(f"\n{exec_res['response']}")
            shared["conversation_history"].append(
                {"role": "user", "content": prep_res["user_input"]}
            )
            shared["conversation_history"].append(
                {"role": "assistant", "content": exec_res["response"]}
            )
            return "answer"

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
