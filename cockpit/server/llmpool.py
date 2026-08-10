
import os
import re
import json
import time
import threading
import datetime

_DEFAULT_COOLDOWN = 300

_DEFAULT_SWITCH_PCT = int(os.environ.get("PN_LLMPOOL_SWITCH_PCT", "90") or 90)
_REJECT_STATUSES = ("rejected", "blocked", "exceeded", "reached", "rate_limited", "throttled")
_WARN_STATUSES = ("warning", "allowed_warning", "approaching", "near_limit")

_AUTH_REASON_MARKERS = (
    ("org_disabled", ("disabled claude subscription access",
                      "oauth authentication is currently not allowed for this organization",
                      "oauth_not_allowed_for_organization",
                      "ask your admin to enable access")),
    ("logged_out",   ("not logged in", "please run /login", "please run `claude /login`")),
    ("expired",      ("oauth token has expired", "refresh token has expired")),
    ("invalid",      ("invalid api key",)),
    ("balance",      ("credit balance is too low",)),
)

_AUTH_REASON_DE = {
    "org_disabled": ("Claude-Zugang abgelehnt: dieses Konto hat Claude Code deaktiviert "
                     "— bitte anderes Konto verbinden."),
    "logged_out":   "Claude-Zugang nicht verbunden: bitte Konto neu anmelden.",
    "expired":      "Claude-Zugang abgelaufen: bitte Konto neu anmelden.",
    "invalid":      ("Claude-Zugang ungültig: die hinterlegte Anmeldung wird abgelehnt "
                     "— bitte Konto neu verbinden."),
    "balance":      "Claude-Kontingent aufgebraucht: bitte Abo oder Guthaben prüfen.",
}

_AUTH_OWNER_ONLY = ("org_disabled",)

def auth_reason_for(text):

    low = (text or "").lower()
    for reason, markers in _AUTH_REASON_MARKERS:
        if any(m in low for m in markers):
            return reason
    return None

def auth_status_de(reason):

    return _AUTH_REASON_DE.get(reason or "", "")

def _parse_ts(v):

    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        f = float(v)
        if f > 1e12:
            return f / 1000.0
        return f if f > 1e6 else 0.0
    s = str(v or "").strip()
    if not s:
        return 0.0
    if s.replace(".", "", 1).isdigit():
        f = float(s)
        return f / 1000.0 if f > 1e12 else (f if f > 1e6 else 0.0)
    s = s.replace("Z", "+0000")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.datetime.strptime(s, fmt).timestamp()
        except (ValueError, OverflowError):
            continue
    return 0.0

def parse_stream(stdout):

    text = ""
    events = []
    is_error = False
    assistant = []
    saw_json = False
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if not line or line[0] != "{":
            continue
        try:
            o = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(o, dict):
            continue
        saw_json = True
        t = o.get("type")
        if t == "rate_limit_event" or "rate_limit_info" in o:
            events.append(o)
        if t == "result":
            if o.get("is_error") or str(o.get("subtype", "")).startswith("error"):
                is_error = True
            r = o.get("result")
            if isinstance(r, str) and r:
                text = r
        elif t == "assistant":
            msg = o.get("message") or {}
            for part in (msg.get("content") or []):
                if isinstance(part, dict) and part.get("type") == "text":
                    assistant.append(str(part.get("text", "")))
    if not text and assistant:
        text = "".join(assistant).strip()
    if not saw_json:
        text = (stdout or "").strip()
    return text, events, is_error

def looks_rate_limited(returncode, stderr, text, events):

    for ev in (events or []):
        info = ev.get("rate_limit_info") if isinstance(ev.get("rate_limit_info"), dict) else ev
        if str(info.get("status") or "").lower() in _REJECT_STATUSES:
            return True
    if returncode != 0:
        blob = ((stderr or "") + " " + (text or "")).lower()
        if any(k in blob for k in ("rate limit", "usage limit", "429", "too many requests",
                                   "limit reached", "resets at", "quota")):
            return True
    return False

def _blank_state():
    return {
        "five_hour": {"status": "", "resets_at": 0.0, "at": 0.0},
        "seven_day": {"status": "", "resets_at": 0.0, "at": 0.0},
        "cooldown_until": 0.0,
        "last_used": 0.0,

        "auth_reason": "", "auth_at": 0.0,
        "calls": 0, "errors": 0, "rate_limited": 0,
        "alerted_renewal": 0.0,
    }

def next_monthly_renewal(created_epoch, now):

    if not created_epoch or created_epoch <= 0:
        return 0.0
    import calendar
    dt = datetime.datetime.fromtimestamp(created_epoch)
    y, m, anchor = dt.year, dt.month, dt.day
    for _ in range(600):
        cand = dt.replace(year=y, month=m, day=min(anchor, calendar.monthrange(y, m)[1]))
        if cand.timestamp() >= now:
            return cand.timestamp()
        m += 1
        if m > 12:
            m = 1; y += 1
    return 0.0

_INFO_CACHE = {}
_INFO_TTL = 60.0

def read_account_info(home, usage_path=None, now=None, ttl=_INFO_TTL):

    now = time.time() if now is None else now
    cached = _INFO_CACHE.get(home)
    if cached and (now - cached[0]) < ttl:
        return cached[1]
    info = {"email": "", "display_name": "", "org": "", "subscription": "", "tier": "",
            "subscription_created": 0.0, "next_renewal": 0.0, "token_expires": 0.0,
            "five_hour_pct": None, "seven_day_pct": None,
            "five_hour_resets_at": 0.0, "seven_day_resets_at": 0.0, "usage_at": 0.0}
    try:
        with open(os.path.join(home, ".claude.json")) as f:
            oa = (json.load(f) or {}).get("oauthAccount") or {}
        info["email"] = oa.get("emailAddress") or ""
        info["display_name"] = oa.get("displayName") or ""
        info["org"] = oa.get("organizationName") or ""
        info["tier"] = oa.get("organizationRateLimitTier") or oa.get("userRateLimitTier") or ""
        info["subscription_created"] = _parse_ts(oa.get("subscriptionCreatedAt"))
        info["next_renewal"] = next_monthly_renewal(info["subscription_created"], now)
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.join(home, ".claude", ".credentials.json")) as f:
            o = (json.load(f) or {}).get("claudeAiOauth") or {}
        info["subscription"] = o.get("subscriptionType") or ""
        info["tier"] = info["tier"] or (o.get("rateLimitTier") or "")
        info["token_expires"] = _parse_ts(o.get("expiresAt"))
    except (OSError, ValueError):
        pass
    up = usage_path or os.path.expanduser("~/.claude/accounts/usage.json")
    if info["email"]:
        try:
            with open(up) as f:
                u = (json.load(f) or {}).get(info["email"]) or {}

            info["usage_at"] = _parse_ts(u.get("ts"))
            _fh_reset = _parse_ts(u.get("five_hour_resets_at"))
            _sd_reset = _parse_ts(u.get("seven_day_resets_at"))
            info["five_hour_resets_at"] = _fh_reset
            info["seven_day_resets_at"] = _sd_reset
            _stale = bool(info["usage_at"]) and (now - info["usage_at"] > 6 * 3600)
            info["usage_stale"] = _stale or not info["usage_at"]
            def _live_pct(val, reset):
                if _stale or not info["usage_at"]:
                    return None
                if reset and now >= reset:
                    return 0.0
                return val
            info["five_hour_pct"] = _live_pct(u.get("five_hour_pct"), _fh_reset)
            info["seven_day_pct"] = _live_pct(u.get("seven_day_pct"), _sd_reset)
        except (OSError, ValueError):
            pass
    _INFO_CACHE[home] = (now, info)
    return info

_OAUTH_CLIENT_ID = os.environ.get("PN_OAUTH_CLIENT_ID", "9d1c250a-e61b-44d9-88ed-5944d1962f5e")
_OAUTH_TOKEN_URLS = tuple(u.strip() for u in os.environ.get(
    "PN_OAUTH_TOKEN_URL",
    "https://platform.claude.com/v1/oauth/token,https://console.anthropic.com/v1/oauth/token"
).split(",") if u.strip())

_OAUTH_UA = os.environ.get("PN_OAUTH_UA", "claude-cli/2.0 (external, brainbox-llmpool)")

def _creds_path(home):
    return os.path.join(os.path.expanduser(home or "~"), ".claude", ".credentials.json")

def _read_oauth(path):
    try:
        with open(path) as f:
            d = json.load(f) or {}
    except (OSError, ValueError):
        return None, {}
    return d, (d.get("claudeAiOauth") or {})

def _is_fresh(o, margin_s, now=None):

    exp = o.get("expiresAt") or 0
    if not exp:
        return True
    return (float(exp) / 1000.0) - (time.time() if now is None else now) > margin_s

def ensure_fresh_credentials(home, margin_s=300, force=False, timeout=25):

    import fcntl
    import urllib.request
    path = _creds_path(home)
    d, o = _read_oauth(path)
    if d is None:
        return "no-creds", None
    tok0 = o.get("accessToken") or ""
    if not force and _is_fresh(o, margin_s):
        return "fresh", tok0
    if not o.get("refreshToken"):
        return "no-refresh", tok0 or None
    lockp = path + ".lock"
    with open(lockp, "w") as lk:
        try:
            os.chmod(lockp, 0o600)
        except OSError:
            pass
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        d, o = _read_oauth(path)
        if d is None:
            return "no-creds", None
        tok = o.get("accessToken") or ""
        if _is_fresh(o, margin_s) and (not force or tok != tok0):
            return ("refreshed" if tok != tok0 else "fresh"), tok
        rt = o.get("refreshToken")
        if not rt:
            return "no-refresh", tok or None
        body = json.dumps({"grant_type": "refresh_token", "refresh_token": rt,
                           "client_id": _OAUTH_CLIENT_ID}).encode()
        resp = None
        last_err = None
        for url in _OAUTH_TOKEN_URLS:
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json",
                                         "User-Agent": _OAUTH_UA}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    resp = json.load(r)
                break
            except Exception as e:
                detail = ""
                try:
                    if hasattr(e, "read"):
                        eb = json.loads(e.read().decode() or "{}")
                        detail = (str(eb.get("error") or "") + " " +
                                  str(eb.get("error_description") or ""))[:160]
                except Exception:
                    pass
                last_err = "%s: %s %s" % (url.split("/")[2] if "//" in url else url,
                                           e.__class__.__name__, (str(e)[:120] + " " + detail).strip())
                resp = None
                continue
        if not isinstance(resp, dict) or not resp.get("access_token"):
            if last_err:
                try:
                    import sys as _sys
                    _sys.stderr.write("[llmpool] token-refresh %s: %s\n"
                                      % (os.path.basename(str(home or "")), last_err))
                except Exception:
                    pass
            return "failed", tok or None
        o = dict(o)
        o["accessToken"] = resp["access_token"]
        if resp.get("refresh_token"):
            o["refreshToken"] = resp["refresh_token"]
        o["expiresAt"] = int((time.time() + float(resp.get("expires_in") or 28800)) * 1000)
        if isinstance(resp.get("scope"), str) and resp["scope"]:
            o["scopes"] = resp["scope"].split()
        d["claudeAiOauth"] = o
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(json.dumps(d))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return "refreshed", o["accessToken"]

def refresh_enabled_accounts(cfg_path=None, default_home=None, margin_s=1800):

    default_home = default_home or os.path.expanduser("~")
    cfg_path = cfg_path or os.path.expanduser("~/.config/brainbox-portal/llmpool.json")
    homes = {"primary": default_home}
    try:
        with open(cfg_path) as f:
            cfg = json.load(f) or {}
        for a in (cfg.get("accounts") or []):
            if str(a.get("provider") or "claude").lower() != "claude":
                continue
            if a.get("enabled") and a.get("home") and a.get("id"):
                homes[str(a["id"])] = os.path.expanduser(str(a["home"]))
    except (OSError, ValueError):
        pass
    out = []
    for aid, home in sorted(homes.items()):
        try:
            st, _ = ensure_fresh_credentials(home, margin_s=margin_s)
        except Exception:
            st = "failed"
        out.append({"id": aid, "status": st})
    return out

def refresh_usage_accounts(cfg_path=None, state_path=None, default_home=None, usage_path=None):

    try:
        cfg_path = cfg_path or os.path.expanduser("~/.config/brainbox-portal/llmpool.json")
        default_home = default_home or os.path.expanduser("~")
        state_path = state_path or os.path.expanduser(
            "~/.local/share/brainbox-portal/llmpool_state.json")
        p = LLMPool(cfg_path, state_path, default_home, usage_path=usage_path)
        res = p.refresh_usage()

        return {"ok": bool(res.get("ok")), "updated": res.get("updated", 0),
                "results": [{"id": r.get("id"), "status": r.get("status")}
                            for r in (res.get("results") or [])]}
    except Exception as e:
        return {"ok": False, "msg": "%s" % type(e).__name__, "results": []}

class LLMPool:

    def __init__(self, cfg_path, state_path, default_home, usage_path=None):
        self._lock = threading.RLock()
        self.cfg_path = cfg_path
        self.state_path = state_path
        self.default_home = default_home
        self.usage_path = usage_path or os.path.expanduser("~/.claude/accounts/usage.json")
        self._state = {}
        self._inflight = {}
        self._load_config()
        self._load_state()

    def _now(self):
        return time.time()

    @staticmethod
    def _cc_scope(home):

        try:
            with open(os.path.join(home, ".claude", ".credentials.json")) as f:
                cr = json.load(f)
        except (OSError, ValueError):
            return None
        o = cr.get("claudeAiOauth") or cr
        sc = o.get("scopes") or o.get("scope")
        if sc is None:
            return None
        if isinstance(sc, str):
            sc = sc.split()
        return "user:sessions:claude_code" in sc

    def _load_config(self):
        cfg = {}
        try:
            with open(self.cfg_path) as f:
                cfg = json.load(f) or {}
        except (OSError, ValueError):
            cfg = {}
        norm, seen = [], set()
        for a in (cfg.get("accounts") or []):
            if not isinstance(a, dict):
                continue
            aid = str(a.get("id") or "").strip()
            home = str(a.get("home") or "").strip()
            if not aid or not home or aid in seen:
                continue
            seen.add(aid)
            home_x = os.path.expanduser(home)
            enabled = bool(a.get("enabled", True))

            norm.append({
                "id": aid,
                "home": home_x,
                "enabled": enabled,
                "provider": (str(a.get("provider") or "claude").strip().lower() or "claude"),
                "weight": max(1, int(a.get("weight", 1) or 1)),
            })
        if not norm:
            norm = [{"id": "primary", "home": self.default_home, "enabled": True,
                     "provider": "claude", "weight": 1}]
        self.accounts = norm

        try:
            _mc = cfg.get("max_concurrent")
            if _mc:
                self.max_concurrent = max(1, min(int(_mc), 64))
            else:
                self.max_concurrent = max(4, sum(1 for a in norm if a["enabled"]))
        except (TypeError, ValueError):
            self.max_concurrent = 4

    _USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
    _USAGE_BETA = "oauth-2025-04-20"

    def _account_bearer(self, home):

        try:
            ensure_fresh_credentials(home, margin_s=1800)
        except Exception:
            pass
        try:
            with open(os.path.join(home, ".claude", ".credentials.json")) as f:
                return ((json.load(f) or {}).get("claudeAiOauth") or {}).get("accessToken") or ""
        except (OSError, ValueError):
            return ""

    def refresh_usage(self, now=None):

        import urllib.request, urllib.error
        now = self._now() if now is None else now
        with self._lock:
            accts = [dict(a) for a in self.accounts]
        try:
            with open(self.usage_path) as f:
                store = json.load(f) or {}
        except (OSError, ValueError):
            store = {}
        results, updated = [], 0
        for a in accts:
            aid = a["id"]
            if a.get("provider", "claude") != "claude":
                results.append({"id": aid, "status": "übersprungen (%s)" % a.get("provider")})
                continue
            info = read_account_info(a["home"], self.usage_path, now, ttl=0)
            email = info.get("email") or ""
            tok = self._account_bearer(a["home"])
            if not tok:
                results.append({"id": aid, "email": email, "status": "kein Token (bitte anmelden)"})
                continue
            req = urllib.request.Request(self._USAGE_URL, headers={
                "Authorization": "Bearer " + tok, "anthropic-beta": self._USAGE_BETA,
                "User-Agent": "brainbox-usage/1", "Accept": "application/json"})
            try:
                r = urllib.request.urlopen(req, timeout=20)
                body = json.load(r) or {}
            except urllib.error.HTTPError as e:
                msg = ("Token ohne Nutzungs-Scope" if e.code == 403
                       else "vom Anbieter gedrosselt (429)" if e.code == 429
                       else "HTTP %s" % e.code)
                results.append({"id": aid, "email": email, "status": msg})
                continue
            except Exception as e:
                results.append({"id": aid, "email": email, "status": "Netzfehler (%s)" % type(e).__name__})
                continue
            fh = (body.get("five_hour") or {})
            sd = (body.get("seven_day") or {})
            p5 = fh.get("utilization"); p7 = sd.get("utilization")
            if not email:
                results.append({"id": aid, "status": "5h=%s%% 7d=%s%% (kein E-Mail-Schlüssel — nicht gespeichert)"
                                % (p5, p7), "five_hour_pct": p5, "seven_day_pct": p7})
                continue
            store[email] = {
                "five_hour_pct": float(p5) if p5 is not None else None,
                "seven_day_pct": float(p7) if p7 is not None else None,
                "ts": int(now),
                "five_hour_resets_at": _parse_ts(fh.get("resets_at")),
                "seven_day_resets_at": _parse_ts(sd.get("resets_at")),
            }
            updated += 1
            results.append({"id": aid, "email": email, "status": "ok",
                            "five_hour_pct": p5, "seven_day_pct": p7})
        if updated:
            try:
                os.makedirs(os.path.dirname(self.usage_path), exist_ok=True)
                tmp = self.usage_path + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(store, f)
                os.replace(tmp, self.usage_path)
            except OSError as e:
                return {"ok": False, "msg": "Schreiben fehlgeschlagen: %s" % e, "results": results}
            with self._lock:
                _INFO_CACHE.clear()
        return {"ok": True, "updated": updated, "results": results}

    def reload(self):

        with self._lock:
            self._load_config()
            for a in self.accounts:
                self._state.setdefault(a["id"], _blank_state())
                self._inflight.setdefault(a["id"], 0)
        return {"ok": True, "accounts": len(self.accounts), "multi": self.multi()}

    def _read_config_raw(self):
        try:
            with open(self.cfg_path) as f:
                return json.load(f) or {}
        except (OSError, ValueError):
            return {}

    def _write_config(self, cfg):
        os.makedirs(os.path.dirname(self.cfg_path), exist_ok=True)
        tmp = self.cfg_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, self.cfg_path)

    def add_account(self, aid, home=None, enabled=False, provider="claude"):

        aid = str(aid or "").strip()
        provider = (str(provider or "claude").strip().lower() or "claude")
        if not aid or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$", aid):
            return {"ok": False, "msg": "bad account id"}
        with self._lock:
            cfg = self._read_config_raw()
            accts = cfg.setdefault("accounts", [])
            if not accts:
                accts.append({"id": "primary", "home": self.default_home, "enabled": True, "weight": 1})
            existing = next((a for a in accts if a.get("id") == aid), None)
            if existing is None:
                home = home or os.path.join(os.path.expanduser("~/.llmpool"), aid)
                accts.append({"id": aid, "home": home, "enabled": bool(enabled),
                              "provider": provider, "weight": 1})
            elif home:
                existing["home"] = home
            try:
                os.makedirs(os.path.expanduser(
                    (existing or {}).get("home") or home or ""), exist_ok=True)
            except OSError:
                pass
            self._write_config(cfg)
            self._load_config()
            for a in self.accounts:
                self._state.setdefault(a["id"], _blank_state())
                self._inflight.setdefault(a["id"], 0)
            return {"ok": True, "id": aid, "home": self.account_home(aid)}

    def set_enabled(self, aid, val):
        with self._lock:
            cfg = self._read_config_raw()
            if not cfg.get("accounts"):
                cfg["accounts"] = [{"id": a["id"], "home": a["home"], "enabled": a["enabled"],
                                    "weight": a["weight"]} for a in self.accounts]
            found = False
            for a in cfg["accounts"]:
                if a.get("id") == aid:
                    a["enabled"] = bool(val); found = True
            if not found:
                return {"ok": False, "msg": "unknown account"}
            self._write_config(cfg)
            self._load_config()
            return {"ok": True, "id": aid, "enabled": bool(val)}

    def remove_account(self, aid):

        if aid == "primary":
            return {"ok": False, "msg": "cannot remove the primary account"}
        with self._lock:
            cfg = self._read_config_raw()
            accts = cfg.get("accounts") or []
            new = [a for a in accts if a.get("id") != aid]
            if len(new) == len(accts):
                return {"ok": False, "msg": "unknown account"}
            cfg["accounts"] = new
            self._write_config(cfg)
            self._load_config()
            self._state.pop(aid, None)
            self._inflight.pop(aid, None)
            return {"ok": True, "id": aid}

    def account_home(self, aid):
        for a in self.accounts:
            if a["id"] == aid:
                return a["home"]
        return None

    def multi(self):

        return sum(1 for a in self.accounts if a["enabled"]) >= 2

    def enabled_count(self):
        return sum(1 for a in self.accounts if a["enabled"])

    def _load_state(self):
        data = {}
        try:
            with open(self.state_path) as f:
                data = json.load(f) or {}
        except (OSError, ValueError):
            data = {}
        for a in self.accounts:
            st = _blank_state()
            saved = data.get(a["id"])
            if isinstance(saved, dict):
                for k, v in _blank_state().items():
                    if isinstance(v, dict):
                        st[k] = {**v, **(saved.get(k) or {})}
                    else:
                        st[k] = saved.get(k, v)
            self._state[a["id"]] = st
            self._inflight[a["id"]] = 0

    def _save(self):
        tmp = self.state_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(tmp, "w") as f:
                json.dump(self._state, f)
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    def _rank(self, aid, now):

        st = self._state[aid]

        if self._auth_reason_live(self.account_home(aid), st):
            return 4
        if st["cooldown_until"] > now:
            return 3
        rank = 0
        for slot in ("five_hour", "seven_day"):
            w = st[slot]
            if float(w.get("resets_at") or 0) <= now:
                continue
            status = str(w.get("status") or "").lower()
            if status in _REJECT_STATUSES:
                return 3
            if status in _WARN_STATUSES:
                rank = max(rank, 1)
        return rank

    def _prefs_path(self):
        return str(self.cfg_path) + ".prefs.json"

    def _prefs(self):

        p = self._prefs_path()
        try:
            mt = os.stat(p).st_mtime
        except OSError:
            return {}
        cur = getattr(self, "_prefs_cache", None)
        if cur and cur[0] == mt:
            return cur[1]
        try:
            with open(p) as f:
                d = json.load(f) or {}
        except Exception:
            d = {}
        self._prefs_cache = (mt, d)
        return d

    def set_prefs(self, preferred=..., switch_pct=...):
        d = dict(self._prefs())
        if preferred is not ...:
            d["preferred"] = preferred or None
        if switch_pct is not ...:
            d["switch_pct"] = int(switch_pct) if switch_pct else None
        tmp = self._prefs_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, self._prefs_path())
        self._prefs_cache = None
        return d

    def _usage_pct(self, aid, now=None):

        now = self._now() if now is None else now
        try:
            a = next((x for x in self.accounts if x["id"] == aid), None)
            if not a:
                return None
            info = read_account_info(a["home"], usage_path=self.usage_path, now=now)
            if info.get("usage_stale"):
                return None
            vals = [info.get("five_hour_pct"), info.get("seven_day_pct")]
            vals = [float(v) for v in vals if v is not None]
            return max(vals) if vals else None
        except Exception:
            return None

    def _over_threshold(self, aid, pct):

        v = self._usage_pct(aid)
        return v is not None and v >= float(pct)

    def pick(self, exclude=(), provider="claude"):

        now = self._now()
        provider = (str(provider or "claude").strip().lower() or "claude")
        with self._lock:
            _prov = [a for a in self.accounts if a.get("provider", "claude") == provider]
            cands = [a for a in _prov if a["enabled"] and a["id"] not in exclude]
            if not cands:
                cands = [a for a in _prov if a["enabled"]] or list(_prov)
            if not cands:
                return None
            ready = [a for a in cands if self._state[a["id"]]["cooldown_until"] <= now]
            pool = ready or cands
            prefs = self._prefs()

            _sw = prefs.get("switch_pct")
            if _sw is None:
                _sw = _DEFAULT_SWITCH_PCT
            if _sw:
                under = [a for a in pool if not self._over_threshold(a["id"], _sw)]
                if under:
                    pool = under
            _pref = prefs.get("preferred")
            if _pref:

                hit = next((a for a in pool if a["id"] == _pref), None)
                if hit is not None:
                    self._inflight[hit["id"]] = self._inflight.get(hit["id"], 0) + 1
                    return dict(hit)

            def _headroom_band(aid):
                hp = self._usage_pct(aid, now)
                return 0 if hp is None else min(4, int(hp // 20))
            best = min(pool, key=lambda a: (
                self._rank(a["id"], now),
                _headroom_band(a["id"]),
                self._inflight[a["id"]],
                self._state[a["id"]]["last_used"],
            ))
            self._inflight[best["id"]] = self._inflight.get(best["id"], 0) + 1
            return dict(best)

    def _apply_event(self, st, ev, now):
        info = ev.get("rate_limit_info") if isinstance(ev.get("rate_limit_info"), dict) else ev
        status = str(info.get("status") or "").lower()
        rtype = str(info.get("rateLimitType") or info.get("rate_limit_type") or info.get("type") or "").lower()
        resets_at = _parse_ts(info.get("resetsAt") or info.get("resets_at") or info.get("resetsAtEpoch") or 0)
        slot = "seven_day" if any(k in rtype for k in ("day", "week", "seven", "7")) else "five_hour"
        st[slot] = {"status": status or st[slot].get("status", ""), "resets_at": resets_at, "at": now}
        if status in _REJECT_STATUSES and resets_at > now:
            st["cooldown_until"] = max(st["cooldown_until"], resets_at)

    def record(self, aid, ok=True, rate_events=None, was_rate_limited=False, auth_reason=None,
               output=None):

        now = self._now()
        if auth_reason is None and output:
            auth_reason = auth_reason_for(output)
        with self._lock:
            st = self._state.get(aid)
            if st is None:
                return
            self._inflight[aid] = max(0, self._inflight.get(aid, 0) - 1)
            st["last_used"] = now
            st["calls"] += 1
            if auth_reason:
                st["auth_reason"] = auth_reason
                st["auth_at"] = now
                ok = False
            elif ok:
                st["auth_reason"] = ""
                st["auth_at"] = 0.0
            if not ok:
                st["errors"] += 1
            if was_rate_limited:
                st["rate_limited"] += 1
            for ev in (rate_events or []):
                try:
                    self._apply_event(st, ev, now)
                except (ValueError, TypeError, AttributeError):
                    continue
            if was_rate_limited and st["cooldown_until"] <= now:
                st["cooldown_until"] = now + _DEFAULT_COOLDOWN
            self._save()

    def _logged_in(self, home, provider="claude"):

        if provider and provider != "claude":
            _cred = {"codex": os.path.join(".codex", "auth.json")}.get(provider)
            if _cred:
                try:
                    return os.path.getsize(os.path.join(home, _cred)) > 2
                except OSError:
                    return False
            return False
        return self._logged_in_claude(home)

    def _logged_in_claude(self, home):

        try:
            with open(os.path.join(home, ".claude.json")) as f:
                if (json.load(f) or {}).get("oauthAccount"):
                    return True
        except (OSError, ValueError):
            pass
        try:
            with open(os.path.join(home, ".claude", ".credentials.json")) as f:
                return bool(((json.load(f) or {}).get("claudeAiOauth") or {}).get("accessToken"))
        except (OSError, ValueError):
            return False

    def _auth_reason_live(self, home, st):

        reason = st.get("auth_reason") or ""
        if not reason:
            return ""
        auth_at = float(st.get("auth_at") or 0)
        if auth_at <= 0 or not home:
            return reason
        newest = 0.0
        for p in (os.path.join(home, ".claude", ".credentials.json"),
                  os.path.join(home, ".claude.json")):
            try:
                m = os.path.getmtime(p)
            except OSError:
                m = 0.0
            if m > newest:
                newest = m
        return "" if newest > auth_at else reason

    def _win_view(self, w, now):
        resets_at = float(w.get("resets_at") or 0)
        return {
            "status": w.get("status") or "",
            "resets_in_s": max(0, int(resets_at - now)) if resets_at > now else 0,
            "observed_at": float(w.get("at") or 0),
        }

    def snapshot(self):

        now = self._now()
        with self._lock:
            accts = []
            dirty = False
            for a in self.accounts:
                st = self._state[a["id"]]
                cd = max(0, int(st["cooldown_until"] - now))
                reason = self._auth_reason_live(a["home"], st)
                if not reason and st.get("auth_reason"):

                    st["auth_reason"] = ""
                    st["auth_at"] = 0.0
                    dirty = True
                logged_in = self._logged_in(a["home"], a.get("provider", "claude"))
                accts.append({
                    "id": a["id"],
                    "home": a["home"],
                    "enabled": a["enabled"],
                    "provider": a.get("provider", "claude"),
                    "weight": a["weight"],
                    "logged_in": logged_in,

                    "usable": bool(logged_in and a["enabled"] and not reason),
                    "auth_reason": reason,

                    "cc_scope": self._cc_scope(a["home"]),
                    "status_de": auth_status_de(reason),
                    "owner_action_required": reason in _AUTH_OWNER_ONLY,
                    "auth_at": float(st.get("auth_at") or 0),
                    "inflight": self._inflight.get(a["id"], 0),
                    "cooling": cd > 0,
                    "cooldown_s": cd,
                    "five_hour": self._win_view(st["five_hour"], now),
                    "seven_day": self._win_view(st["seven_day"], now),
                    "calls": st["calls"],
                    "errors": st["errors"],
                    "rate_limited": st["rate_limited"],
                    "last_used": float(st["last_used"] or 0),
                    "info": read_account_info(a["home"], self.usage_path, now),
                })
            if dirty:
                self._save()
            usable = [a for a in accts if a["usable"]]
            refused = [a for a in accts if a["enabled"] and a["auth_reason"]]

            if usable:
                status_de = ""
            elif refused:
                status_de = (refused[0]["status_de"] +
                             " (betroffen: %s)" % ", ".join(a["id"] for a in refused))
            else:
                status_de = "Kein Claude-Konto verbunden: bitte im Portal ein Konto anmelden."
            return {
                "preferred": self._prefs().get("preferred"),
                "switch_pct": self._prefs().get("switch_pct"),
                "ok": True,
                "multi": self.multi(),
                "enabled": self.enabled_count(),
                "usable": len(usable),
                "degraded": not usable,
                "status_de": status_de,
                "max_concurrent": self.max_concurrent,
                "accounts": accts,
            }

    def renewals_due(self, days=2, now=None):

        now = self._now() if now is None else now
        out = []
        with self._lock:
            for a in self.accounts:
                info = read_account_info(a["home"], self.usage_path, now, ttl=0)
                renewal = float(info.get("next_renewal") or 0)
                if not renewal:
                    continue
                if 0 <= (renewal - now) <= days * 86400:
                    st = self._state.get(a["id"]) or {}
                    if abs(float(st.get("alerted_renewal") or 0) - renewal) < 43200:
                        continue
                    out.append({"id": a["id"], "email": info.get("email") or "",
                                "renewal_ts": renewal, "days_left": max(0, (renewal - now) / 86400.0),
                                "tier": info.get("tier") or "", "display_name": info.get("display_name") or ""})
        return out

    def mark_alerted(self, aid, renewal_ts):
        with self._lock:
            st = self._state.get(aid)
            if st is not None:
                st["alerted_renewal"] = float(renewal_ts)
                self._save()

    def clear_cooldown(self, aid):

        with self._lock:
            st = self._state.get(aid)
            if st is None:
                return {"ok": False, "msg": "unknown account"}
            st["cooldown_until"] = 0.0
            st["five_hour"]["status"] = ""
            st["seven_day"]["status"] = ""
            self._save()
            return {"ok": True, "id": aid}

    def clear_auth_refusal(self, aid):

        with self._lock:
            st = self._state.get(aid)
            if st is None:
                return {"ok": False, "msg": "unknown account"}
            st["auth_reason"] = ""
            st["auth_at"] = 0.0
            self._save()
            return {"ok": True, "id": aid}
