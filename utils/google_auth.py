import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

CREDS_DIR = Path.home() / ".life_admin"
CLIENT_SECRET = CREDS_DIR / "client_secret.json"
TOKEN_FILE = CREDS_DIR / "google_token.json"
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def _token_has_required_scopes() -> bool:
    """Check if the stored token file contains all required scopes."""
    if not TOKEN_FILE.exists():
        return False
    token_data = json.loads(TOKEN_FILE.read_text())
    stored_scopes = set(token_data.get("scopes", []))
    return set(SCOPES).issubset(stored_scopes)


def get_google_creds() -> Credentials:
    """Get valid Google OAuth2 credentials, prompting for consent if needed."""
    creds = None

    # Check scopes before loading — delete stale token if scopes expanded
    if TOKEN_FILE.exists() and not _token_has_required_scopes():
        print("[GoogleAuth] Token missing required scopes — re-authorizing...")
        TOKEN_FILE.unlink()

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())

    if not creds or not creds.valid:
        if not CLIENT_SECRET.exists():
            raise FileNotFoundError(
                f"Google API not configured. "
                f"Place client_secret.json from Google Cloud Console in {CREDS_DIR}/"
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
        creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())

    return creds
