
from __future__ import annotations
import os, json, ssl, smtplib, urllib.request, hmac, hashlib
from email.message import EmailMessage

from . import DATA_DIR

NOTIFY_DIR = os.path.join(DATA_DIR, "notify")
CONFIG_PATH = os.environ.get("PN_NOTIFY_CONFIG", os.path.join(NOTIFY_DIR, "config.json"))

POSTFACH_DIR = os.environ.get("PN_NOTIFY_POSTFACH", os.path.join(NOTIFY_DIR, "postfach"))

DEFAULTS = {
    "rate": {"capacity": 10, "refill_per_sec": 1.0},
    "fallback": {"adapter": "file"},
    "adapters": {
        "native":   {"enabled": True},
        "file":     {"enabled": True, "dir": POSTFACH_DIR,
                     "max_bytes": 4_000_000, "keep": 3},
        "mock":     {"enabled": False},
        "telegram": {"enabled": False, "api_base": "https://api.telegram.org",
                     "timeout_s": 10, "parse_mode": None},
        "email":    {"enabled": False, "smtp_host": "", "smtp_port": 587, "from_addr": "",
                     "starttls": True, "smtp_user": "", "subject_prefix": "[portioneer] ",
                     "timeout_s": 15},
        "webhook":  {"enabled": False, "timeout_s": 8, "sign_header": "X-PN-Signature"},
    },
}

def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out

def load(path: str | None = None) -> dict:

    path = path or CONFIG_PATH
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        with open(path) as f:
            cfg = _deep_merge(cfg, json.load(f))
    except (OSError, ValueError):
        pass
    if os.environ.get("PN_NOTIFY_RATE_CAP"):
        cfg["rate"]["capacity"] = int(os.environ["PN_NOTIFY_RATE_CAP"])
    if os.environ.get("PN_NOTIFY_RATE_REFILL"):
        cfg["rate"]["refill_per_sec"] = float(os.environ["PN_NOTIFY_RATE_REFILL"])
    return cfg

def save(cfg: dict, path: str | None = None) -> str:

    path = path or CONFIG_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return path

def smtp_send(cfg_email: dict):

    def _send(to, subject, body, _passwd_reader=None):
        host = cfg_email.get("smtp_host")
        if not host:
            raise RuntimeError("email adapter enabled but no smtp_host configured")
        port = int(cfg_email.get("smtp_port", 587))
        msg = EmailMessage()
        msg["From"] = cfg_email.get("from_addr") or cfg_email.get("smtp_user") or "portioneer@localhost"
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        timeout = float(cfg_email.get("timeout_s", 15))
        with smtplib.SMTP(host, port, timeout=timeout) as srv:
            if cfg_email.get("starttls", True):
                srv.starttls(context=ssl.create_default_context())
            user = cfg_email.get("smtp_user")
            passwd = _passwd_reader() if _passwd_reader else None
            if user and passwd:
                srv.login(user, passwd)
            srv.send_message(msg)
    return _send

def https_post(cfg_webhook: dict):

    timeout = float(cfg_webhook.get("timeout_s", 8))
    sign_header = cfg_webhook.get("sign_header", "X-PN-Signature")

    def _post(url, json_body, secret):
        data = json.dumps(json_body, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "User-Agent": "portioneer-notify"}
        if secret:
            sig = hmac.new(secret.encode() if isinstance(secret, str) else secret,
                           data, hashlib.sha256).hexdigest()
            headers[sign_header] = "sha256=" + sig
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", resp.getcode())
            return {"ok": 200 <= int(code) < 300, "id": f"http-{code}"}
    return _post

def telegram_send(cfg_tg: dict):

    api_base = cfg_tg.get("api_base", "https://api.telegram.org").rstrip("/")
    timeout = float(cfg_tg.get("timeout_s", 10))
    parse_mode = cfg_tg.get("parse_mode")

    def _send(token, chat_id, text, meta):
        url = f"{api_base}/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", resp.getcode())
            return {"ok": 200 <= int(code) < 300, "id": f"tg-{code}"}
    return _send
