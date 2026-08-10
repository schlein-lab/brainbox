

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

FIRST_CLASS = frozenset(("claude", "codex", "ollama", "gemini"))

PRESETS = (
    {"id": "openai",     "name": "OpenAI (API-Key)",   "base_url": "https://api.openai.com/v1",
     "key_console": "https://platform.openai.com/api-keys"},
    {"id": "anthropic",  "name": "Anthropic (API-Key)", "base_url": "https://api.anthropic.com/v1",
     "key_console": "https://console.anthropic.com/settings/keys"},
    {"id": "mistral",    "name": "Mistral",            "base_url": "https://api.mistral.ai/v1",
     "key_console": "https://console.mistral.ai"},
    {"id": "deepseek",   "name": "DeepSeek",           "base_url": "https://api.deepseek.com/v1",
     "key_console": "https://platform.deepseek.com/api_keys"},
    {"id": "groq",       "name": "Groq (hostet Llama)", "base_url": "https://api.groq.com/openai/v1",
     "key_console": "https://console.groq.com/keys"},
    {"id": "kimi",       "name": "Kimi (Moonshot)",    "base_url": "https://api.moonshot.ai/v1",
     "key_console": "https://platform.kimi.ai/console/api-keys"},
    {"id": "qwen",       "name": "Qwen (Alibaba)",     "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
     "key_console": "https://modelstudio.console.alibabacloud.com"},
    {"id": "xai",        "name": "xAI",                "base_url": "https://api.x.ai/v1",
     "key_console": "https://console.x.ai"},
    {"id": "cohere",     "name": "Cohere",             "base_url": "https://api.cohere.ai/compatibility/v1",
     "key_console": "https://dashboard.cohere.com"},
    {"id": "openrouter", "name": "OpenRouter (viele Modelle)", "base_url": "https://openrouter.ai/api/v1",
     "key_console": "https://openrouter.ai/keys"},
    {"id": "together",   "name": "Together (hostet Llama)", "base_url": "https://api.together.xyz/v1",
     "key_console": "https://api.together.ai"},
)

def presets_available():

    have = {e["id"] for e in entries()}
    return [dict(p) for p in PRESETS if p["id"] not in have]

_DATA_DIR = os.environ.get("PN_BRAINBOX_DATA",
                           os.path.expanduser("~/.local/share/brainbox-portal"))
CFG_PATH = os.environ.get("PN_ENDPOINTS_FILE", os.path.join(_DATA_DIR, "endpoints.json"))

DEFAULT_OLLAMA = os.environ.get("PN_OLLAMA_ENDPOINT", "http://127.0.0.1:11434")

DISCOVERY_TTL = float(os.environ.get("PN_ENDPOINT_DISCOVERY_TTL", "45"))

NEGATIVE_TTL = float(os.environ.get("PN_ENDPOINT_NEG_TTL", "15"))
_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{0,40}$")

_LOCK = threading.Lock()
_CFG_CACHE = {"mtime": None, "data": None}
_DISCO_CACHE = {}
_REFRESH = {"started": False}

import portal_zustand as _zst
_zst.register("llm_endpoints._CFG_CACHE", "cache", __name__, ref=_CFG_CACHE,
              beschreibung="llm-endpoints-Konfigdatei mtime-gecacht",
              neustart="verfaellt", schreiber="Konfig-Leser bei mtime-Wechsel")
_zst.register("llm_endpoints._DISCO_CACHE", "cache", __name__, ref=_DISCO_CACHE, ttl_s=45.0,
              beschreibung="Modell-Discovery je (base_url, discovery); Negativ-TTL 15 s — auch NICHT-Antworten wird gemerkt (kein TCP-Klopfen je Brains-Poll)",
              neustart="verfaellt", schreiber="models() + Hintergrund-Refresher")
_zst.register("llm_endpoints._REFRESH", "singleton", __name__, ref=_REFRESH,
              beschreibung="Started-Flag des Hintergrund-Refreshers",
              neustart="rekonstruiert", schreiber="models() beim Erststart")

def _ollama_seed():
    return {"id": "ollama", "name": "Ollama", "base_url": DEFAULT_OLLAMA, "api_key": "",
            "kind": "openai", "discovery": "ollama", "agentic": False, "model": ""}

def _norm(e):

    if not isinstance(e, dict):
        return None
    pid = str(e.get("id") or "").strip().lower()
    if not _ID_RE.match(pid):
        return None
    base = str(e.get("base_url") or "").strip().rstrip("/")
    if not re.match(r"^https?://", base):
        return None
    disc = str(e.get("discovery") or "").strip().lower()
    if disc not in ("ollama", "openai"):
        disc = "ollama" if pid == "ollama" else "openai"
    return {"id": pid,
            "name": str(e.get("name") or pid).strip()[:60] or pid,
            "base_url": base,
            "api_key": str(e.get("api_key") or ""),
            "kind": "openai",
            "discovery": disc,

            "agentic": False,
            "model": str(e.get("model") or "").strip()[:120]}

def _seed_file(path):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"providers": [_ollama_seed()]}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError:
        pass

def load():

    path = CFG_PATH
    try:
        mt = os.path.getmtime(path)
    except OSError:
        _seed_file(path)
        try:
            mt = os.path.getmtime(path)
        except OSError:
            return {"providers": [_ollama_seed()]}
    with _LOCK:
        if _CFG_CACHE["mtime"] == mt and _CFG_CACHE["data"] is not None:
            return _CFG_CACHE["data"]
    data = {"providers": []}
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        provs = raw.get("providers") if isinstance(raw, dict) else raw
        out = []
        seen = set()
        for e in (provs or []):
            n = _norm(e)
            if n and n["id"] not in seen:
                seen.add(n["id"])
                out.append(n)
        data = {"providers": out}
    except Exception:
        data = {"providers": [_ollama_seed()]}
    with _LOCK:
        _CFG_CACHE.update(mtime=mt, data=data)
    return data

def entries():
    return list(load().get("providers") or [])

def get(pid):
    pid = str(pid or "").strip().lower()
    for e in entries():
        if e["id"] == pid:
            return e
    return None

def ids():
    return [e["id"] for e in entries()]

def is_endpoint(pid):

    return get(pid) is not None

def tested(pid):
    return str(pid or "").strip().lower() in FIRST_CLASS

def save(providers):

    out = []
    seen = set()
    for e in (providers or []):
        n = _norm(e)
        if not n:
            return (False, "Eintrag unvollstaendig (id/base_url pruefen)")
        if n["id"] in seen:
            return (False, "doppelte id: %s" % n["id"])
        seen.add(n["id"])
        out.append(n)
    try:
        os.makedirs(os.path.dirname(CFG_PATH), exist_ok=True)
        tmp = CFG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"providers": out}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CFG_PATH)
    except OSError as e:
        return (False, str(e))
    with _LOCK:
        _CFG_CACHE.update(mtime=None, data=None)
        _DISCO_CACHE.clear()
    return (True, None)

def upsert(entry):
    n = _norm(entry)
    if not n:
        return (False, "Eintrag unvollstaendig (id/base_url pruefen)")
    provs = [e for e in entries() if e["id"] != n["id"]]
    provs.append(n)
    return save(provs)

def delete(pid):
    pid = str(pid or "").strip().lower()
    return save([e for e in entries() if e["id"] != pid])

def _http_json(url, headers=None, method="GET", body=None, timeout=6):

    data = None
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return (r.status, json.loads(raw) if raw.strip() else None)
            except ValueError:
                return (r.status, None)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8", "replace")
            return (e.code, json.loads(raw) if raw.strip() else None)
        except Exception:
            return (e.code, None)
    except Exception:
        return (0, None)

def _v1(base):

    b = (base or "").rstrip("/")
    return b if b.endswith("/v1") else b + "/v1"

def _server_root(base):

    b = (base or "").rstrip("/")
    return b[:-3] if b.endswith("/v1") else b

def _auth_headers(entry):
    key = (entry or {}).get("api_key") or ""
    return {"Authorization": "Bearer " + key} if key else {}

def _discover(entry, timeout=4):

    base = entry["base_url"]
    hdrs = _auth_headers(entry)
    out = []
    if entry.get("discovery") == "ollama":
        st, obj = _http_json(_server_root(base) + "/api/tags", headers=hdrs, timeout=timeout)
        if isinstance(obj, dict) and isinstance(obj.get("models"), list):
            for m in obj["models"]:
                if isinstance(m, dict) and m.get("name"):
                    out.append({"id": str(m["name"]), "label": str(m["name"])})
            if out:
                return out
    st, obj = _http_json(_v1(base) + "/models", headers=hdrs, timeout=timeout)
    data = obj.get("data") if isinstance(obj, dict) else None
    if isinstance(data, list):
        for m in data:
            mid = (m.get("id") if isinstance(m, dict) else None)
            if mid:
                out.append({"id": str(mid), "label": str(mid)})
    return out

def _ckey(entry):
    return (entry["base_url"], entry.get("discovery"), entry.get("api_key") and "k" or "")

def _probe_into_cache(entry):

    got = _discover(entry)
    with _LOCK:
        _DISCO_CACHE[_ckey(entry)] = {"ts": time.time(), "models": got}
    return got

def _refresh_loop():

    while True:
        try:
            time.sleep(max(5.0, NEGATIVE_TTL))
            for e in entries():
                _probe_into_cache(e)
        except Exception:
            pass

def _ensure_refresher():
    with _LOCK:
        if _REFRESH["started"]:
            return
        _REFRESH["started"] = True
    t = threading.Thread(target=_refresh_loop, name="llm-endpoint-refresh", daemon=True)
    t.start()

def models(entry, ttl=DISCOVERY_TTL):

    if not entry:
        return []
    if ttl and ttl > 0:
        _ensure_refresher()
        with _LOCK:
            row = _DISCO_CACHE.get(_ckey(entry))
        if row is not None:
            age = time.time() - row["ts"]
            fresh_for = ttl if row["models"] else min(ttl, NEGATIVE_TTL)
            if age >= fresh_for:

                pass
            return list(row["models"])

    return list(_probe_into_cache(entry))

def models_for(pid, ttl=DISCOVERY_TTL):
    return models(get(pid), ttl=ttl)

def probe_models(base_url, discovery=None, api_key="", ttl=0):

    e = _norm({"id": "probe", "name": "probe", "base_url": base_url,
               "discovery": discovery or "", "api_key": api_key or ""})
    if not e:
        return None
    return models(e, ttl=ttl)

def reachable(entry, ttl=DISCOVERY_TTL):

    return bool(models(entry, ttl=ttl))

def _pick_model(entry, model):
    m = str(model or "").strip()
    if m:
        return m
    m = str((entry or {}).get("model") or "").strip()
    if m:
        return m
    ms = models(entry)
    return ms[0]["id"] if ms else ""

def chat(entry, model, system, user, timeout=120):

    if not entry:
        return (False, "", "")
    used = _pick_model(entry, model)
    if not used:
        return (False, "", "")
    body = {"model": used, "stream": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}
    st, obj = _http_json(_v1(entry["base_url"]) + "/chat/completions",
                         headers=_auth_headers(entry), method="POST", body=body, timeout=timeout)
    if not (200 <= (st or 0) < 300) or not isinstance(obj, dict):
        return (False, "", used)
    try:
        text = str(((obj.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    except Exception:
        text = ""
    return (bool(text.strip()), text, used)

def _lan_cidrs():

    import subprocess
    out = []
    try:
        r = subprocess.run(["ip", "-o", "-4", "addr", "show", "scope", "global"],
                           capture_output=True, text=True, timeout=4)
        for line in r.stdout.splitlines():
            m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", line)
            if m:
                out.append((m.group(1), int(m.group(2))))
    except Exception:
        pass
    return out

def _tcp_open(host, port, timeout=0.4):
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

def discover_ollama(extra_hosts=None, port=11434, scan_lan=True, timeout=0.4, max_hosts=260):

    import concurrent.futures
    import ipaddress
    cands = ["127.0.0.1"]
    for h in (extra_hosts or []):
        h = str(h or "").strip()

        m = re.match(r"^https?://([^:/]+)", h)
        if m:
            h = m.group(1)
        if h and h not in cands:
            cands.append(h)
    if scan_lan:
        for ip, plen in _lan_cidrs():
            try:
                net = ipaddress.ip_network(ip + "/" + str(max(plen, 24)), strict=False)
            except Exception:
                continue
            for a in net.hosts():
                s = str(a)
                if s not in cands:
                    cands.append(s)
            if len(cands) >= max_hosts:
                break
    cands = cands[:max_hosts]
    open_hosts = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
        futs = {ex.submit(_tcp_open, h, port, timeout): h for h in cands}
        for fut in concurrent.futures.as_completed(futs):
            try:
                if fut.result():
                    open_hosts.append(futs[fut])
            except Exception:
                pass
    found = []
    for h in open_hosts:
        base = "http://%s:%d" % (h, port)
        ms = _discover({"base_url": base, "discovery": "ollama", "api_key": ""}, timeout=3)
        if ms:
            found.append({"host": h, "base_url": base, "models": ms, "count": len(ms)})
    found.sort(key=lambda x: (x["host"] != "127.0.0.1", x["host"]))
    return found

def smoke(entry, model=None, timeout=60):

    import time
    t0 = time.time()
    ok, text, used = chat(
        entry, model,
        "Du bist ein Verbindungstest eines Heim-Server-Portals. Antworte knapp auf Deutsch.",
        "Antworte mit genau einem kurzen Satz, dass die Verbindung funktioniert.",
        timeout=timeout)
    return {"ok": bool(ok), "seconds": round(time.time() - t0, 1),
            "model": used, "reply": (text or "").strip()[:400]}

def public_entry(entry):

    if not entry:
        return None
    return {"id": entry["id"], "name": entry["name"], "base_url": entry["base_url"],
            "discovery": entry["discovery"], "kind": entry["kind"], "agentic": entry["agentic"],
            "model": entry.get("model") or "", "has_key": bool(entry.get("api_key")),
            "tested": tested(entry["id"])}

if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:

        e = get("ollama") or _ollama_seed()
        print("endpoint:", e["base_url"], "discovery:", e["discovery"])
        ms = models(e)
        print("models (LIVE /api/tags):", [m["id"] for m in ms] or "(keine / Endpoint aus)")
        if ms:
            ok, text, used = chat(e, None,
                                  "Du bist ein Test. Antworte mit EINEM Wort.",
                                  "Sag: ok", timeout=60)
            print("chat ok=%s model=%s text=%r" % (ok, used, (text or "")[:80]))
        sys.exit(0)
    print("llm_endpoints: entries =", json.dumps([public_entry(e) for e in entries()], ensure_ascii=False))
