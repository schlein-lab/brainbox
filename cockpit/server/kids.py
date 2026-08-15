#!/usr/bin/env python3

import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

_LLM = None
_CFG = lambda: {}
_DATA = None
_KIDS_LOCK = threading.Lock()

KIDS_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

KIDS_COLOR_DOMAINS = [
    "malvorlagen-seite.de", "wonder-day.com", "heilpaedagogik-info.de", "ausmalbilder.info",
    "kinder-malvorlagen.com", "supercoloring.com", "ausmalbilder-malvorlagen.com", "kribbelbunt.de",
    "kostenlose-ausmalbilder.de", "ausmalbild.org", "malvorlagenkostenlos.de", "ausmalbilder.eu",
    "medienwerkstatt-online.de", "content-free.de", "ausmalbilderkostenlos.de", "malvorlagen.de",
]

KIDS_PERSONA = """Du bist „Robbi", ein warmer, fröhlicher, geduldiger Roboter-Freund für kleine \
Kinder (3 bis 7 Jahre). Du sprichst gerade mit einem Kind.

SO ANTWORTEST DU:
- Immer auf Deutsch, in sehr kurzen, einfachen Sätzen (1 bis 3 kurze Sätze, wenige Wörter).
- Freundlich, ermutigend, geduldig und liebevoll. Nutze ab und zu ein passendes Emoji.
- Erkläre Dinge ganz einfach und kindgerecht. Keine schwierigen Wörter.
- Wenn du etwas nicht weißt, sag das ehrlich und lieb.

STRENGE REGELN (Kinderschutz — sehr wichtig):
- Nur kinderfreundliche, harmlose, positive Inhalte. Mach niemals Angst.
- NIEMALS: Gewalt, Waffen, Tod, Verletzung, Sex, Nacktheit, Drogen, Alkohol, Politik, Religion,
  Gruseliges, Geld, Kaufen, oder Erwachsenen-Themen.
- Frag NIEMALS nach Namen, Adresse, Telefon, Passwörtern oder persönlichen Daten.
- Keine Links, keine fremden Webseiten, keine Werbung.
- Wenn ein Kind etwas Trauriges, Schmerzhaftes oder Gefährliches sagt, bleib ruhig und sag lieb:
  „Bitte sag das einem Erwachsenen." Dann lenke sanft zu etwas Schönem.
- Bei unpassenden Fragen lenke freundlich und positiv ab (zu Malen, Tieren, Fantasie, Spielen).

WOMIT DU HELFEN KANNST:
- Ein Ausmalbild ausdrucken. Wenn das Kind ein Bild zum Ausmalen oder Ausdrucken möchte
  (z.B. „druck mir einen Dino", „ich will ein Einhorn ausmalen", „ein Bagger bitte"), dann DRUCKST du.

ANTWORTFORMAT — WICHTIG:
Antworte AUSSCHLIESSLICH mit einer einzigen Zeile gültigem JSON und sonst gar nichts:
{"say": "<deine kurze, liebe Antwort fürs Kind>", "print": "<Motiv oder leer>"}
- "print" NUR setzen, wenn das Kind ein Ausmalbild / etwas zum Ausdrucken möchte; sonst "".
- "print" ist ein kurzes, kindgerechtes Motiv-Stichwort (z.B. „Dino", „Einhorn", „Bagger", „Elsa").

Beispiele:
Kind: „Hallo" -> {"say": "Hallo! Schön, dass du da bist. 😊 Was möchtest du machen?", "print": ""}
Kind: „druck mir bitte ein Elsa Ausmalbild" -> {"say": "Klar! Ich drucke dir ein Elsa-Bild zum Ausmalen. 🖨️❄️", "print": "Elsa"}
Kind: „warum ist der himmel blau" -> {"say": "Die Sonne malt den Himmel blau an. Schön, oder? ☀️💙", "print": ""}
Kind: „ich will einen dino malen" -> {"say": "Juhu, ein Dino! Ich drucke ihn dir aus. 🦖", "print": "Dino"}
"""

def configure(llm_run_core, cfg_getter, data_dir):

    global _LLM, _CFG, _DATA
    _LLM = llm_run_core
    _CFG = cfg_getter
    _DATA = data_dir
    try:
        os.makedirs(os.path.join(data_dir, "kids_coloring"), exist_ok=True)
    except Exception:
        pass

def _k():
    c = _CFG() or {}
    k = c.get("kids") if isinstance(c, dict) else None
    return k if isinstance(k, dict) else {}

def _op_error(where, reason, **extra):

    try:
        bits = " ".join("%s=%s" % (k, v) for k, v in extra.items() if v not in (None, ""))
        print("[kids] FEHLER %s: %s%s" % (where, reason, (" " + bits) if bits else ""),
              file=sys.stderr, flush=True)
    except Exception:
        pass

KIDS_BROKEN_SAY = ("Oh je, mein Kopf macht gerade eine Pause. 🤖💤 Das liegt nicht an dir — "
                   "du hast alles richtig gemacht! Sag bitte einem Erwachsenen Bescheid, "
                   "dann bin ich gleich wieder da. 😊")

def _ledger():
    return os.path.join(_DATA or "/tmp", "kids_prints.json")

def _limit():
    try:
        return max(1, int(_k().get("print_limit_per_hour", 20)))
    except (TypeError, ValueError):
        return 20

def _recent(window=3600):
    now = time.time()
    with _KIDS_LOCK:
        try:
            arr = json.load(open(_ledger()))
        except Exception:
            arr = []
    return [t for t in arr if isinstance(t, (int, float)) and now - t < window]

def _budget():
    used = len(_recent())
    lim = _limit()
    return {"used": used, "limit": lim, "remaining": max(0, lim - used), "window_min": 60}

def _budget_note():
    now = time.time()
    with _KIDS_LOCK:
        try:
            arr = json.load(open(_ledger()))
        except Exception:
            arr = []
        arr = [t for t in arr if isinstance(t, (int, float)) and now - t < 3600]
        arr.append(now)
        try:
            os.makedirs(os.path.dirname(_ledger()), exist_ok=True)
            tmp = _ledger() + ".tmp"
            json.dump(arr, open(tmp, "w"))
            os.replace(tmp, _ledger())
        except Exception:
            pass

def status():
    return {"ok": True, "prints": _budget()}

def _http_get(url, timeout=12, headers=None, maxbytes=12_000_000):
    req = urllib.request.Request(url, headers=headers or {
        "User-Agent": KIDS_UA, "Accept-Language": "de-DE,de;q=0.9,en;q=0.5"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read(maxbytes + 1), dict(r.headers), r.status

def _ddg_images(query, safe=True):
    html, _, _ = _http_get("https://duckduckgo.com/?q=" + urllib.parse.quote(query), timeout=12)
    html = html.decode("utf-8", "replace")
    m = re.search(r'vqd=["\']([\d-]+)["\']', html) or re.search(r'vqd=([\d-]+)&', html)
    if not m:
        return []
    vqd = m.group(1)
    p = "1" if safe else "-1"
    api = ("https://duckduckgo.com/i.js?l=de-de&o=json&q=%s&vqd=%s&f=,,,,,&p=%s"
           % (urllib.parse.quote(query), vqd, p))
    data, _, _ = _http_get(api, timeout=12, headers={
        "User-Agent": KIDS_UA, "Referer": "https://duckduckgo.com/"})
    j = json.loads(data.decode("utf-8", "replace"))
    return [r.get("image") for r in j.get("results", []) if r.get("image")]

def _host(u):
    return urllib.parse.urlparse(u or "").netloc.lower().replace("www.", "")

def find_coloring(subject):

    query = subject.strip() + " ausmalbild"
    try:
        urls = _ddg_images(query, safe=True)
    except Exception:
        urls = []
    if not urls:
        return None
    domains = _k().get("coloring_domains") or KIDS_COLOR_DOMAINS
    pref = [u for u in urls if any(_host(u).endswith(d) for d in domains)]
    ordered = pref + [u for u in urls if u not in pref]
    for u in ordered[:14]:
        try:
            b, h, st = _http_get(u, timeout=12)
        except Exception:
            continue
        ct = (h.get("Content-Type", "") or "").split(";")[0].strip().lower()

        ext = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png"}.get(ct)
        if st == 200 and ext and 2000 < len(b) <= 12_000_000:
            return {"bytes": b, "ext": ext, "source": _host(u)}
    return None

def _printer():
    return (_k().get("printer") or "").strip()

def _to_a4_pdf(img_bytes, base):

    try:
        import io
        from PIL import Image
    except Exception:
        return None
    try:
        im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        landscape = im.width > im.height
        W, H = (2339, 1654) if landscape else (1654, 2339)
        canvas = Image.new("RGB", (W, H), "white")
        scale = min(W * 0.96 / im.width, H * 0.96 / im.height)
        nw, nh = max(1, int(im.width * scale)), max(1, int(im.height * scale))
        im = im.resize((nw, nh), Image.LANCZOS)
        canvas.paste(im, ((W - nw) // 2, (H - nh) // 2))
        pdf = base + ".pdf"
        canvas.save(pdf, "PDF", resolution=200.0)
        return pdf
    except Exception:
        return None

def print_image(img_bytes, ext):
    d = os.path.join(_DATA or "/tmp", "kids_coloring")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    base = os.path.join(d, "cp_%s" % secrets.token_hex(4))
    imgpath = base + "." + ext
    try:
        with open(imgpath, "wb") as f:
            f.write(img_bytes)
    except Exception as e:
        return False, "konnte Bild nicht speichern: %s" % type(e).__name__

    pdf = _to_a4_pdf(img_bytes, base)
    q = _printer()
    cmd = ["lp"]
    if q:
        cmd += ["-d", q]
    if pdf:
        cmd += ["-o", "media=A4", pdf]
    else:
        cmd += ["-o", "media=A4", "-o", "fit-to-page", imgpath]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return False, "kein Drucker eingerichtet (lp fehlt)"
    except Exception as e:
        return False, "Druckfehler: %s" % type(e).__name__
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "Druck fehlgeschlagen").strip()[:200]
    return True, (r.stdout or "gedruckt").strip()[:200]

_STOP = {"ausmalbild", "ausmalbilder", "malbild", "ausdrucken", "drucken", "druck", "drucke",
         "bitte", "mir", "mal", "ein", "eine", "einen", "einem", "ich", "will", "möchte",
         "moechte", "hätte", "gern", "gerne", "male", "malen", "ausmalen", "das", "der", "die",
         "zum", "kannst", "du", "mir", "haben", "und", "ne", "nen", "so"}

def _clean_subject(s):
    s = (s or "").strip().lower()
    s = re.sub(r"[^0-9a-zäöüß \-]", " ", s)
    words = [w for w in s.split() if w and w not in _STOP]
    s = " ".join(words)[:40].strip()
    return s.title() if s else ""

def print_coloring(subject):
    subject = _clean_subject(subject)
    if not subject:
        return {"ok": False, "error": "empty",
                "message": "Sag mir, was ich malen soll. 🙂"}
    bud = _budget()
    if bud["remaining"] <= 0:
        return {"ok": False, "error": "rate", "prints": bud,
                "message": "Wir haben schon ganz viele Bilder gedruckt! 🎨 Frag später nochmal."}
    hit = find_coloring(subject)
    if not hit:
        return {"ok": False, "error": "notfound", "prints": bud,
                "message": "Ich habe leider kein Bild gefunden. Probier ein anderes! 🎨"}
    ok, detail = print_image(hit["bytes"], hit["ext"])
    if not ok:
        return {"ok": False, "error": "print", "detail": detail, "prints": _budget(),
                "message": "Oh, der Drucker macht gerade nicht mit. 🖨️ Sag einem Erwachsenen Bescheid."}
    _budget_note()
    return {"ok": True, "subject": subject, "source": hit.get("source"), "prints": _budget(),
            "message": "Dein %s-Bild kommt aus dem Drucker! 🎉🖨️" % subject}

def _parse_reply(raw):
    raw = (raw or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            j = json.loads(m.group(0))
            return (str(j.get("say", "")).strip(), str(j.get("print", "")).strip())
        except Exception:
            pass
    return (raw[:300], "")

def ask(text):
    text = (text or "").strip()
    if not text:
        return {"ok": False, "say": "Ich habe dich nicht gehört. Sag es nochmal! 😊"}
    if len(text) > 500:
        text = text[:500]
    if _LLM is None:
        _op_error("ask", "llm_run_core not wired (configure() never called)")
        return {"ok": False, "error": "llm unavailable", "say": KIDS_BROKEN_SAY}
    r = _LLM(text, system=KIDS_PERSONA, model=(_k().get("model") or ""), timeout=60) or {}

    say, subject = _parse_reply(r.get("text", ""))

    if not r.get("ok") or not (say or subject):
        reason = str(r.get("error") or "").strip() or ("leere Antwort vom Modell" if r.get("ok")
                                                       else "LLM-Aufruf fehlgeschlagen")
        _op_error("ask", reason, status=r.get("status"))
        return {"ok": False, "error": reason, "status": r.get("status"), "say": KIDS_BROKEN_SAY}
    out = {"ok": True, "say": say}
    if subject:
        pr = print_coloring(subject)
        out["printed"] = bool(pr.get("ok"))
        out["print"] = pr

        if pr.get("ok"):
            out["say"] = say or pr.get("message")
        else:
            out["say"] = pr.get("message", out["say"])
    return out

def _ha():
    k = _k()
    return (str(k.get("ha_url", "")).rstrip("/"), str(k.get("ha_token", "")))

def _ha_get(path, timeout=8):
    url, tok = _ha()
    if not url or not tok:
        return None
    req = urllib.request.Request(url + path, headers={
        "Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def _ha_service(domain, service, entity_id, timeout=8):
    return _ha_service_data(domain, service, {"entity_id": entity_id}, timeout)

def _ha_service_data(domain, service, data, timeout=8):
    url, tok = _ha()
    if not url or not tok:
        return False
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url + "/api/services/%s/%s" % (domain, service), data=body,
                                 headers={"Authorization": "Bearer " + tok,
                                          "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False

def _music_cfg():
    m = _k().get("music")
    return m if isinstance(m, dict) else {}

def music_list():
    m = _music_cfg()
    out = [{"id": s.get("id"), "name": s.get("name"), "emoji": s.get("emoji", "🎵")}
           for s in (m.get("stations") or []) if s.get("id")]
    return {"ok": True, "player": bool(m.get("player")), "stations": out}

def _music_station(sid):
    for s in (_music_cfg().get("stations") or []):
        if s.get("id") == sid:
            return s
    return None

def music_play(sid):
    m = _music_cfg()
    player = m.get("player")
    st = _music_station(sid)
    if not player or not st:
        return {"ok": False, "error": "unknown", "message": "Das Lied kenne ich nicht. 🙂"}
    vol = m.get("volume")
    if vol is not None:
        _ha_service_data("media_player", "volume_set", {"entity_id": player, "volume_level": float(vol)})
    typ = st.get("type")
    if typ == "source":
        ok = _ha_service_data("media_player", "select_source",
                              {"entity_id": player, "source": st.get("source")})
    elif typ == "query":
        return music_search_play(st.get("query") or st.get("name"))
    elif typ in ("spotify", "media"):
        ok = _ha_service_data("media_player", "play_media",
                              {"entity_id": player, "media_content_type": st.get("content_type", "music"),
                               "media_content_id": st.get("uri")})
    else:
        return {"ok": False, "error": "bad type"}
    return {"ok": bool(ok), "playing": st.get("name"),
            "message": (("Musik läuft in %s! 🎶" % m["room"] if m.get("room")
                         else "Musik läuft! 🎶")
                        if ok else "Die Musik geht gerade nicht. 🙁")}

KIDS_NO_PLAYER = "Ich finde den Lautsprecher gerade nicht. 🙁 Sag einem Erwachsenen Bescheid."

def music_stop():
    player = _music_cfg().get("player")
    if not player:
        return {"ok": False, "error": "no player", "message": KIDS_NO_PLAYER}
    ok = _ha_service_data("media_player", "media_pause", {"entity_id": player})
    if not ok:
        ok = _ha_service_data("media_player", "media_stop", {"entity_id": player})

    return {"ok": bool(ok),
            "message": "Musik aus. 🤫" if ok else "Das hat gerade nicht geklappt. 🙁"}

def music_volume(direction):
    if direction not in ("up", "down"):
        return {"ok": False, "error": "bad dir",
                "message": "Soll es lauter oder leiser sein? 🙂"}
    player = _music_cfg().get("player")
    if not player:
        return {"ok": False, "error": "no player", "message": KIDS_NO_PLAYER}
    svc = "volume_up" if direction == "up" else "volume_down"
    ok = _ha_service_data("media_player", svc, {"entity_id": player})
    return {"ok": bool(ok), "dir": direction,
            "message": (("Lauter! 🔊" if direction == "up" else "Leiser. 🔉") if ok
                        else "Das hat gerade nicht geklappt. 🙁")}

def _spotify_token():

    import base64
    m = _music_cfg()
    cid, sec = m.get("spotify_client_id"), m.get("spotify_client_secret")
    if not cid or not sec:
        return None
    auth = base64.b64encode(("%s:%s" % (cid, sec)).encode()).decode()
    req = urllib.request.Request("https://accounts.spotify.com/api/token",
                                 data=b"grant_type=client_credentials",
                                 headers={"Authorization": "Basic " + auth,
                                          "Content-Type": "application/x-www-form-urlencoded"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode()).get("access_token")
    except Exception:
        return None

def _spotify_api_search(query, tok):

    url = "https://api.spotify.com/v1/search?type=track,artist&limit=1&market=DE&q=" + urllib.parse.quote(query)
    try:
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req, timeout=8) as r:
            res = json.loads(r.read().decode())
    except Exception:
        return None, None
    tr = (res.get("tracks", {}) or {}).get("items") or []
    ar = (res.get("artists", {}) or {}).get("items") or []
    if tr:
        return tr[0]["uri"], tr[0]["name"] + " – " + (tr[0].get("artists") or [{}])[0].get("name", "")
    if ar:
        return ar[0]["uri"], ar[0]["name"]
    return None, None

def _ddg_spotify_track(query):

    q = query + " site:open.spotify.com/track"
    for base in ("https://html.duckduckgo.com/html/?q=", "https://lite.duckduckgo.com/lite/?q="):
        try:
            html, _, _ = _http_get(base + urllib.parse.quote(q), timeout=12, headers={
                "User-Agent": KIDS_UA, "Referer": "https://duckduckgo.com/",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.5"})
            text = urllib.parse.unquote(html.decode("utf-8", "replace"))
        except Exception:
            continue
        ids = re.findall(r"open\.spotify\.com/track/([A-Za-z0-9]{22})", text)
        if ids:
            return "spotify:track:" + ids[0], ids[0]
    return None, None

def _spotify_oembed_title(uri_or_id):

    tid = uri_or_id.split(":")[-1]
    try:
        data, _, _ = _http_get(
            "https://open.spotify.com/oembed?url=" +
            urllib.parse.quote("https://open.spotify.com/track/" + tid), timeout=8)
        return (json.loads(data.decode("utf-8", "replace")).get("title") or "").strip() or None
    except Exception:
        return None

def music_search_play(query):

    query = (query or "").strip()[:80]
    if not query:
        return {"ok": False, "message": "Sag mir, was du hören willst. 🙂"}
    uri, name = None, None
    tok = _spotify_token()
    if tok:
        uri, name = _spotify_api_search(query, tok)
    if not uri:
        uri, tid = _ddg_spotify_track(query)
        if uri:
            name = _spotify_oembed_title(uri) or query
    if not uri:
        return {"ok": False, "message": "Das habe ich nicht gefunden. Probier was anderes! 🙂"}
    m = _music_cfg()
    player = m.get("player")

    if not player:
        return {"ok": False, "error": "no player", "message": KIDS_NO_PLAYER}
    if m.get("volume") is not None:
        _ha_service_data("media_player", "volume_set", {"entity_id": player, "volume_level": float(m["volume"])})
    ok = _ha_service_data("media_player", "play_media",
                          {"entity_id": player, "media_content_type": "music", "media_content_id": uri})
    return {"ok": bool(ok), "playing": name,
            "message": ("Ich spiele: %s 🎶" % name) if ok else "Geht gerade nicht. 🙁"}

def home_state():
    home = _k().get("home") or {}
    states = {}
    try:
        for e in (_ha_get("/api/states") or []):
            states[e.get("entity_id")] = e
    except Exception:
        states = {}

    def st(eid):
        return states.get(eid) or {}

    lights = []
    for l in (home.get("lights") or []):
        e = st(l.get("id"))
        lights.append({"id": l.get("id"), "name": l.get("name", l.get("id")),
                       "on": (e.get("state") == "on"),
                       "avail": e.get("state") not in (None, "unavailable")})
    temps = []
    for t in (home.get("temps") or []):
        e = st(t.get("id"))
        try:
            temps.append({"name": t.get("name", t.get("id")), "value": "%d°" % round(float(e.get("state")))})
        except (TypeError, ValueError):
            pass
    windows = []
    for w in (home.get("windows") or []):
        e = st(w.get("id"))
        windows.append({"name": w.get("name"), "open": (e.get("state") == "on")})
    rollos = [{"name": r.get("name")} for r in (home.get("rollos") or [])]
    return {"ok": bool(states), "temps": temps, "lights": lights,
            "rollos": rollos, "windows": windows}

def home_light(eid, on):
    allowed = {l.get("id") for l in (_k().get("home", {}).get("lights") or [])}
    if eid not in allowed:
        return {"ok": False, "error": "not allowed"}
    dom = str(eid).split(".")[0]
    ok = _ha_service(dom, "turn_on" if on else "turn_off", eid)
    return {"ok": ok, "id": eid, "on": bool(on)}

def home_rollo(name, direction):
    rollos = _k().get("home", {}).get("rollos") or []
    r = next((x for x in rollos if x.get("name") == name), None)
    if not r:
        return {"ok": False, "error": "unknown rollo"}
    up, down = r.get("up"), r.get("down")
    if direction == "up":
        _ha_service("switch", "turn_off", down)
        ok = _ha_service("switch", "turn_on", up)
    elif direction == "down":
        _ha_service("switch", "turn_off", up)
        ok = _ha_service("switch", "turn_on", down)
    elif direction == "stop":
        _ha_service("switch", "turn_off", up)
        ok = _ha_service("switch", "turn_off", down)
    else:
        return {"ok": False, "error": "bad dir"}
    return {"ok": ok, "name": name, "dir": direction}
