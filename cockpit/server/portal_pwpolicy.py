
import os
import re

MIN_LEN = int(os.environ.get("PN_PW_MIN_LEN", "8"))
MAX_LEN = 4096

_DIGIT = re.compile(r"\d")
_SPECIAL = re.compile(r"[^A-Za-z0-9]")

_BUILTIN_COMMON = {
    "12345678", "123456789", "1234567890", "password", "passwort", "qwertz123", "qwerty123",
    "111111111", "000000000", "iloveyou1", "admin1234", "letmein12", "password1", "passwort1",
    "password1!", "passwort1!", "welcome123", "abcd1234", "1234abcd", "test1234", "changeme1",
    "password123", "passwort123", "qwertzuiop", "adminadmin", "brainbox1", "brainarbeit1",
}

def _extra_blocklist():
    p = (os.environ.get("PN_PW_BLOCKLIST") or "").strip()
    if not p or not os.path.isfile(p):
        return frozenset()
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return frozenset(line.strip().lower() for line in f if line.strip())
    except Exception:
        return frozenset()

def is_common(pw: str) -> bool:
    low = (pw or "").strip().lower()
    return low in _BUILTIN_COMMON or low in _extra_blocklist()

def check(pw, *, username: str | None = None) -> tuple[bool, str]:

    pw = pw if isinstance(pw, str) else ""
    if len(pw) < MIN_LEN:
        return (False, "Zu kurz — mindestens %d Zeichen." % MIN_LEN)
    if len(pw) > MAX_LEN:
        return (False, "Zu lang.")
    if not _DIGIT.search(pw):
        return (False, "Mindestens eine Zahl muss enthalten sein.")
    if not _SPECIAL.search(pw):
        return (False, "Mindestens ein Sonderzeichen muss enthalten sein.")
    if username and pw.strip().lower() == str(username).strip().lower():
        return (False, "Das Passwort darf nicht dein Benutzername sein.")
    if is_common(pw):
        return (False, "Dieses Passwort ist zu häufig/bekannt — bitte ein anderes wählen.")
    return (True, "")

def rules_text() -> str:

    return ("Mindestens %d Zeichen, mindestens eine Zahl und ein Sonderzeichen; "
            "keine sehr häufigen Passwörter." % MIN_LEN)
