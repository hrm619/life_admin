import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".life_admin"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS = {
    "lookback_hours": 24,
    "calendar_lookahead_weeks": 4,
    "max_emails": 50,
    "sources": {
        "imessage": True,
        "calendar": True,
        "gmail": True,
        "notes": True,
    },
}


def load_config(config_path: str | None = None) -> dict:
    """Load config from file, creating with defaults if it doesn't exist."""
    path = Path(config_path) if config_path else CONFIG_FILE

    if path.exists():
        with open(path) as f:
            user_config = json.load(f)
        # Merge defaults with user config (user values win)
        config = {**DEFAULTS, **user_config}
        # Deep merge sources dict
        config["sources"] = {**DEFAULTS["sources"], **user_config.get("sources", {})}
        return config

    # Create default config file
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(DEFAULTS, f, indent=2)
    print(f"[Config] Created default config at {path}")
    return dict(DEFAULTS)
