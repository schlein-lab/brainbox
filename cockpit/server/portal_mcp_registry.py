

import os, json, time, threading, secrets

import portal_jobs_persist as pjp

_LOCK = threading.Lock()

_TRANSPORTS = ("stdio", "http", "sse")

def _path(uid):
    return os.path.join(pjp.user_dir(uid), "mcp_servers.json")

def _load(uid):
    try:
        v = json.load(open(_path(uid)))
        return v if isinstance(v, list) else []
    except Exception:
        return []

def _save(uid, servers):
    p = _path(uid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    json.dump(servers, open(tmp, "w"), ensure_ascii=False)
    os.replace(tmp, p)

def list_servers(uid):
    return sorted(_load(uid), key=lambda s: -(s.get("created") or 0))

def add(uid, name, transport, target, extra=None):

    name = str(name or "").strip()[:80]
    transport = str(transport or "stdio").strip().lower()
    target = str(target or "").strip()[:2000]
    if not name:
        return {"ok": False, "error": "Name fehlt"}
    if transport not in _TRANSPORTS:
        return {"ok": False, "error": "Transport muss stdio, http oder sse sein"}
    if not target:
        return {"ok": False, "error": ("Befehl fehlt" if transport == "stdio" else "URL fehlt")}
    if transport in ("http", "sse") and not (target.startswith("http://") or target.startswith("https://")):
        return {"ok": False, "error": "URL muss mit http:// oder https:// beginnen"}
    entry = {"id": "mcp" + secrets.token_hex(5), "created": time.time(),
             "name": name, "transport": transport}
    if transport == "stdio":
        entry["command"] = target
    else:
        entry["url"] = target
    if isinstance(extra, dict):
        for k in ("catalog_id", "homepage", "description"):
            v = str(extra.get(k) or "").strip()
            if v:
                entry[k] = v[:300]
    with _LOCK:
        servers = _load(uid)
        if any(s.get("name") == name for s in servers):
            return {"ok": False, "error": "Ein Server mit diesem Namen existiert schon"}
        servers.append(entry)
        _save(uid, servers)
    return {"ok": True, "server": entry}

def remove(uid, mid):
    mid = str(mid or "")
    with _LOCK:
        servers = _load(uid)
        n = len(servers)
        servers = [s for s in servers if s.get("id") != mid]
        if len(servers) == n:
            return {"ok": False, "error": "unbekannter Server"}
        _save(uid, servers)
    return {"ok": True}

_REGISTRY_API = "https://registry.modelcontextprotocol.io/v0/servers"
_REMOTE_TIMEOUT = 6.0
_REMOTE_LIMIT = 20

_SRV = "https://github.com/modelcontextprotocol/servers/tree/main/src/"
_SRV_ARCH = "https://github.com/modelcontextprotocol/servers-archived/tree/main/src/"

def _npx(pkg):
    return {"type": "stdio", "command": "npx", "args": ["-y", pkg]}

def _uvx(pkg):
    return {"type": "stdio", "command": "uvx", "args": [pkg]}

CATALOG = [
    {"id": "filesystem", "name": "Filesystem",
     "description": "Lese- und Schreibzugriff auf lokale Dateien und Verzeichnisse (freigegebene Pfade als Argumente).",
     "install": _npx("@modelcontextprotocol/server-filesystem"), "homepage": _SRV + "filesystem"},
    {"id": "fetch", "name": "Fetch",
     "description": "Webseiten abrufen und als LLM-freundliches Markdown aufbereiten.",
     "install": _uvx("mcp-server-fetch"), "homepage": _SRV + "fetch"},
    {"id": "git", "name": "Git",
     "description": "Git-Repositories lesen und bedienen: status, diff, log, commit.",
     "install": _uvx("mcp-server-git"), "homepage": _SRV + "git"},
    {"id": "github", "name": "GitHub",
     "description": "GitHub-API: Repos, Issues und Pull Requests verwalten (GITHUB_PERSONAL_ACCESS_TOKEN noetig).",
     "install": _npx("@modelcontextprotocol/server-github"), "homepage": _SRV_ARCH + "github"},
    {"id": "memory", "name": "Memory",
     "description": "Persistentes Wissensgraph-Gedaechtnis ueber Sessions hinweg.",
     "install": _npx("@modelcontextprotocol/server-memory"), "homepage": _SRV + "memory"},
    {"id": "puppeteer", "name": "Puppeteer",
     "description": "Browser-Automatisierung mit Puppeteer: Seiten laden, klicken, Screenshots.",
     "install": _npx("@modelcontextprotocol/server-puppeteer"), "homepage": _SRV_ARCH + "puppeteer"},
    {"id": "playwright", "name": "Playwright",
     "description": "Browser-Automatisierung mit Playwright ueber den Accessibility-Baum (Microsoft, aktiv gepflegt).",
     "install": _npx("@playwright/mcp@latest"), "homepage": "https://github.com/microsoft/playwright-mcp"},
    {"id": "brave-search", "name": "Brave Search",
     "description": "Websuche ueber die Brave Search API (BRAVE_API_KEY noetig).",
     "install": _npx("@modelcontextprotocol/server-brave-search"), "homepage": _SRV_ARCH + "brave-search"},
    {"id": "postgres", "name": "PostgreSQL",
     "description": "Nur-Lese-Zugriff auf PostgreSQL: Schema inspizieren, SQL abfragen (Connection-URL als Argument).",
     "install": _npx("@modelcontextprotocol/server-postgres"), "homepage": _SRV_ARCH + "postgres"},
    {"id": "sqlite", "name": "SQLite",
     "description": "SQLite-Datenbanken abfragen und analysieren (Dateipfad als Argument).",
     "install": _uvx("mcp-server-sqlite"), "homepage": _SRV_ARCH + "sqlite"},
    {"id": "slack", "name": "Slack",
     "description": "Slack-Workspace: Kanaele lesen und Nachrichten senden (Bot-Token noetig).",
     "install": _npx("@modelcontextprotocol/server-slack"), "homepage": _SRV_ARCH + "slack"},
    {"id": "google-drive", "name": "Google Drive",
     "description": "Google-Drive-Dateien suchen und lesen (OAuth-Einrichtung noetig).",
     "install": _npx("@modelcontextprotocol/server-gdrive"), "homepage": _SRV_ARCH + "gdrive"},
    {"id": "sentry", "name": "Sentry",
     "description": "Sentry-Fehlerberichte abrufen und auswerten (Auth-Token noetig).",
     "install": _uvx("mcp-server-sentry"), "homepage": _SRV_ARCH + "sentry"},
    {"id": "time", "name": "Time",
     "description": "Aktuelle Uhrzeit und Zeitzonen-Umrechnung.",
     "install": _uvx("mcp-server-time"), "homepage": _SRV + "time"},
    {"id": "everything", "name": "Everything",
     "description": "Referenz- und Testserver, der alle MCP-Features demonstriert.",
     "install": _npx("@modelcontextprotocol/server-everything"), "homepage": _SRV + "everything"},
]

def catalog_search(q=""):

    q = str(q or "").strip().lower()
    if not q:
        return list(CATALOG)
    out = []
    for e in CATALOG:
        hay = " ".join((e["id"], e["name"], e["description"])).lower()
        if all(tok in hay for tok in q.split()):
            out.append(e)
    return out

def catalog_get(cid):
    cid = str(cid or "").strip().lower()
    for e in CATALOG:
        if e["id"] == cid:
            return e
    return None

def _remote_entry(item):

    srv = item.get("server") if isinstance(item.get("server"), dict) else item
    name = str(srv.get("name") or "")
    entry = {"id": name, "name": str(srv.get("title") or name),
             "description": str(srv.get("description") or "")[:300],
             "version": str(srv.get("version") or "")}
    repo = srv.get("repository") or {}
    if isinstance(repo, dict) and repo.get("url"):
        entry["homepage"] = str(repo["url"])[:300]
    install = None
    for pkg in (srv.get("packages") or []):
        if not isinstance(pkg, dict):
            continue
        rt = str(pkg.get("registryType") or "").lower()
        ident = str(pkg.get("identifier") or "")
        if not ident:
            continue
        if rt == "npm":
            install = _npx(ident)
        elif rt == "pypi":
            install = _uvx(ident)
        else:
            continue
        break
    if install is None:
        for rem in (srv.get("remotes") or []):
            if not isinstance(rem, dict):
                continue
            rurl = str(rem.get("url") or "")
            if rurl.startswith("http://") or rurl.startswith("https://"):
                rtype = str(rem.get("type") or "").lower()
                install = {"type": ("sse" if rtype == "sse" else "http"), "url": rurl[:2000]}
                break
    if install is not None:
        entry["install"] = install
    return entry

def remote_search(q, timeout=_REMOTE_TIMEOUT, limit=_REMOTE_LIMIT):

    q = str(q or "").strip()
    if not q:
        return [], None
    import urllib.request
    import urllib.parse as _up
    url = _REGISTRY_API + "?" + _up.urlencode(
        {"search": q, "version": "latest", "limit": str(max(1, min(int(limit), 50)))})
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json", "User-Agent": "brainbox-portal-mcp-catalog/1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return [], "%s: %s" % (type(e).__name__, str(e)[:200])
    if not isinstance(data, dict):
        return [], "unerwartete Antwort der Registry"
    out = []
    for item in (data.get("servers") or [])[:limit]:
        try:
            entry = _remote_entry(item)
        except Exception:
            continue
        if entry.get("id"):
            out.append(entry)
    return out, None

_REMOTE_TTL_OK = 600.0
_REMOTE_TTL_ERR = 30.0
_REMOTE_CACHE_MAX = 200
_REMOTE_CACHE = {}
_REMOTE_CACHE_LK = threading.Lock()

import portal_zustand as _zst
_zst.register("portal_mcp_registry._REMOTE_CACHE", "cache", __name__, ref=_REMOTE_CACHE, ttl_s=600.0,
              beschreibung="Remote-MCP-Suche je normalisierter Query (Deckel 200); Fehler halten nur 30 s und bleiben als remote_error sichtbar",
              neustart="verfaellt", schreiber="_remote_search_cached()")

def _remote_search_cached(q):
    key = str(q or "").strip().lower()
    now = time.time()
    with _REMOTE_CACHE_LK:
        ent = _REMOTE_CACHE.get(key)
        if ent:
            ts, remote, err = ent
            if now - ts < (_REMOTE_TTL_ERR if err else _REMOTE_TTL_OK):
                return list(remote), err
    remote, err = remote_search(q)
    with _REMOTE_CACHE_LK:
        if len(_REMOTE_CACHE) >= _REMOTE_CACHE_MAX:
            for k, _ in sorted(_REMOTE_CACHE.items(), key=lambda kv: kv[1][0])[:_REMOTE_CACHE_MAX // 2]:
                _REMOTE_CACHE.pop(k, None)
        _REMOTE_CACHE[key] = (now, list(remote), err)
    return remote, err

def search(q=""):

    q = str(q or "").strip()
    res = {"ok": True, "q": q, "catalog": catalog_search(q)}
    remote, err = _remote_search_cached(q)
    res["remote"] = remote
    if err:
        res["remote_error"] = err
        res["source"] = "catalog_only"
    else:
        res["source"] = "catalog+remote" if q else "catalog_only"
    return res

def add_from_catalog(uid, catalog_id):

    e = catalog_get(catalog_id)
    if e is None:
        return {"ok": False, "error": "unbekannter Katalog-Eintrag: %s" % str(catalog_id or "")[:80]}
    inst = e.get("install") or {}
    transport = str(inst.get("type") or "stdio")
    if transport == "stdio":
        target = " ".join([str(inst.get("command") or "")] + [str(a) for a in (inst.get("args") or [])]).strip()
    else:
        target = str(inst.get("url") or "")
    return add(uid, e["name"], transport, target,
               extra={"catalog_id": e["id"], "homepage": e.get("homepage"),
                      "description": e.get("description")})
