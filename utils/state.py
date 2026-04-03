import json
from pathlib import Path

STATE_DIR = Path.home() / ".life_admin"
LAST_RUN_FILE = STATE_DIR / "last_run.json"


def read_last_run() -> str | None:
    if not LAST_RUN_FILE.exists():
        return None
    text = LAST_RUN_FILE.read_text().strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        return data.get("last_run")
    except json.JSONDecodeError:
        return None


def write_last_run(timestamp: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LAST_RUN_FILE.write_text(json.dumps({"last_run": timestamp}))
