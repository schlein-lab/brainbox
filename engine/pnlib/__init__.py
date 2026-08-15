
import os

STATE_DIR = os.path.join(os.environ.get("XDG_DATA_HOME",
                         os.path.expanduser("~/.local/share")), "portioneer")
DB_PATH = os.path.join(STATE_DIR, "queue.db")
LOG_DIR = os.path.join(STATE_DIR, "logs")

def _resolve_data_dir() -> str:
    env = os.environ.get("PN_DATA_DIR")
    if env:
        return env
    prod = "/var/lib/portioneer"

    if os.path.isdir(prod) and os.access(prod, os.W_OK):
        return prod
    return STATE_DIR

DATA_DIR = _resolve_data_dir()

RECORD_DIR = os.path.join(DATA_DIR, "record")
WORK_DIR = os.path.join(RECORD_DIR, "work")
INDEX_DIR = os.path.join(RECORD_DIR, "index")
TRASH_DIR = os.path.join(RECORD_DIR, ".trash")

CAS_DIR = os.path.join(DATA_DIR, "cas")

VERSION = "0.3.0"
