"""Runtime configuration. Secrets come from the environment (or a local .env), never from source."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("BELLHAVEN_DATA_DIR", ROOT / "data"))


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

BASE_URL = os.environ.get("BELLHAVEN_BASE_URL", "https://analyst-assessment-production.up.railway.app").rstrip("/")
API_BASE = f"{BASE_URL}/api/v1"
TOKEN = os.environ.get("BELLHAVEN_TOKEN", "")

PARENT_NAME = "Bellhaven Senior Living (Parent Account)"
SYNC_TAG = "bellhaven-sync"

# A candidate must score at least this to be treated as the same facility.
MATCH_THRESHOLD = 60


def crm_browser_url(account_id: str) -> str:
    return f"{BASE_URL}/crm/{TOKEN}/accounts/{account_id}"


def require_token() -> str:
    if not TOKEN:
        raise SystemExit("BELLHAVEN_TOKEN is not set. Put it in .env or the environment (see .env.example).")
    return TOKEN
