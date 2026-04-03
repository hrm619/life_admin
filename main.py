import argparse
from datetime import datetime, timezone

from dotenv import load_dotenv

from flow import create_flow
from utils.config import load_config

VERSION = "2.0.0"

BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def main():
    parser = argparse.ArgumentParser(description="Life Admin — morning briefing agent")
    parser.add_argument("--version", action="version", version=f"life-admin {VERSION}")
    parser.add_argument("--config", type=str, help="path to config file", default=None)
    args = parser.parse_args()

    load_dotenv()
    config = load_config(args.config)

    today = datetime.now(timezone.utc).strftime("%A, %B %-d, %Y")
    print(f"\n{BOLD}Life Admin v{VERSION}{RESET} {DIM}— {today}{RESET}\n")

    shared = {
        "config": config,
        "last_run_timestamp": None,
        "current_date": None,
        "raw_messages": [],
        "raw_events": [],
        "raw_emails": [],
        "raw_notes": [],
        "briefing": {},
        "conversation_history": [],
        "drafted_replies": [],
        "created_tasks": [],
        "vector_index": None,
        "retrieved_context": [],
    }

    flow = create_flow()
    flow.run(shared)


if __name__ == "__main__":
    main()
