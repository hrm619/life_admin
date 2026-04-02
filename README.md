# Life Admin

CLI morning briefing agent that reads your iMessages, summarizes them into a prioritized briefing, and lets you interact conversationally — ask follow-up questions, draft replies, and create tasks.

Built with [PocketFlow](https://github.com/The-Pocket/PocketFlow) and the Anthropic Claude API.

## Setup

**Prerequisites:**
- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager
- macOS (iMessage access requires local SQLite database)
- **Full Disk Access** enabled for your terminal app (System Settings → Privacy & Security → Full Disk Access)

**Install & configure:**

```bash
uv sync
```

Create a `.env` file with your Anthropic API key:

```
ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

```bash
uv run python main.py
```

The agent will:
1. Load the last-run timestamp (defaults to 24 hours ago on first run)
2. Fetch iMessages since the last briefing
3. Summarize them into a structured briefing with action-required and informational items
4. Display the briefing in your terminal
5. Enter an interactive follow-up session

In the follow-up session you can:
- Ask questions about your messages ("What did Sarah say about Saturday?")
- Draft replies ("Reply to Mom and say I'll be there at 6")
- Create tasks ("Remind me to call the dentist")
- Type `done` to end the session

The last-run timestamp is saved on exit so the next run only fetches new messages.

## Roadmap

See `life_admin_design.md` for the full design doc. Planned versions:

- **v0.1** — iMessage briefing (current)
- **v0.2** — Google Calendar
- **v0.3** — Gmail
- **v0.4** — Apple Notes
- **v1.0** — Unified CLI briefing with all sources
