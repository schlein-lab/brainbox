

import os

import portal_pipeline as _eng

try:
    import portal_widget_addons as _wa
    _ADDON_DIR = os.path.join(_wa.ADDON_DIR, "pipeline")
except Exception:
    _ADDON_DIR = os.path.expanduser("~/.local/share/brainbox-portal/widget-addons/pipeline")

def configure(**kw):

    _eng.RUN_GATE = lambda: os.path.isdir(_ADDON_DIR)
    _eng.pipeline_worker_start()

def handle(verb, method, body, query, ctx):
    if not (ctx.get("is_admin") or str(ctx.get("principal") or "") == "owner"):
        return {"ok": False, "error": "nur Owner/Admin"}
    body = body if isinstance(body, dict) else {}
    query = query if isinstance(query, dict) else {}
    if verb == "status":
        return _eng.status()
    if verb == "enable":
        return _eng.set_enabled(True)
    if verb == "disable":
        return _eng.set_enabled(False)
    if verb == "lane":
        name = str(body.get("name") or query.get("name") or "")
        act = str(body.get("action") or query.get("action") or "")
        if act == "pause":
            return _eng.set_lane_paused(name, True)
        if act == "resume":
            return _eng.set_lane_paused(name, False)
        if act == "run":
            return _eng.run_now(name)
        return {"ok": False, "error": "action muss pause|resume|run sein"}
    return {"ok": False, "error": "unbekanntes Verb: %s" % verb}
