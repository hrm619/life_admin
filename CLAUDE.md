# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Life Admin is a CLI morning briefing agent that aggregates communications from iMessage, Google Calendar, Gmail, and Apple Notes, summarizes them via LLM, and provides an interactive follow-up session. Built with PocketFlow (workflow/agent framework) and the Anthropic Claude API.

Current scope is **v1.0 (unified CLI briefing)**. See `life_admin_design.md` for the full design doc.

## Commands

```bash
# Install dependencies (uses uv)
uv sync

# Run the agent
uv run python main.py

# Show help / version
uv run python main.py --help
uv run python main.py --version

# Use a custom config file
uv run python main.py --config /path/to/config.json
```

## Architecture

Two-phase system: **Briefing Workflow** (deterministic pipeline) then **Follow-up Agent** (interactive loop).

**Flow:** `LoadLastRunNode` → `FetchIMessageNode` → `FetchCalendarNode` → `FetchGmailNode` → `FetchNotesNode` → `SummarizeBriefingNode` → `DisplayBriefingNode` → `FollowUpAgentNode` (loops on answer/draft_reply/draft_email/create_task, "refresh" loops back to LoadLastRunNode, exits on "done")

- `main.py` — entry point with argparse, loads `.env` and config, prints welcome banner, runs the flow
- `nodes.py` — all PocketFlow node definitions plus `_extract_json()` helper for parsing LLM responses
- `flow.py` — creates and connects the flow graph with agent loop edges
- `utils/config.py` — loads/creates `~/.life_admin/config.json` with per-source settings
- `utils/call_llm.py` — single Anthropic Claude wrapper (`claude-sonnet-4-6`), all LLM calls go through here, logs token usage to stderr
- `utils/read_imessages.py` — reads from `~/Library/Messages/chat.db` (SQLite, read-only mode); iMessage dates are nanoseconds since 2001-01-01
- `utils/state.py` — read/write last-run timestamp to `~/.life_admin/last_run.json`
- `utils/google_auth.py` — shared Google OAuth2 credential management (used by Calendar and Gmail); token stored at `~/.life_admin/google_token.json`
- `utils/fetch_calendar.py` — Google Calendar API client; fetches events for a configurable lookahead window
- `utils/fetch_gmail.py` — Gmail API client; fetches unread/starred emails since last run (configurable max)
- `utils/read_notes.py` — Apple Notes SQLite reader; reads from `NoteStore.sqlite`, decompresses gzipped protobuf bodies, skips encrypted notes
- `utils/format_briefing.py` — ANSI-colored terminal output for the briefing

Data flows through PocketFlow's **shared store** dict — nodes read/write keys like `raw_messages`, `raw_events`, `raw_emails`, `raw_notes`, `briefing`, `conversation_history`, `drafted_replies`, `created_tasks`.

## Configuration

Config file at `~/.life_admin/config.json` (auto-created with defaults on first run):

```json
{
  "lookback_hours": 24,
  "calendar_lookahead_weeks": 4,
  "max_emails": 50,
  "sources": {
    "imessage": true,
    "calendar": true,
    "gmail": true,
    "notes": true
  }
}
```

- `lookback_hours` — how far back to look on first run (when no last_run timestamp exists)
- `calendar_lookahead_weeks` — how many weeks ahead to fetch calendar events
- `max_emails` — maximum number of emails to fetch per run
- `sources` — enable/disable individual data sources; disabled sources are skipped entirely

## Key Technical Details

- Requires **Full Disk Access** for Terminal/Python to read `~/Library/Messages/chat.db` and `~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite`
- API key loaded from `.env` via `python-dotenv` (`ANTHROPIC_API_KEY`)
- Google Calendar and Gmail share OAuth2 credentials: place `client_secret.json` (from Google Cloud Console) in `~/.life_admin/`; first run opens browser for consent (both calendar.readonly and gmail.readonly scopes), token saved to `~/.life_admin/google_token.json`
- If Google API is not configured, `FetchCalendarNode` and `FetchGmailNode` gracefully skip with a warning
- If Apple Notes DB is not accessible, `FetchNotesNode` gracefully skips; encrypted/password-protected notes are always skipped
- Each fetch node checks `shared["config"]["sources"]` and skips if disabled
- LLM responses are parsed via `_extract_json()` in `nodes.py` which handles both raw JSON and markdown-fenced JSON blocks; raises `ValueError` on failure to trigger PocketFlow's retry mechanism
- The FollowUpAgentNode loops via PocketFlow action strings (edge connections back to self), not a while loop
- `write_last_run` is called in FollowUpAgentNode's "done" action (not DisplayBriefingNode) to avoid marking messages as seen if the session crashes
- Raw messages are truncated at ~60-80k chars before being sent to the LLM to stay within context limits
- Read-only access to all data sources — never modify or send messages autonomously

## PocketFlow Patterns

- Nodes follow the `prep(shared)` → `exec(prep_res)` → `post(shared, prep_res, exec_res)` lifecycle
- `post()` returns an action string that determines the next node via edge connections (e.g. `agent - "answer" >> agent`)
- Retries are configured per-node via `max_retries` — `exec_fallback()` handles exhausted retries gracefully
- The shared store is a plain dict passed through the entire flow; `prep` reads from it, `post` writes to it
