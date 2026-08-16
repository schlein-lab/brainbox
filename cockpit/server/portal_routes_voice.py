
import os, json, base64, hashlib, socket, secrets, subprocess, time
import re, threading, shlex, contextlib
import urllib.parse, urllib.request

_OAUTH_PANE_W = 1000

CLAUDE_BIN = None
DEFAULT_PRINCIPAL = None
_CLIENT_VERBS = None
_CLIENT_VERB_ALIAS = None
_CerEntry = None
_DISPLAY_REG = None
_LLMPOOL = None
_OAUTH_LOCK = None
_OAUTH_SESS = None
_OAUTH_URL_RE = None
_agent_ctx = None
_agent_norm_url = None
_agent_schedule_dispatch = None
_cer_target = None
_ceremony = None
_fabric = None
_hpc_ssh = None
_hpc_submit = None
_hpc_fetch = None
_hpc_ctl = None
_hpc_slurmwatch = None
_inject = None
_kiosk_post = None
_llmpool_mod = None
_meta_apply_policy = None
_meta_counts = None
_meta_load = None
_meta_new_tid = None
_meta_update = None
_nabu_late_on = None
_nabu_reengage = None
_oauth_cell_uid = None
_oauth_logged_in = None
_oauth_pane = None
_oauth_sess_name = None
_pane = None
_prov_log = None
_screen_open = None
_session_store = None
_sessprov_set = None
_tmux = None
_traceback_log = None
_uid_safe = None
_uservpn_allowed = None
_vdisp = None
_vext = None
_vext_ctx = None
_vext_tts = None
_voice_cellmgr = None
_voice_prewarm_mode = None
_voice_prewarm_set = None
_voice_rotate_and_prewarm = None
_voice_route_load = None
_voice_route_options = None
_voice_route_set = None
_voice_sess_name = None
_voice_session_for = None
_voice_turn_seq_bump = None
job_create = None
job_link = None
links_add = None
llm_run_core = None
pending_actions_drain = None
pending_actions_push = None
pn_req = None
portal_agent = None
seat_low_stakes = None
voice_first = None

def _models_registry():

    try:
        import portal_session_svc as _svc
        return _svc.sess_models()
    except Exception:
        return []

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

_LLMD_SNAP_TTL = 3.0
_LLMD_SNAP_CACHE = {}
_PND_STATUS_TTL = 3.0
_PND_STATUS_CACHE = []

import pn_sprachausgabe as _sprach
import portal_zustand as _zst
_zst.register("portal_routes_voice._LLMD_SNAP_CACHE", "cache", __name__, ref=_LLMD_SNAP_CACHE, ttl_s=3.0,
              beschreibung="pn-llmd-Admission-Snapshots je Verb fuer /api/queue; NUR ok-Antworten werden gecacht, Fehler gehen immer frisch und ungeschminkt raus",
              neustart="verfaellt", schreiber="Queue-Leser")
_zst.register("portal_routes_voice._PND_STATUS_CACHE", "cache", __name__, ref=_PND_STATUS_CACHE, ttl_s=3.0,
              beschreibung="pnd-status-Snap fuer /api/queue (Ein-Feld-Zelle); NUR ok-Antworten werden gecacht",
              neustart="verfaellt", schreiber="Queue-Leser")

try:
    from portal_terminal import HOSTSHELL_GONE_REASON as _HOSTSHELL_GONE
except Exception:
    _HOSTSHELL_GONE = ("Host-Shell entfernt — Arbeit läuft in Session-Zellen, "
                       "Box-Verwaltung per SSH.")

_HOSTSHELL_GONE_SPOKEN = (
    _HOSTSHELL_GONE +
    " Öffne die Sitzung im Reiter „Sessions“ — dort hat jede Sitzung ihr eigenes Terminal in der Zelle.")

_SSE_CT = [("Content-Type", "text/event-stream; charset=utf-8"), ("Cache-Control", "no-cache")]

def _sse_ereignis(name, daten):

    return "event: %s\ndata: %s\n\n" % (name, json.dumps(daten, ensure_ascii=False))

def _anthropic_strom(mid, model, text, pt, ct):

    return (
        _sse_ereignis("message_start", {
            "type": "message_start",
            "message": {"id": mid, "type": "message", "role": "assistant", "model": model,
                        "content": [], "stop_reason": None, "stop_sequence": None,
                        "usage": {"input_tokens": pt, "output_tokens": 0}}})
        + _sse_ereignis("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""}})
        + _sse_ereignis("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": text}})
        + _sse_ereignis("content_block_stop", {"type": "content_block_stop", "index": 0})
        + _sse_ereignis("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": ct}})
        + _sse_ereignis("message_stop", {"type": "message_stop"}))

class VoiceRoutes:
    def _voice_turn(self, text, channel=""):

        text = (text or "").strip()
        if not text:
            return self.send_html(json.dumps({"action": "say", "speak": ""}), 200, [("Content-Type", "application/json")])
        t = text.lower()

        hostshell_ask = any(k in t for k in ("terminal", "konsole", "shell"))
        lens = None
        if not hostshell_ask:
            for name, keys in (("browser", ("browser", "internet", "webseite", "seite", "web")),
                               ("notes", ("notiz", "notes", "note")),
                               ("screen", ("screen", "bildschirm", "gestreamt")),
                               ("queue", ("queue", "warteschlange", "auftrag", "aufgabe")),
                               ("attach", ("anhang", "anhäng", "attach"))):
                if any(k in t for k in keys):
                    lens = name
                    break
        verb, ceremony = "conversation.say", "read"
        if _vdisp is not None:
            try:
                it = _vdisp.classify(text)
                verb, ceremony = it.verb, it.ceremony.value
            except Exception:
                pass
        _prov_log(verb, self._principal(), text, {"ceremony": ceremony, "lens": lens})
        if ceremony == "irreversible":
            return self._begin_ceremony(verb, text)
        show = bool(re.search(r"\b(zeig|zeige|öffne|oeffne|oeffnen|geh\s+zu|wechsel|open|show|switch)\b", t)) \
            or verb in ("terminal.read", "screen.show", "app.sense", "app.open")
        if hostshell_ask and (show or str(verb).startswith("terminal.")):

            _prov_log("hostshell.refused", self._principal(), text,
                      {"ceremony": ceremony, "asked_verb": verb, "channel": channel or "web"})
            return self.send_html(json.dumps({"action": "say", "verb": "hostshell.refused",
                                              "speak": _HOSTSHELL_GONE_SPOKEN}),
                                  200, [("Content-Type", "application/json")])
        if lens and show and channel != "nabu":
            labels = {"browser": "Browser", "notes": "Notes",
                      "screen": "Screen", "queue": "Queue", "attach": "Anhänge"}
            out = {"action": "summon", "lens": lens, "verb": verb, "speak": "Zeige " + labels.get(lens, lens) + "."}
            return self.send_html(json.dumps(out), 200, [("Content-Type", "application/json")])

        _DRIVE = ("app.click", "app.scroll", "app.type", "app.enter", "app.press", "app.sense")
        if verb in _DRIVE:
            params = {}
            if verb == "app.type":
                mt = re.search(r"(?:tippe|schreibe|gib|type)\s+(.+?)(?:\s+ein)?\s*$", text, re.I)
                params["text"] = (mt.group(1).strip() if mt else "")
                if not params["text"]:
                    return self.send_html(json.dumps({"action": "say", "verb": verb,
                        "speak": "Was soll ich tippen?"}), 200, [("Content-Type", "application/json")])
            if verb == "app.scroll":
                params["n"] = 1 if re.search(r"\b(hoch|rauf|up)\b", t) else -1
            res = seat_low_stakes("app.enter" if verb == "app.press" else verb, params, self._principal())
            _prov_log(verb, self._principal(), text, {"phase": "forge", "ok": res.get("ok")})
            return self.send_html(json.dumps({"action": "say", "verb": verb, "speak": res["speech"]}),
                                  200, [("Content-Type", "application/json")])
        uid = self._principal()

        _nabu_seq = _voice_turn_seq_bump(uid) if (channel == "nabu" and _nabu_late_on()) else None
        first = voice_first(text, uid, channel=channel or "web")
        out = {"action": "say", "verb": verb, "speak": first.get("text", ""),
               "cursor": first.get("off", 0), "busy": bool(first.get("busy"))}
        if _nabu_seq is not None:

            _nabu_reengage(uid, int(first.get("off", 0)), _nabu_seq)
        acts = pending_actions_drain(uid)
        if acts:
            out["actions"] = acts
        return self.send_html(json.dumps(out), 200, [("Content-Type", "application/json")])

    def _agent_state(self):

        if portal_agent is None:
            return self._cer_json({"error": "portal_agent unavailable"})
        try:
            st = portal_agent.build_state(_agent_ctx(), self._principal())
        except Exception as e:
            st = {"uid": self._principal(), "error": str(e)}
        return self._cer_json(st)

    def _agent_exec(self, body):

        try:
            req = json.loads(body or "{}")
        except Exception:
            req = {}
        verb = str(req.get("verb") or "").strip()
        _kan = getattr(portal_agent, "kanonisch", None) if portal_agent else None
        if _kan:
            verb = _kan(verb)
        args = req.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        uid = self._principal()
        if verb in ("schedule", "schedule_list", "schedule_cancel"):
            _do = args.get("do") if isinstance(args.get("do"), dict) else None
            if _do and str(_do.get("verb") or "") == "session_wake":

                _sid = re.sub(r"[^A-Za-z0-9_-]", "", (self.headers.get("X-Pn-Session-Sid", "") or ""))[:40]
                if not _sid:
                    return self._cer_json({"ok": False, "error": "no_session",
                                           "spoken": "Weckrufe kann nur eine Session fuer sich selbst planen."})
                if not isinstance(_do.get("args"), dict):
                    _do["args"] = {}
                _do["args"]["_sid"] = _sid
            return self._cer_json(_agent_schedule_dispatch(verb, args, uid))
        if verb in ("session_spawn", "session_status", "session_tell",
                    "session_broadcast", "session_stop",
                    "session_resize", "session_restart",
                    "session_transcript", "session_watch"):
            return self._cer_json(self._agent_orchestrate(verb, args, uid))
        if verb in ("store_status", "store_onboard"):
            return self._cer_json(self._agent_store(verb, args, uid))
        if verb in ("kits_status", "kits_add", "kits_remove"):
            return self._agent_kits(verb, args, uid)
        if verb in ("ask_owner", "ask_owner_result"):
            return self._cer_json(self._agent_ask_owner(verb, args, uid))

        if verb in getattr(portal_agent, "RETIRED_TOOLS", ("terminal_run", "terminal_read")):
            result, spoken, _actions = self._agent_run(verb, args, uid)
            return self._cer_json({"ok": False, "result": result, "spoken": spoken})
        tool = portal_agent.TOOL_BY_NAME.get(verb) if portal_agent else None
        if not tool:
            return self._cer_json({"ok": False, "error": "unknown verb %r" % verb,
                                   "spoken": "Diesen Befehl kenne ich nicht."})
        if tool.get("ceremony_class") == "irreversible":
            return self._agent_irreversible(verb, args, uid)
        try:
            result, spoken, actions = self._agent_run(verb, args, uid)
            _prov_log("agent." + verb, uid, json.dumps(args, sort_keys=True)[:400], {"wire": "agent"})
            out = {"ok": bool(result.get("ok", True)) if isinstance(result, dict) else True,
                   "result": result, "spoken": spoken}
            if actions:
                out["actions"] = actions
            return self._cer_json(out)
        except Exception as e:
            return self._cer_json({"ok": False, "error": str(e), "spoken": "Das hat nicht geklappt."})

    def _agent_orchestrate(self, verb, args, uid):

        sid = re.sub(r"[^A-Za-z0-9_-]", "", (self.headers.get("X-Pn-Session-Sid", "") or ""))[:40]
        try:
            import portal_metasessions as _pm
        except Exception as e:
            return {"ok": False, "error": str(e), "spoken": "Sub-Sessions sind gerade nicht verfuegbar."}
        if verb == "session_spawn":
            res, spoken, ok = _pm.orch_spawn(uid, sid, args.get("task"), args.get("title"),
                                             args.get("model"))
        elif verb == "session_status":
            res, spoken, ok = _pm.orch_status(uid, sid)
        elif verb == "session_broadcast":
            res, spoken, ok = _pm.orch_broadcast(uid, sid, args.get("text"))
        elif verb == "session_transcript":
            res, spoken, ok = _pm.orch_transcript(uid, sid, args.get("tid"),
                                                  args.get("ab"), args.get("kb"))
        elif verb == "session_watch":
            res, spoken, ok = _pm.orch_watch(uid, sid, args.get("modus") or args.get("mode"))
        elif verb == "session_resize":
            res, spoken, ok = _pm.orch_resize(uid, sid, args.get("tid"), args.get("disk_gb"),
                                              args.get("mem_mb"), args.get("reason"),
                                              args.get("approval"), args.get("restart"))
        elif verb == "session_restart":
            res, spoken, ok = _pm.orch_restart(uid, sid, args.get("tid"), args.get("reason"))
        elif verb == "session_stop":

            res, spoken, ok = _pm.orch_stop(uid, sid, args.get("tid"), args.get("reason"),
                                            erledigt=bool(args.get("erledigt")
                                                          or args.get("done")))
        else:
            res, spoken, ok = _pm.orch_tell(uid, sid, args.get("tid"), args.get("text"))
        if ok:
            _prov_log("agent." + verb, uid,
                      json.dumps({"sid": sid, "args": sorted(args)[:6]}, sort_keys=True)[:300],
                      {"wire": "agent"})
        return {"ok": bool(ok), "result": res, "spoken": spoken}

    def _agent_ask_owner(self, verb, args, uid):

        sid = re.sub(r"[^A-Za-z0-9_-]", "", (self.headers.get("X-Pn-Session-Sid", "") or ""))[:40]
        try:
            import portal_metafeatures as _mf
        except Exception as e:
            return {"ok": False, "error": str(e), "spoken": "Metafeatures nicht verfuegbar."}
        if not sid:
            return {"ok": False, "error": "no_session",
                    "spoken": "ask_owner geht nur aus einer Session heraus."}
        if verb == "ask_owner":
            return _mf.ask_owner(uid, sid, args.get("question"), options=args.get("options"),
                                 urgent=bool(args.get("urgent")), kind=args.get("kind"))
        return _mf.ask_owner_result(uid, sid, args.get("aid"))

    def _api_decisions_get(self, query):
        try:
            import portal_metafeatures as _mf
        except Exception as e:
            return self._vext_json({"ok": False, "error": str(e)}, 500)
        import urllib.parse as _up
        q = _up.parse_qs(query or "")

        state = (q.get("state", [""])[0] or "pending").strip().lower()
        if state == "all":
            state = None

        me = self._principal()
        cards = _mf.appr_list(me, state=state, include_kids=self._is_admin())

        can_decide = self._is_admin()
        for c in cards:
            own = (c.get("principal") == me)

            if c.get("an_owner"):
                mine_to_decide = can_decide
            else:
                mine_to_decide = can_decide or (own and not self._is_kid())
            c["decider"] = "self" if mine_to_decide else "parent"
            c["needs_2fa"] = bool(mine_to_decide and c.get("kind") == "approval")
        return self._vext_json({"ok": True, "approvals": cards})

    def _is_kid(self):

        try:
            from portal_users import user_get as _ug
            return (_ug(self._principal()) or {}).get("role") == "kid"
        except Exception:
            return False

    def _api_rights_request(self, raw):

        try:
            body = json.loads(raw or b"{}") or {}
        except Exception:
            body = {}
        text = str(body.get("text") or "").strip()
        if not text:
            return self._vext_json({"ok": False, "error": "text fehlt"}, 400)
        if len(text) > 800:
            text = text[:800]
        me = self._principal()
        try:
            import portal_metafeatures as _mf
            r = _mf.ask_owner(me, "rechte", "[Rechte-Anfrage von %s] %s" % (me, text),
                              options=["Erlauben (setze ich in der Verwaltung)",
                                       "Ablehnen"],
                              an_owner=True)
        except Exception as e:
            return self._vext_json({"ok": False, "error": str(e)[:200]}, 500)
        if not r.get("ok"):
            return self._vext_json(r, 400)

        try:
            import portal_channels as _pc2
            import portal_users as _pu2
            ziele = [u.get("uid") for u in _pu2.user_list()
                     if u.get("role") in ("owner", "admin") and u.get("status") != "deleted"]
            for z in (ziele or ["owner"]):
                _pc2.bus_append(None, z, "meldungen", "message", role="system",
                                text="Rechte-Anfrage von %s: %s (im Braucht-dich-Fenster "
                                     "entscheiden)" % (me, text[:200]),
                                notify="alert", quelle="rechte-anfrage")
        except Exception:
            pass
        return self._vext_json({"ok": True, "aid": r.get("aid")})

    def _api_decisions_dismiss(self, raw):

        try:
            import portal_metafeatures as _mf
            body = json.loads(raw or b"{}")
        except Exception as e:
            return self._vext_json({"ok": False, "error": str(e)}, 400)
        res = _mf.appr_dismiss(self._principal(), body.get("aid"), include_kids=self._is_admin())
        if not res.get("ok"):
            return self._vext_json(res, 404)
        try:
            _prov_log("approvals.dismiss", self._principal(),
                      json.dumps({"aid": str(body.get("aid"))})[:200], {"wire": "http"})
        except Exception:
            pass
        return self._vext_json(res)

    def _api_decisions_answer(self, raw):
        try:
            import portal_metafeatures as _mf
            body = json.loads(raw or b"{}")
        except Exception as e:
            return self._vext_json({"ok": False, "error": str(e)}, 400)
        aid = body.get("aid")
        rec = _mf.appr_get(self._principal(), aid, include_kids=self._is_admin())
        if not rec:
            return self._vext_json({"ok": False,
                                    "error": "Unbekannte oder schon entschiedene Anfrage."}, 404)
        decision = str(body.get("decision") or "").strip().lower() or None
        if rec.get("kind") == "approval":
            if decision not in ("approve", "deny"):
                return self._vext_json({"ok": False,
                                        "error": "decision approve|deny erforderlich."}, 400)

            ok2, reason = self._verify_winthin_totp(str(body.get("totp") or "").strip())
            if not ok2:

                r = (reason or "").lower()
                if "not armed" in r:
                    msg = ('Kein 2FA/Handy-Code eingerichtet — unter „Off-LAN Freigaben & 2FA“ '
                           'einrichten. Es wurde NICHTS entschieden, die Anfrage bleibt offen.')
                elif "locked" in r:
                    try:
                        from relaylib.registry import TWOFA_LOCKOUT_S as _lo
                    except Exception:
                        _lo = 900
                    msg = ("2FA ist nach zu vielen Fehlversuchen kurz gesperrt (bis zu %d Minuten). "
                           "Es wurde NICHTS entschieden — die Anfrage bleibt offen und ist danach "
                           "ganz normal entscheidbar." % max(1, int(_lo // 60)))
                else:
                    msg = ("2FA-Code abgelaufen oder falsch — es wurde NICHTS entschieden (weder "
                           "genehmigt noch abgelehnt). Die Anfrage bleibt offen: der Code wechselt "
                           'alle 30 Sekunden, also einen FRISCHEN vom Handy holen und erneut auf '
                           '„%s“ tippen.' % ("Ja, genehmigen" if decision == "approve"
                                             else "Nein, ablehnen"))
                return self._vext_json({"ok": False, "need_2fa": True, "undecided": True,
                                        "state": "pending", "error": msg}, 403)
        res = _mf.appr_answer(self._principal(), aid, answer=body.get("answer"),
                              decision=decision, include_kids=self._is_admin())
        try:
            _prov_log("approvals.answer", self._principal(),
                      json.dumps({"aid": aid, "decision": decision or "answer"})[:200],
                      {"wire": "http"})
        except Exception:
            pass
        return self._vext_json(res, 200 if res.get("ok") else 400)

    def _api_conversations(self):
        try:
            import portal_metafeatures as _mf
        except Exception as e:
            return self._vext_json({"ok": False, "error": str(e)}, 500)
        return self._vext_json({"ok": True, "conversations": _mf.conv_list(self._principal())})

    def _api_conversation_get(self, query):
        try:
            import portal_metafeatures as _mf
        except Exception as e:
            return self._vext_json({"ok": False, "error": str(e)}, 500)
        import urllib.parse as _up
        q = _up.parse_qs(query or "")
        rec = _mf.conv_get(self._principal(), q.get("id", [""])[0])
        if not rec:
            return self._vext_json({"ok": False, "error": "Unbekannte Konversation."}, 404)
        return self._vext_json({"ok": True, "conversation": rec})

    def _api_conversation_create(self, raw):
        try:
            import portal_metafeatures as _mf
            body = json.loads(raw or b"{}")
        except Exception as e:
            return self._vext_json({"ok": False, "error": str(e)}, 400)
        res = _mf.conv_start(self._principal(), body.get("a_sid"), body.get("b_sid"),
                             body.get("task"), title=body.get("title"),
                             max_turns=body.get("max_turns") or 8,
                             role_a=body.get("role_a"), role_b=body.get("role_b"))
        return self._vext_json(res, 200 if res.get("ok") else 400)

    def _api_conversation_stop(self, raw):
        try:
            import portal_metafeatures as _mf
            body = json.loads(raw or b"{}")
        except Exception as e:
            return self._vext_json({"ok": False, "error": str(e)}, 400)
        res = _mf.conv_stop(self._principal(), body.get("id") or body.get("cid"))
        return self._vext_json(res, 200 if res.get("ok") else 400)

    def _api_broadcast(self, raw):
        try:
            import portal_metafeatures as _mf
            body = json.loads(raw or b"{}")
        except Exception as e:
            return self._vext_json({"ok": False, "error": str(e)}, 400)
        res = _mf.broadcast(self._principal(), body.get("sids"), body.get("text"),
                            title=body.get("title"))
        return self._vext_json(res, 200 if res.get("ok") else 400)

    def _agent_kits(self, verb, args, uid):
        sid = re.sub(r"[^A-Za-z0-9_-]", "", (self.headers.get("X-Pn-Session-Sid", "") or ""))[:40]
        if not sid:
            return self._cer_json({"ok": False, "error": "no_session",
                                   "spoken": "Nur eine Session kann ihre eigene Ausstattung "
                                             "aendern."})
        try:
            from portal_session_svc import _sessprov_get as _pg
            prov = _pg(uid, sid) or {}
        except Exception as e:
            return self._cer_json({"ok": False, "error": str(e),
                                   "spoken": "Deine Ausstattung ist gerade nicht lesbar."})
        dran = [str(k) for k in (prov.get("kits") or [])]

        regal, karten = [], {}
        try:
            import pn_software_shelf as _shelf
            for _kid in sorted((_shelf.catalog() or {}).get("kits") or {}):
                if _shelf.kit_img(_kid):
                    regal.append(_kid)
            for _kid in regal:
                _rec = (_shelf.card_get(_kid) or {}).get("manual") or {}
                karten[_kid] = [p.get("name") for p in (_rec.get("programs") or [])][:24]
        except Exception:
            pass

        try:
            from portal_routes_session import KIT_DECKEL as _deckel
        except Exception:
            _deckel = 14

        if verb == "kits_status":
            return self._cer_json({"ok": True, "result": {
                "dran": dran, "regal": regal, "programme": karten,
                "deckel": _deckel, "frei": max(0, _deckel - len(dran)),
                "hinweis": "kits_add/kits_remove starten deine Zelle neu. Fuer EIN Programm "
                           "reicht apt-get — das braucht keinen Neustart."},
                "spoken": "Du hast %d von %d Kisten." % (len(dran), _deckel)})

        kit = str(args.get("kit") or args.get("name") or "").strip()
        if not kit:
            return self._cer_json({"ok": False, "error": "kit fehlt",
                                   "spoken": "Welche Kiste denn?"})
        if verb == "kits_add":
            if kit in dran:
                return self._cer_json({"ok": True, "result": {"dran": dran, "nichts_zu_tun": True},
                                       "spoken": "Die haengt schon dran."})
            if regal and kit not in regal:
                return self._cer_json({"ok": False, "error": "unbekannte Kiste %r" % kit,
                                       "result": {"regal": regal},
                                       "spoken": "Die Kiste kenne ich nicht."})
            if len(dran) >= _deckel:
                return self._cer_json({"ok": False, "error": "kein Platz",
                                       "result": {"dran": dran, "deckel": _deckel},
                                       "spoken": "Alle Plaetze belegt — haeng erst eine ab."})
            neu = dran + [kit]
        else:
            if kit not in dran:
                return self._cer_json({"ok": True, "result": {"dran": dran, "nichts_zu_tun": True},
                                       "spoken": "Die haengt gar nicht dran."})
            neu = [k for k in dran if k != kit]

        import json as _json
        return self._api_session_provision(
            _json.dumps({"sid": sid, "kits": neu, "restart": True}).encode(), uid_override=uid)

    def _agent_store(self, verb, args, uid):

        sid = re.sub(r"[^A-Za-z0-9_-]", "", (self.headers.get("X-Pn-Session-Sid", "") or ""))[:40]
        try:
            import portal_metasessions as _pm
            import pn_software_shelf as _shelf
        except Exception as e:
            return {"ok": False, "error": str(e), "spoken": "Das Software-Regal ist gerade nicht verfuegbar."}
        if not sid:
            return {"ok": False, "error": "no_session",
                    "spoken": "Ich kann die aufrufende Sitzung nicht bestimmen -- Regal-Verben gehen nur aus einer Session."}
        if not _pm._orch_has_right(uid, sid):
            return {"ok": False, "error": "orchestrate_denied",
                    "spoken": "Diese Sitzung hat kein Orchestrator-Recht und darf das Regal nicht steuern."}
        if verb == "store_status":
            res = _shelf.status_brief()
            _prov_log("agent.store_status", uid, json.dumps({"sid": sid})[:200], {"wire": "agent"})
            return {"ok": bool(res.get("ok", True)), "result": res,
                    "spoken": res.get("spoken") or "Regal-Status geliefert."}
        kit = re.sub(r"[^A-Za-z0-9._-]", "", str(args.get("kit") or ""))[:60]
        res = _shelf.onboard_request(kit)
        if res.get("ok"):
            _prov_log("agent.store_onboard", uid, json.dumps({"sid": sid, "kit": kit})[:200], {"wire": "agent"})
        return {"ok": bool(res.get("ok")), "result": res,
                "spoken": res.get("spoken") or res.get("reason") or "Abgelehnt."}

    def _agent_run(self, verb, args, uid):

        if verb == "state":
            return portal_agent.build_state(_agent_ctx(), uid), "Hier ist der aktuelle Zustand.", None
        if verb == "summon_lens":
            lens = str(args.get("lens") or "").strip()
            if lens == "terminal":

                _prov_log("hostshell.refused", uid, "summon_lens", {"wire": "agent", "lens": lens})
                return ({"ok": False, "error": "hostshell_retired", "retired": True},
                        _HOSTSHELL_GONE_SPOKEN, None)
            if lens not in ("chat", "browser", "notes", "screen", "queue", "attach"):
                return {"ok": False}, "Diese Ansicht kenne ich nicht.", None
            labels = {"browser": "Browser", "notes": "Notes",
                      "screen": "Screen", "queue": "Queue", "attach": "Anhänge", "chat": "Chat"}
            act = {"action": "summon", "lens": lens}
            pending_actions_push(uid, act)
            return {"ok": True, "lens": lens}, "Zeige %s." % labels.get(lens, lens), [act]
        if verb == "voice_say":

            sid = re.sub(r"[^A-Za-z0-9_-]", "", (self.headers.get("X-Pn-Session-Sid", "") or ""))[:40]
            if not sid:
                return ({"ok": False, "error": "no_session"},
                        "Sprechen kann nur eine Session fuer sich selbst.", None)
            kanal = str(args.get("channel") or "sprachsystem").strip().lower()
            pol = _sprach.policy_der_sitzung(uid, sid)
            if pol is None:
                return ({"ok": False, "error": "no_policy"},
                        "Zu dieser Session finde ich keine Rechte — es wird nichts gesagt.", None)
            ok, grund = _sprach.ansage(args.get("text"), kanal=kanal, policy=pol, sid=sid,
                                       ziel=(str(args.get("speaker") or "").strip() or None))
            if not ok:
                return {"ok": False, "error": grund}, grund, None
            return {"ok": True, "kanal": kanal, "info": grund}, grund, None
        if verb == "display_list":
            disps = _DISPLAY_REG.list() if _DISPLAY_REG else []
            names = ", ".join("%s%s" % (d.get("label") or d.get("name"),
                                        (" – %s" % d.get("name")) if d.get("label") and d.get("name") and d.get("label") != d.get("name") else "")
                              for d in disps) or "keine"
            return {"ok": True, "displays": disps}, "Anzeigen: %s." % names, None
        if verb == "display_show":

            did = str(args.get("display") or "local")
            did = (_DISPLAY_REG.resolve(did) or did) if _DISPLAY_REG else did
            ref = args.get("ref") or {}
            if isinstance(ref, str):
                ref = {"kind": "url", "value": ref} if "://" in ref else {"kind": "text", "text": ref}
            if not isinstance(ref, dict) or not (ref.get("value") or ref.get("text")):
                return {"ok": False}, "Was genau soll ich anzeigen?", None
            if ref.get("kind") in ("file", "object", "path"):
                _t, err = (_vext.resolve_ref(_vext_ctx(), uid, ref) if _vext else (None, "unavailable"))
                if err:
                    return {"ok": False, "error": err}, "Das darf ich hier nicht anzeigen.", None
            res, err = (_DISPLAY_REG.show(uid, did, ref, kiosk_post=_kiosk_post)
                        if _DISPLAY_REG else (None, "unavailable"))
            if err:
                return {"ok": False, "error": err}, "Anzeige nicht möglich.", None
            acts = pending_actions_drain(uid) if did == "local" else None
            _lbl = ((_DISPLAY_REG.get(did) or {}).get("label") if _DISPLAY_REG else None) or did
            where = "hier" if did == "local" else _lbl

            if isinstance(res, dict) and res.get("shown") is False:
                return {"ok": True, "shown": False, "result": res}, \
                    "%s ist gerade aus oder nicht erreichbar — ich konnte es dort nicht anzeigen." % _lbl, \
                    (acts or None)
            return {"ok": True, "result": res}, "Zeige es auf %s." % where, (acts or None)
        if verb == "display_restore":
            did = str(args.get("display") or "local")
            res, err = (_DISPLAY_REG.restore_idle(uid, did, kiosk_post=_kiosk_post)
                        if _DISPLAY_REG else (None, "unavailable"))
            if err:
                return {"ok": False, "error": err}, "Konnte die Anzeige nicht zurücksetzen.", None
            acts = pending_actions_drain(uid) if did == "local" else None
            return {"ok": True}, "Anzeige zurückgesetzt.", (acts or None)
        if verb == "hpc_status":

            jid = re.sub(r"[^0-9_]", "", str(args.get("job_id") or ""))[:20]
            cmd = ("sacct -j %s --format=JobID,JobName%%20,State,Elapsed -n 2>/dev/null || squeue -j %s" % (jid, jid)) \
                  if jid else "squeue --me -o '%.10i %.20j %.8T %.10M' 2>/dev/null | head -15"
            res, err = _hpc_ssh(cmd, uid=uid)
            if err:
                return {"ok": False, "error": err}, err, None
            out = (res.get("out") or "").strip()
            spoken = ("Auf dem Cluster: %s" % out[:400]) if out else "Auf dem Cluster laufen gerade keine Aufträge von dir."
            return {"ok": True, "out": out}, spoken, None
        if verb == "hpc_submit":

            res, err = _hpc_submit(command=args.get("command"), script=args.get("script"),
                                      name=args.get("name"), uid=uid)
            if err:
                return {"ok": False, "error": err}, err, None
            return ({"ok": True, "job_id": res["job_id"]},
                    "Auftrag auf dem Cluster angenommen, Job-Nummer %s." % res["job_id"], None)
        if verb == "hpc_fetch":

            res, err = _hpc_fetch(args.get("path"), uid=uid, max_kb=args.get("max_kb"))
            if err:
                return {"ok": False, "error": err}, err, None
            n = res.get("bytes", 0)
            return {"ok": True, **res}, ("%d Bytes vom Cluster gelesen." % n) if n else "Datei leer/nicht gefunden.", None
        if verb == "hpc_ctl":
            res, err = _hpc_ctl(args.get("command"), uid=uid)
            if err:
                return {"ok": False, "error": err}, err, None
            out = (res.get("out") or "").strip()
            return {"ok": True, "out": out}, ("Login-Node: %s" % out[:400]) if out else "Kein Output.", None
        if verb == "hpc_slurmwatch":

            res, err = _hpc_slurmwatch(args.get("aktion"), uid=uid)
            if err:
                return {"ok": False, "error": err}, err, None
            spoken = ("Der Cluster-Melder läuft." if res.get("laeuft")
                      else "Der Cluster-Melder läuft NICHT — auf dem Login-Knoten ist niemand da, "
                           "der über fehlgeschlagene Rechnungen Bescheid gibt.")
            return {"ok": True, **res}, spoken, None
        if verb == "browser_open":
            url = _agent_norm_url(args.get("url") or args.get("query") or "")
            if not url:
                return {"ok": False}, "Welche Adresse soll ich öffnen?", None
            res = _screen_open(url, uid)
            if res.get("ok"):
                links_add(uid, url, "opened")
            spoken = ("Öffne %s im Browser." % url) if res.get("ok") else \
                     ("Konnte den Browser nicht öffnen: %s" % res.get("error"))
            return res, spoken, None
        if verb in ("terminal_run", "terminal_read"):

            _prov_log("hostshell.refused", uid, verb, {"wire": "agent", "args_keys": sorted(args)[:8]})
            return ({"ok": False, "error": "hostshell_retired", "retired": True},
                    _HOSTSHELL_GONE_SPOKEN, None)
        if verb == "app_sense":
            res = seat_low_stakes("app.sense", {"mode": args.get("mode", "text")}, uid)
            return res, (res.get("speech") or "")[:240], None
        if verb == "app_drive":
            vmap = {"click": "app.click", "scroll": "app.scroll", "type": "app.type",
                    "enter": "app.enter", "press": "app.enter"}
            v = vmap.get(str(args.get("action") or "").strip())
            if not v:
                return {"ok": False}, "Unbekannte App-Aktion.", None
            params = {"x": args.get("x"), "y": args.get("y"), "n": args.get("n"),
                      "text": args.get("text"), "btn": args.get("btn"),
                      "mode": args.get("mode", "text"), "secret": bool(args.get("secret"))}
            res = seat_low_stakes(v, params, uid)
            return res, (res.get("speech") or "")[:240], None
        if verb == "client_input":

            raw = str(args.get("verb") or args.get("action") or "").strip().lower()
            v = _CLIENT_VERB_ALIAS.get(raw, raw)
            if v not in _CLIENT_VERBS:
                return {"ok": False}, "Diese Eingabe-Aktion kenne ich nicht.", None
            a = {}
            if v in ("paste", "type") and args.get("text") is not None:
                a["text"] = str(args.get("text"))
            if v == "press":
                keys = str(args.get("keys") or args.get("text") or "").strip()
                if not keys:
                    return {"ok": False}, "Welche Taste soll ich druecken?", None
                a["keys"] = keys
            if v == "select":
                what = str(args.get("what") or "all").strip()
                a["what"] = what if what in ("all", "line", "word") else "all"
            if v == "right-click":
                if args.get("x") is not None:
                    a["x"] = args.get("x")
                if args.get("y") is not None:
                    a["y"] = args.get("y")
            act = {"action": v, "args": a}
            pending_actions_push(uid, act)
            spoken = {"copy": "Kopiert.", "cut": "Ausgeschnitten.", "paste": "Eingefuegt.",
                      "select": "Markiert.", "select-all": "Alles markiert.", "undo": "Rueckgaengig.",
                      "redo": "Wiederholt.", "delete": "Geloescht.", "right-click": "Rechtsklick.",
                      "context-menu": "Kontextmenue.", "press": "Taste gedrueckt.",
                      "type": "Getippt."}.get(v, "Erledigt.")
            return {"ok": True, "verb": v}, spoken, [act]
        if verb == "files_list":
            app = str(args.get("app") or "notes")
            try:
                files = _fabric.open_store(uid, app).list_files() if _fabric else []
            except Exception:
                files = []
            return {"ok": True, "app": app, "files": files}, "%d Dateien in %s." % (len(files), app), None
        if verb == "file_read":
            app = str(args.get("app") or "notes"); name = str(args.get("name") or "")
            if not name:
                return {"ok": False}, "Welche Datei?", None
            try:
                with _fabric.open_store(uid, app).open(name, "rb") as fh:
                    data = fh.read().decode("utf-8", "replace")
            except Exception as e:
                return {"ok": False, "error": str(e)}, "Datei nicht gefunden.", None
            return {"ok": True, "app": app, "name": name, "body": data[:20000]}, "Gelesen: %s." % name, None
        if verb == "file_write":
            app = str(args.get("app") or "notes"); name = str(args.get("name") or "")
            if not name:
                return {"ok": False}, "Welcher Dateiname?", None
            try:
                with _fabric.open_store(uid, app).open(name, "wb") as fh:
                    fh.write(str(args.get("body") or "").encode("utf-8"))
            except Exception as e:
                return {"ok": False, "error": str(e)}, "Konnte nicht schreiben.", None
            return {"ok": True, "app": app, "name": name}, "Gespeichert: %s." % name, None
        if verb == "queue_job":
            prompt = str(args.get("prompt") or "").strip()
            cmd = args.get("cmd")
            if prompt:
                jid = job_create(prompt, args.get("email"), None, "commission", principal=uid)
                return {"ok": True, "job": jid, "link": job_link(jid)}, "Auftrag angelegt.", None
            if cmd:

                return ({"ok": False, "geschlossen": True},
                        "Rohe Befehle auf dem Wirt sind geschlossen. Sag mir, WAS getan werden "
                        "soll, dann lege ich einen Auftrag an — der laeuft in einer eigenen Zelle.",
                        None)
            return {"ok": False}, "Was soll in die Warteschlange?", None
        return {"ok": False}, "Diesen Befehl kenne ich nicht.", None

    def _agent_irreversible(self, verb, args, uid):

        if _ceremony is None or _CerEntry is None:
            _prov_log("agent." + verb, uid, "", {"wire": "agent", "ceremony": "unavailable"})
            return self._cer_json({"ok": False, "result": {"action": "refuse", "verb": verb},
                "spoken": "Das ist unumkehrbar und die Ceremony-Engine ist nicht geladen — ich mache das nicht ungefragt."})
        if verb == "enter_credential":
            cerverb = "verb.credential_enter"
            text = str(args.get("name") or "").strip() or "(unbenannt)"
        else:
            raw = str(args.get("verb") or "").strip().lower()
            cerverb = {"send": "verb.mail_send", "mail_send": "verb.mail_send", "delete": "verb.delete",
                       "pay": "verb.pay", "commit": "verb.commit", "kill": "verb.kill"}.get(
                           raw, "verb." + (raw or "action"))
            text = args.get("text") or json.dumps(args, ensure_ascii=False)
        try:
            tgt = _cer_target(cerverb, text)
            re_id = "re-" + secrets.token_hex(6)

            def _do(_v=cerverb):
                return {"committed": True, "verb": _v, "effect": "recorded-intent (kein Backend)"}

            cer, prompt = _ceremony.begin(re_id=re_id, verb=cerverb, target=tgt, action=_do,
                                          subject=tgt.meta.get("subject"))
        except Exception as e:
            return self._cer_json({"ok": False, "error": str(e),
                                   "spoken": "Ceremony konnte nicht gestartet werden."})
        _prov_log("agent." + verb, uid, str(text)[:400],
                  {"wire": "agent", "ceremony": "armed", "re": re_id, "verb": cerverb})
        return self._cer_json({"ok": True, "result": {"action": "ceremony", "re": re_id, "verb": cerverb,
            "readback": prompt.get("readback"), "challenge": prompt.get("challenge"),
            "hold_ms": prompt.get("hold_ms")},
            "spoken": prompt.get("spoken") or "Bitte bestätige das ausdrücklich mit der Zahl."})

    def _llm_pool_action(self, body):

        if _LLMPOOL is None:
            return {"ok": False, "msg": "llm pool unavailable"}
        action = (body.get("action") or "").strip()
        if action == "reload":
            out = _LLMPOOL.reload()
            _prov_log("llm.pool_reload", self._principal(), "", {"accounts": out.get("accounts")})
            return out
        if action == "refresh_usage":
            out = _LLMPOOL.refresh_usage()
            _prov_log("llm.pool_refresh_usage", self._principal(), "",
                      {"updated": out.get("updated")})
            return out
        if action == "clear_cooldown":
            out = _LLMPOOL.clear_cooldown((body.get("id") or "").strip())
            _prov_log("llm.pool_clear_cooldown", self._principal(), body.get("id") or "", {"ok": out.get("ok")})
            return out
        if action == "prefer":

            _aid = (body.get("id") or "").strip() or None
            out = _LLMPOOL.set_prefs(preferred=_aid)
            return {"ok": True, "preferred": out.get("preferred")}
        if action == "switch_pct":
            try:
                _v = int(body.get("value") or 0)
            except Exception:
                return {"ok": False, "msg": "Zahl erwartet"}
            out = _LLMPOOL.set_prefs(switch_pct=(_v if 0 < _v <= 100 else None))
            return {"ok": True, "switch_pct": out.get("switch_pct")}
        if action in ("enable", "disable"):
            out = _LLMPOOL.set_enabled((body.get("id") or "").strip(), action == "enable")
            _prov_log("llm.pool_" + action, self._principal(), body.get("id") or "", {"ok": out.get("ok")})
            return out
        if action == "remove":
            aid = (body.get("id") or "").strip()
            self._oauth_teardown(aid)
            out = _LLMPOOL.remove_account(aid)
            _prov_log("llm.pool_remove", self._principal(), aid, {"ok": out.get("ok")})
            return out
        return {"ok": False, "msg": "unknown action (reload | clear_cooldown | enable | disable | remove)"}

    @staticmethod
    def _cred_fp(home):

        if not home:
            return ""
        h = hashlib.sha256()
        try:
            with open(os.path.join(home, ".claude", ".credentials.json"), "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"-")
        try:
            with open(os.path.join(home, ".claude.json")) as f:
                acct = (json.load(f) or {}).get("oauthAccount") or {}
            h.update(json.dumps(acct, sort_keys=True).encode())
        except (OSError, ValueError):
            h.update(b"-")
        return h.hexdigest()

    def _llm_oauth_start(self, body):

        if _LLMPOOL is None:
            return {"ok": False, "msg": "llm pool unavailable"}
        aid = str(body.get("id") or "").strip()
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$", aid):
            return {"ok": False, "msg": "bad account id"}
        home = _LLMPOOL.account_home(aid)
        if home is None:
            res = _LLMPOOL.add_account(aid)
            if not res.get("ok"):
                return res
            home = res.get("home")
        try:
            os.makedirs(os.path.join(home, ".claude"), exist_ok=True)
        except OSError as e:
            return {"ok": False, "msg": "home: %s" % type(e).__name__}
        cell_uid = _oauth_cell_uid(aid)
        sess = _oauth_sess_name(aid)

        with _OAUTH_LOCK:
            prev = _OAUTH_SESS.get(aid)
        if prev and prev.get("url"):
            pane_now = _oauth_pane(prev.get("sess") or "")
            if ("__OAUTH_EXIT_" not in pane_now) and (
                    "paste code here" in pane_now.lower() or "press enter to retry" in pane_now.lower()):
                return {"ok": True, "id": aid, "url": prev["url"], "cell_uid": prev.get("cell_uid") or "",
                        "vnc": "/vnc3?cell=" + urllib.parse.quote(prev.get("cell_uid") or ""),
                        "phase": "awaiting_code", "reused": True}

        self._oauth_teardown(aid)
        with _OAUTH_LOCK:
            _tmux("kill-session", "-t", sess)
            cmd = "env HOME=%s %s setup-token; printf '__OAUTH_EXIT_%%d__\\n' $?; sleep 240" % (
                shlex.quote(home), shlex.quote(CLAUDE_BIN))
            r = _tmux("new-session", "-d", "-s", sess, "-x", str(_OAUTH_PANE_W), "-y", "50", "bash", "-lc", cmd)
            if getattr(r, "returncode", 1) != 0:
                return {"ok": False, "msg": "tmux start failed"}
            _OAUTH_SESS[aid] = {"sess": sess, "home": home, "cell_uid": cell_uid, "url": "",
                                "phase": "starting", "started": time.time(), "code_sent": False,
                                "baseline": self._cred_fp(home)}
        url = ""
        truncated = False
        for _ in range(24):
            pane = _oauth_pane(sess)
            m = _OAUTH_URL_RE.search(pane)
            if m:
                if len(m.group(0)) >= _OAUTH_PANE_W - 4:
                    truncated = True
                else:
                    url = m.group(0); break
            if "__OAUTH_EXIT_" in pane:
                break
            time.sleep(0.4)
        if not url:
            tail = _oauth_pane(sess)[-300:]
            self._oauth_teardown(aid)
            msg = ("OAuth URL truncated at pane width — refusing to hand out a broken link"
                   if truncated else "no OAuth URL captured")
            return {"ok": False, "msg": msg, "tail": tail}
        with _OAUTH_LOCK:
            s = _OAUTH_SESS.get(aid)
            if s:
                s["url"] = url; s["phase"] = "awaiting_code"
        try:
            _screen_open(url, uid=cell_uid)
        except Exception:
            pass
        _prov_log("llm.oauth_start", self._principal(), aid, {"cell": cell_uid})
        return {"ok": True, "id": aid, "url": url, "cell_uid": cell_uid,
                "vnc": "/vnc3?cell=" + urllib.parse.quote(cell_uid), "phase": "awaiting_code"}

    def _llm_oauth_status(self, aid):
        aid = str(aid or "").strip()
        with _OAUTH_LOCK:
            s = dict(_OAUTH_SESS.get(aid) or {})
        home = s.get("home") or (_LLMPOOL.account_home(aid) if _LLMPOOL else "") or ""
        li = _oauth_logged_in(home)

        if s and li and s.get("baseline") is not None and self._cred_fp(home) == s.get("baseline"):
            li = False
        email = ""
        if li:
            try:
                email = (_llmpool_mod.read_account_info(home, _LLMPOOL.usage_path, ttl=30)
                         or {}).get("email") or ""
            except Exception:
                email = ""
        if not s:
            return {"ok": True, "id": aid, "phase": ("done" if li else "idle"),
                    "logged_in": li, "email": email}
        pane = _oauth_pane(s.get("sess") or "")
        phase = "done" if li else s.get("phase") or "starting"
        if not li and "__OAUTH_EXIT_" in pane and s.get("code_sent"):
            phase = "error"
        return {"ok": True, "id": aid, "phase": phase, "url": s.get("url") or "", "logged_in": li,
                "email": email, "cell_uid": s.get("cell_uid") or "",
                "vnc": "/vnc3?cell=" + urllib.parse.quote(s.get("cell_uid") or ""), "tail": pane[-260:]}

    def _llm_oauth_code(self, body):

        if _LLMPOOL is None:
            return {"ok": False, "msg": "llm pool unavailable"}
        aid = str(body.get("id") or "").strip()
        code = str(body.get("code") or "").strip()
        if not aid or not code:
            return {"ok": False, "msg": "id + code required"}
        if not re.match(r"^[A-Za-z0-9._~:/?#=&%+\-]{4,600}$", code):
            return {"ok": False, "msg": "code looks malformed"}
        with _OAUTH_LOCK:
            s = _OAUTH_SESS.get(aid)
            if not s:
                return {"ok": False, "msg": "no active oauth session — start first"}
            sess = s["sess"]; home = s["home"]; s["code_sent"] = True; s["phase"] = "exchanging"
            baseline = s.get("baseline")

        if "press enter to retry" in _oauth_pane(sess).lower():
            _tmux("send-keys", "-t", sess, "Enter")
            time.sleep(0.6)
        _tmux("send-keys", "-t", sess, "-l", code)
        time.sleep(0.2)
        _tmux("send-keys", "-t", sess, "Enter")
        ok = False; cli_error = ""
        for _ in range(90):
            if _oauth_logged_in(home) and (
                    baseline is None or self._cred_fp(home) != baseline):
                ok = True; break

            pane_now = _oauth_pane(sess)
            if "oauth error" in pane_now.lower() or "__OAUTH_EXIT_" in pane_now:
                cli_error = pane_now; break
            time.sleep(0.5)
        if not ok:

            pane_all = cli_error or _oauth_pane(sess)
            lines = [l.strip() for l in pane_all.splitlines() if l.strip()]
            tail = "\n".join(lines[-8:])
            low = pane_all.lower()
            if "invalid" in low or "expired" in low:
                hint = ("Der Code wurde abgelehnt (ungültig oder abgelaufen). Wichtig: der Code passt "
                        "nur zum ZULETZT erzeugten Anmelde-Link — Link neu erzeugen, im selben Fenster "
                        "anmelden und den frischen Code sofort einfügen.")
            elif ("network" in low or "enotfound" in low or "etimedout" in low
                  or "fetch failed" in low or "econnrefused" in low):
                hint = ("Netzwerkfehler beim Token-Austausch — die Box kam nicht zu claude.com durch. "
                        "Internet der Box prüfen und den Anmelde-Link neu starten.")
            else:
                hint = ("Der Token-Austausch kam nicht durch (Details unten). "
                        "Anmelde-Link neu starten und den frischen Code einfügen.")
            return {"ok": False, "phase": "error", "msg": hint, "tail": tail}
        _LLMPOOL.set_enabled(aid, True)
        _LLMPOOL.reload()
        info = _llmpool_mod.read_account_info(home, _LLMPOOL.usage_path, ttl=0) if _llmpool_mod else {}
        self._oauth_teardown(aid)
        _prov_log("llm.oauth_done", self._principal(), aid, {"email": info.get("email")})
        return {"ok": True, "id": aid, "logged_in": True, "enabled": True, "email": info.get("email") or ""}

    def _llm_oauth_cancel(self, body):
        aid = str(body.get("id") or "").strip()
        self._oauth_teardown(aid)
        _prov_log("llm.oauth_cancel", self._principal(), aid, {})
        return {"ok": True, "id": aid}

    def _llm_oauth_logout(self, body):

        if _LLMPOOL is None:
            return {"ok": False, "msg": "llm pool unavailable"}
        aid = str(body.get("id") or "").strip()
        if not aid:
            return {"ok": False, "msg": "id required"}

        home = _LLMPOOL.account_home(aid)
        if not home:
            return {"ok": True, "id": aid, "logged_in": False}
        self._oauth_teardown(aid)
        bdir = os.path.join(home, ".claude", "backups")
        try:
            os.makedirs(bdir, exist_ok=True)
        except OSError:
            pass
        cred = os.path.join(home, ".claude", ".credentials.json")
        if os.path.exists(cred):
            try:
                os.replace(cred, os.path.join(
                    bdir, "credentials-logout-%s.json" % time.strftime("%Y%m%d-%H%M%S")))
            except OSError as e:
                return {"ok": False, "msg": "Abmelden fehlgeschlagen: %s" % type(e).__name__}
        cj = os.path.join(home, ".claude.json")
        try:
            with open(cj) as f:
                d = json.load(f)
            if isinstance(d, dict) and d.pop("oauthAccount", None) is not None:
                with open(cj, "w") as f:
                    json.dump(d, f)
        except (OSError, ValueError):
            pass
        try:
            _LLMPOOL.set_enabled(aid, False)
            _LLMPOOL.reload()
        except Exception:
            pass
        _prov_log("llm.oauth_logout", self._principal(), aid, {})
        return {"ok": True, "id": aid, "logged_in": False}

    def _llm_oauth_self_aid(self):

        return "usr-" + hashlib.sha256(("llmpool-byo:" + str(self._principal())).encode()).hexdigest()[:20]

    @staticmethod
    def _job_label(job):
        if job.get("room"):
            return "Raum " + str(job.get("room"))
        cmd = job.get("cmd")
        try:
            parts = json.loads(cmd) if isinstance(cmd, str) else (cmd or [])
            toks = [p for p in parts if isinstance(p, str) and "=" not in p and p != "/usr/bin/env"]
            base = os.path.basename(toks[0]) if toks else ""
            if base in ("claude", "python3", "python", "bash", "sh", "env") and len(toks) > 1:
                base = os.path.basename(toks[1])
            return (base or job.get("client_tag") or "Aufgabe")[:48]
        except Exception:
            return (job.get("client_tag") or "Aufgabe")[:48]

    def _job_owner_principals(self, job):
        return (job.get("principal"), job.get("submitter_principal"))

    def _subsession_rows(self, admin, caller):
        rows = []
        _map = {"pending": "queued", "starting": "running", "running": "running"}
        try:
            meta = _meta_load() or {}
        except Exception:
            return rows
        for msid, ms in meta.items():
            owner = ms.get("owner") or DEFAULT_PRINCIPAL
            if not admin and owner != caller:
                continue
            if ms.get("state") not in ("running", None) and not admin:
                pass
            title = (ms.get("title") or msid)
            for t in ms.get("tasks", []):
                state = _map.get(t.get("state", "pending"))
                if not state:
                    continue
                snippet = " ".join((t.get("prompt") or "").split())[:60]
                rows.append({"id": "sub:%s:%s" % (msid, t.get("tid")), "kind": "subsession",
                             "state": state, "principal": owner,
                             "label": "🧩 %s%s" % (title, (" · " + snippet) if snippet else ""),
                             "submitted_at": ms.get("created"), "room": None,
                             "prog_msg": "Sub-Session" + (" · wartet auf Kapazität" if state == "queued" else "")})
        return rows

    def _llmd_snap(self, verb, timeout=3):

        now = time.time()
        ent = _LLMD_SNAP_CACHE.get(verb)
        if ent and now - ent[0] < _LLMD_SNAP_TTL:
            return ent[1]
        r = self._llmd_snap_live(verb, timeout=timeout)
        _LLMD_SNAP_CACHE[verb] = (now, r)
        return r

    def _llmd_snap_live(self, verb, timeout=3):
        sock = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid()), "pn-llmd.sock")
        try:

            import sys as _s
            for _p in (os.environ.get("PNLIB_HOME"),
                       os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                    "engine"),
                       os.path.expanduser("~/portioneer")):
                if _p and os.path.isdir(os.path.join(_p, "pnlib")) and _p not in _s.path:
                    _s.path.insert(0, _p)
            from pnlib import ipc
            return ipc.send_request({"verb": verb}, timeout=timeout, path=sock)
        except Exception:
            return {}

    def _admission_load(self, cell_map=None):

        def summ(v):
            s = self._llmd_snap(v)
            if not s.get("ok"):
                return None
            out = {"slots": s.get("slots"), "in_use": s.get("in_use"),
                   "waiting": s.get("waiting"), "free": s.get("free")}
            if isinstance(s.get("aimd"), dict):
                out["aimd"] = {k: s["aimd"].get(k) for k in ("on", "cur", "min", "max")}

            cm = cell_map or {}
            def _tick(t):
                d = {k: t.get(k) for k in ("id", "principal", "cell", "klass", "wait_s", "position", "done_at")
                     if t.get(k) is not None}

                sess = cm.get(t.get("cell"))
                if sess:
                    d["origin"] = sess.get("title") or sess.get("sid")
                    d["sid"] = sess.get("sid")
                    if sess.get("model"):
                        d["model"] = sess.get("model")
                    if sess.get("voice"):
                        d["voice"] = True
                elif t.get("cell") in (None, "", "tcp"):
                    d["origin"] = "extern / API"
                return d
            out["granted_list"] = [_tick(t) for t in (s.get("granted_list") or [])][:40]
            out["waiting_list"] = [_tick(t) for t in (s.get("waiting_list") or [])][:40]
            out["done_list"] = [_tick(t) for t in (s.get("done_list") or [])][:40]
            return out
        return {"llm": summ("admit-snapshot"), "exec": summ("exec-admit-snapshot"),
                "act": summ("act-admit-snapshot")}

    def _session_load_row(self, j):

        tag = str(j.get("client_tag") or "")
        sid = tag.split("-")[-1].split("_")[0] if tag else ""
        principal = j.get("principal") or ""
        title = sid
        try:
            if _session_store is not None and sid:
                s = _session_store(principal, "cockpit").get(sid)
                if s and s.get("title"):
                    title = s.get("title")
        except Exception:
            pass
        model = ""
        try:
            if sid:
                from portal_session_svc import _sessprov_get as _pg
                raw = str((_pg(principal, sid) or {}).get("model") or "").lower()
                model = next((t.capitalize() for t in ("opus", "sonnet", "haiku") if t in raw),
                             raw[:12])
        except Exception:
            pass
        _srow = {"id": j.get("id"), "sid": sid, "principal": principal, "title": title,
                "state": j.get("state"), "started_at": j.get("started_at"),
                "mem_mb": j.get("mem_estimate"), "model": model,
                "voice": str(j.get("task_type") or "").startswith("voice")}
        try:
            import portal_placement as _plc; _plc.stamp(_srow)
        except Exception:
            pass
        return _srow

    def _split_session_jobs(self, jobs, only_principal=None):

        real, sess, seen = [], [], set()
        for j in jobs:
            if j.get("source") == "session":

                if j.get("state") == "running" and (only_principal is None or j.get("principal") == only_principal):
                    row = self._session_load_row(j)
                    if row["sid"] not in seen:
                        seen.add(row["sid"]); sess.append(row)
                continue
            real.append(j)
        return real, sess

    def _sess_cell_map(self, sess):

        out = {}
        try:
            import pn_cell_session as _cs
            namer = getattr(_cs, "_cell_name", None)
        except Exception:
            namer = None
        if not namer:
            return out
        for s in sess or []:
            try:
                cn = namer(s.get("principal"), s.get("sid"))
                if cn:
                    out[cn] = s
            except Exception:
                pass
        return out

    _QV_FIELDS = ("id", "state", "principal", "submitter_principal", "client_tag", "task_type",
                  "source", "room", "prog_done", "prog_total", "prog_msg", "submitted_at",
                  "started_at", "finished_at", "exit_code", "mem_estimate", "prio", "cmd",
                  "node", "node_id", "not_before", "attempts", "fail_class",
                  "wait_reason", "wait_reason_de",

                  "kerne", "kerne_wunsch", "dauer_s")

    def _pnd_status_cached(self):

        now = time.time()
        if _PND_STATUS_CACHE and now - _PND_STATUS_CACHE[0][0] < _PND_STATUS_TTL:
            return _PND_STATUS_CACHE[0][1]
        stt = pn_req({"verb": "status"})
        if stt.get("ok"):
            _PND_STATUS_CACHE[:] = [(now, stt)]
        return stt

    def _queue_view(self, limit):

        from pn_governed import pn_list as _pn_list
        lim = max(int(limit or 100), 1)
        lst = _pn_list(min(lim + 40, 500), fields=self._QV_FIELDS, cmd_max=200)
        if not lst.get("ok"):
            return {"ok": False, "error": lst.get("error", "pnd unavailable")}
        jobs = lst.get("jobs", [])
        counts = lst.get("counts", {})
        if self._is_admin():
            stt = self._pnd_status_cached()
            subs = self._subsession_rows(True, None)
            qsub = sum(1 for s in subs if s["state"] == "queued")
            real, sess = self._split_session_jobs(jobs)
            cell_map = self._sess_cell_map(sess)
            admin_jobs = real[:lim]
            try:
                import portal_placement as _plc
                for _j in admin_jobs:
                    _plc.stamp(_j)
            except Exception:
                pass
            return {"ok": True, "admin": True, "gekuerzt": lst.get("gekuerzt"),
                    "jobs": admin_jobs + subs, "counts": counts,
                    "sessions": sess, "now": lst.get("now"), "status": stt if stt.get("ok") else None,
                    "subsessions": len(subs), "admission": self._admission_load(cell_map),
                    "queued_total": counts.get("queued", 0) + qsub}
        caller = self._principal()
        real, sess = self._split_session_jobs(jobs, only_principal=caller)
        out = []
        for j in real:
            if caller not in self._job_owner_principals(j):
                continue
            item = {"id": j.get("id"), "state": j.get("state"), "tag": j.get("client_tag"),
                    "label": self._job_label(j), "room": j.get("room"),
                    "prog_done": j.get("prog_done"), "prog_total": j.get("prog_total"),
                    "prog_msg": j.get("prog_msg"), "submitted_at": j.get("submitted_at"),
                    "exit_code": j.get("exit_code")}
            if j.get("kerne"):
                item["kerne"] = j.get("kerne")
                if j.get("kerne_wunsch"):
                    item["kerne_wunsch"] = j.get("kerne_wunsch")
            if j.get("state") in ("queued", "running"):
                e = (pn_req({"verb": "eta", "id": j.get("id")}) or {}).get("eta") or {}
                item["position"] = e.get("position")
                item["ahead"] = e.get("ahead")
                item["eta_s"] = e.get("eta_done_s")
                item["eta_human"] = e.get("human")
            try:
                import portal_placement as _plc; _plc.stamp(item)
            except Exception:
                pass
            out.append(item)
        out += self._subsession_rows(False, caller)
        out.sort(key=lambda x: (0 if x["state"] == "running" else 1 if x["state"] == "queued" else 2,
                                x.get("position") or 10 ** 9, -(x.get("submitted_at") or 0)))
        qsub = sum(1 for x in out if x.get("kind") == "subsession" and x["state"] == "queued")
        return {"ok": True, "admin": False, "gekuerzt": lst.get("gekuerzt"),
                "jobs": out, "mine": len(out), "sessions": sess,
                "queued_total": counts.get("queued", 0) + qsub, "now": lst.get("now")}

    def _queue_cancel(self, jid):

        if not self._is_admin():
            j = pn_req({"verb": "job", "id": jid})
            job = j.get("job") if isinstance(j.get("job"), dict) else j
            if not job or self._principal() not in self._job_owner_principals(job):
                return {"ok": False, "error": "not your job"}
        r = pn_req({"verb": "cancel", "id": jid})
        _prov_log("queue.cancel", self._principal(), str(jid), {"admin": self._is_admin()})
        return r

    def _queue_clear_waiting(self):

        if not self._is_admin():
            return {"ok": False, "error": "clear-waiting ist admin-only"}

        r = pn_req({"verb": "admin-clear-queued"})
        if not r or not r.get("ok"):
            return {"ok": False, "error": (r or {}).get("error", "pnd unavailable")}
        n = int(r.get("count") or 0)
        _prov_log("queue.clear_waiting", self._principal(), "", {"cleared": n})
        return {"ok": True, "cleared": n, "total": n}

    def _api_admin_fairshare(self):

        if not self._is_admin():
            return self._sess_json({"ok": False, "error": "admin only"}, 403)
        r = pn_req({"verb": "admin-fairshare"}) or {}

        if isinstance(r, dict) and isinstance(r.get("rows"), list):
            _agg, _order = {}, []
            for _row in r["rows"]:
                _pr = _row.get("principal") or _row.get("account") or "?"
                _a = _agg.get(_pr)
                if _a is None:
                    _a = {"principal": _pr, "account": _pr, "weight": _row.get("weight") or 1,
                          "qos_preset": _row.get("qos_preset"), "max_sessions": _row.get("max_sessions") or 0,
                          "max_submit_jobs": _row.get("max_submit_jobs"),
                          "submit_enabled": bool(_row.get("submit_enabled", True)),
                          "block_reason": _row.get("block_reason"),
                          "norm_share": 0.0, "backlog_s": 0.0, "rows": 0, "_ff": []}
                    _agg[_pr] = _a
                    _order.append(_pr)
                _a["norm_share"] += float(_row.get("norm_share") or 0)
                _a["backlog_s"] += float(_row.get("backlog_s") or 0)
                _a["rows"] += int(_row.get("rows") or 0)
                if _row.get("fair_factor") is not None:
                    try:
                        _a["_ff"].append(float(_row["fair_factor"]))
                    except (TypeError, ValueError):
                        pass
                if _row.get("qos_preset") and not _a.get("qos_preset"):
                    _a["qos_preset"] = _row["qos_preset"]
                if _row.get("weight"):
                    _a["weight"] = max(_a.get("weight") or 1, _row["weight"])
                if _row.get("max_sessions"):
                    _a["max_sessions"] = max(_a.get("max_sessions") or 0, _row["max_sessions"])
                if not _row.get("submit_enabled", True):
                    _a["submit_enabled"] = False
                if _row.get("block_reason") and not _a.get("block_reason"):
                    _a["block_reason"] = _row["block_reason"]
            _rows = []
            for _pr in _order:
                _a = _agg[_pr]
                _ff = _a.pop("_ff")
                _a["fair_factor"] = round(sum(_ff) / len(_ff), 3) if _ff else 1
                _a["norm_share"] = round(_a["norm_share"], 4)
                _rows.append(_a)
            r["rows"] = _rows

        if isinstance(r, dict) and r.get("rows") and _session_store is not None:
            for _row in r["rows"]:
                _pr = _row.get("principal") or _row.get("account")
                try:
                    _recs = _session_store(_pr, "cockpit").list()
                    _row["live_sessions"] = sum(1 for _x in _recs
                        if _x.get("state") not in ("deleted",) and not _x.get("archived"))
                except Exception:
                    _row["live_sessions"] = None
        return self._sess_json(r)

    def _api_admin_policy(self, body):

        if not self._is_admin():
            return self._sess_json({"ok": False, "error": "admin only"}, 403)
        body = body if isinstance(body, dict) else {}
        _VMAP = {"submit-suspend": "admin-submit-suspend", "submit-resume": "admin-submit-resume",
                 "apply-preset": "admin-apply-preset", "set-policy": "admin-set-policy"}
        verb = _VMAP.get(str(body.get("action") or ""))
        if not verb:
            return self._sess_json({"ok": False, "error": "unbekannte action"})
        req = {"verb": verb}
        for k in ("target_principal", "reason", "preset", "weight", "max_submit_jobs", "max_sessions",
                  "priority_boost", "boost_expiry", "exclusive_entitled", "preempt_entitled", "qos_preset"):
            if k in body:
                req[k] = body[k]
        r = pn_req(req)
        _prov_log("admin.policy", self._principal(), str(body.get("target_principal") or ""),
                  {"action": body.get("action"), "ok": (r or {}).get("ok")})
        return self._sess_json(r)

    def _queue_reprioritize(self, jid, body):

        if not self._is_admin():
            return {"ok": False, "error": "reprioritize is admin-only"}
        req = {"verb": "admin-reprioritize", "id": jid}
        body = body if isinstance(body, dict) else {}
        if body.get("prio") is not None:
            try:
                req["prio"] = int(body.get("prio"))
            except (TypeError, ValueError):
                return {"ok": False, "error": "prio must be an integer"}
        elif body.get("delta") is not None:
            try:
                req["delta"] = int(body.get("delta"))
            except (TypeError, ValueError):
                return {"ok": False, "error": "delta must be an integer"}
        else:
            return {"ok": False, "error": "reprioritize requires 'prio' or 'delta'"}
        r = pn_req(req)
        _prov_log("queue.reprioritize", self._principal(), str(jid),
                  {"prio": r.get("prio"), "old_prio": r.get("old_prio"),
                   "delta": req.get("delta"), "abs": req.get("prio"), "ok": r.get("ok")})
        return r

    def _set_prio_bias(self, uid, body):

        try:
            bias = int((body or {}).get("prio_bias"))
        except (TypeError, ValueError):
            return {"ok": False, "msg": "prio_bias must be an integer"}
        bias = max(-100, min(100, bias))
        r = pn_req({"verb": "admin-set-prio", "target_principal": uid, "prio_bias": bias})
        _prov_log("admin.set_prio_bias", self._principal(), uid,
                  {"prio_bias": r.get("prio_bias"), "requested": bias})
        return r

    def _llm_run(self, prompt, system="", model="", timeout=120):

        return llm_run_core(prompt, system, model, timeout)

    def _llm_chat(self, body):

        jct = [("Content-Type", "application/json")]
        try:
            req = json.loads(body or "{}")
        except Exception:
            req = {}
        r = self._llm_run(req.get("prompt"), req.get("system") or "", req.get("model") or "", req.get("timeout") or 120)
        if not r["ok"]:
            out = {"ok": False, "error": r["error"]}
            if r.get("detail"):
                out["detail"] = r["detail"]
            return self.send_html(json.dumps(out), r["status"], jct)
        principal = self._principal()
        _prov_log("llm.chat", principal, "", {"reply_chars": len(r["text"]), "account": r.get("account"),
                                              "prompt_chars": len((req.get("prompt") or "")) + len((req.get("system") or ""))})
        return self.send_html(json.dumps({"ok": True, "text": r["text"], "principal": principal}), 200, jct)

    @staticmethod
    def _msgs_to_prompt(messages):

        sys_parts, convo = [], []
        for m in (messages or []):
            if not isinstance(m, dict):
                continue
            role = str(m.get("role", "user"))
            c = m.get("content", "")
            if isinstance(c, list):
                c = "\n".join(str(p.get("text", "")) for p in c if isinstance(p, dict) and p.get("type") in ("text", None))
            c = str(c)
            if role == "system":
                sys_parts.append(c)
            else:
                convo.append("%s: %s" % (role.capitalize(), c))
        return "\n".join(sys_parts), "\n\n".join(convo)

    @staticmethod
    def _map_provider_model(model):

        m = (model or "").lower()
        if m in {str(e.get("id") or "").strip().lower() for e in _models_registry()}:
            return m
        if any(t in m for t in ("haiku", "mini", "small", "fast", "flash", "nano")):
            return "haiku"
        if "opus" in m:
            return "opus"
        return "sonnet"

    def _v1_chat_completions(self, body):

        jct = [("Content-Type", "application/json")]
        try:
            req = json.loads(body or "{}")
        except Exception:
            req = {}
        model = str(req.get("model") or "")
        system, prompt = self._msgs_to_prompt(req.get("messages"))
        r = self._llm_run(prompt, system, self._map_provider_model(model), req.get("timeout") or 120)
        if not r["ok"]:
            return self.send_html(json.dumps({"error": {"message": r["error"], "type": "brainarbeit_error"}}), r["status"], jct)
        text = r["text"]
        _prov_log("llm.v1_openai", self._principal(), "", {"reply_chars": len(text)})
        pt = max(1, len(prompt) // 4); ct = max(1, len(text) // 4)
        return self.send_html(json.dumps({
            "id": "chatcmpl-" + secrets.token_hex(12), "object": "chat.completion", "created": int(time.time()),
            "model": model or "brainarbeit",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}}), 200, jct)

    def _v1_messages(self, body):

        jct = [("Content-Type", "application/json")]
        try:
            req = json.loads(body or "{}")
        except Exception:
            req = {}
        model = str(req.get("model") or "")

        will_strom = bool(req.get("stream"))
        system, prompt = self._msgs_to_prompt(req.get("messages"))
        if req.get("system"):
            system = str(req.get("system")) + (("\n" + system) if system else "")
        r = self._llm_run(prompt, system, self._map_provider_model(model), req.get("timeout") or 120)
        if not r["ok"]:
            if will_strom:

                return self.send_html(_sse_ereignis("error", {
                    "type": "error",
                    "error": {"type": "api_error", "message": r["error"]}}), r["status"], _SSE_CT)
            return self.send_html(json.dumps({"type": "error", "error": {"type": "api_error", "message": r["error"]}}), r["status"], jct)
        text = r["text"]
        _prov_log("llm.v1_anthropic", self._principal(), "", {"reply_chars": len(text)})
        pt = max(1, len(prompt) // 4); ct = max(1, len(text) // 4)
        mid = "msg_" + secrets.token_hex(12)
        if will_strom:
            return self.send_html(_anthropic_strom(mid, model or "brainarbeit", text, pt, ct),
                                  200, _SSE_CT)
        return self.send_html(json.dumps({
            "id": mid, "type": "message", "role": "assistant",
            "model": model or "brainarbeit", "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn", "usage": {"input_tokens": pt, "output_tokens": ct}}), 200, jct)

    def _v1_models(self):

        now = int(time.time())
        ids, seen = [], set()
        for mid in ["fast", "quality"] + [str(e.get("id") or "").strip() for e in _models_registry()]:
            if mid and mid not in seen:
                seen.add(mid); ids.append(mid)
        data = [{"id": mid, "object": "model", "created": now, "owned_by": "brainarbeit"}
                for mid in ids]
        return self.send_html(json.dumps({"object": "list", "data": data}), 200, [("Content-Type", "application/json")])

    def _api_voice_route_get(self):
        uid = self._principal()
        return self._sess_json({"ok": True, "session": _voice_route_load().get(_uid_safe(uid)),
                                "effective": _voice_session_for(uid), "daily": _voice_sess_name(),
                                "options": _voice_route_options(uid)})

    def _api_voice_route_set(self, raw):
        uid = self._principal()
        if not self._is_admin():
            return self._sess_json({"ok": False,
                "error": "Das flexible Zuordnen der Voice-Session ist dem Owner/Admin vorbehalten."}, code=403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        new = re.sub(r"[^A-Za-z0-9_.-]", "", str(body.get("new") or ""))[:64]
        sess = re.sub(r"[^A-Za-z0-9_.-]", "", str(body.get("session") or ""))[:64]
        target = new or sess or None
        _voice_route_set(uid, target)
        if target:
            threading.Thread(target=lambda: _voice_rotate_and_prewarm(uid), daemon=True).start()
        _prov_log("voice.route", uid, json.dumps({"session": target}), {"wire": "api"})
        return self._sess_json({"ok": True, "session": target, "effective": _voice_session_for(uid)})

    def _api_voice_reequip(self, raw):

        uid = self._principal()
        if not self._is_admin():
            return self._sess_json({"ok": False,
                "error": "Das Neu-Ausstatten der Sprachsession ist dem Owner/Admin vorbehalten."}, code=403)
        sid = _voice_session_for(uid)

        try:
            mgr = _voice_cellmgr()
        except Exception as e:
            return self._sess_json({"ok": False, "session": sid,
                                    "error": "Kein Zell-Manager erreichbar: %s" % str(e)[:160]}, code=500)
        if mgr is None:
            return self._sess_json({"ok": False, "session": sid,
                                    "error": "Auf dieser Box sind Zellen nicht verfuegbar."}, code=503)
        zelle = mgr.get(uid, sid)
        laeuft = zelle is not None and getattr(zelle, "proc", None) is not None
        try:
            if not laeuft:

                _voice_rotate_and_prewarm(uid)
                zelle = mgr.get(uid, sid)
                ok = zelle is not None and getattr(zelle, "proc", None) is not None
                wie = "gestartet"
            else:
                import pn_session_watchdog as _wd
                ok = bool(_wd.restart_now(uid, sid))
                wie = "neu gestartet"
        except Exception as e:
            return self._sess_json({"ok": False, "session": sid, "error": str(e)[:200]}, code=500)
        _prov_log("voice.reequip", uid, sid, {"ok": ok, "wie": wie, "lief_vorher": laeuft})
        if not ok:
            if not laeuft:
                grund = ("Die Zelle war aus und liess sich nicht starten. Der Zellstart nennt den "
                         "Grund im Portal-Log (haeufigste Ursache: kein Platz im Arbeitsspeicher).")
            else:
                try:
                    import pn_session_watchdog as _wd
                    knapp = not _wd._budget_ok((uid, sid), _wd._now())
                except Exception:
                    knapp = False
                if knapp:
                    grund = ("Das Neustart-Budget dieser Stunde ist aufgebraucht — die Ausstattung "
                             "greift beim naechsten regulaeren Start.")
                else:

                    z2 = mgr.get(uid, sid)
                    noch_da = z2 is not None and getattr(z2, "proc", None) is not None
                    warum = ""
                    try:
                        warum = str(z2.boot_reason() or "") if z2 is not None else ""
                    except Exception:
                        warum = ""
                    grund = (("Der Neustart ist fehlgeschlagen; die Zelle laeuft unveraendert weiter."
                              if noch_da else
                              "Der Neustart ist fehlgeschlagen und die Zelle ist jetzt AUS.")
                             + ((" Grund: " + warum[:200]) if warum else ""))
            return self._sess_json({"ok": False, "session": sid, "lief_vorher": laeuft, "error": grund})
        return self._sess_json({"ok": True, "session": sid, "wie": wie, "lief_vorher": laeuft,
                                "note": "Sprachzelle %s — Kisten, Geheimnisse und SSH-Ausstattung sind ab jetzt da." % wie})

    def _api_voice_prewarm_get(self):

        return self._sess_json({"ok": True, "mode": _voice_prewarm_mode(),
                                "modes": ["warm", "wakeword"]})

    def _api_voice_prewarm_set(self, raw):
        uid = self._principal()
        if not self._is_admin():
            return self._sess_json({"ok": False,
                "error": "Der Vorwaerm-Modus ist dem Owner/Admin vorbehalten."}, code=403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        mode = str(body.get("mode") or "")
        if mode not in ("warm", "wakeword"):
            return self._sess_json({"ok": False, "error": "mode muss 'warm' oder 'wakeword' sein."}, code=400)
        _voice_prewarm_set(mode)
        if mode == "warm":
            threading.Thread(target=lambda: _voice_rotate_and_prewarm(uid), daemon=True).start()
        _prov_log("voice.prewarm", uid, mode, {"wire": "api"})
        return self._sess_json({"ok": True, "mode": mode})

    def _api_metasessions(self):

        uid = self._principal()

        try:
            import portal_metasessions as _pm_ro
            d = _pm_ro._meta_load_ro()
        except Exception:
            d = _meta_load()
        out = []
        for msid, ms in d.items():
            if ms.get("owner") != uid and not self._is_admin():
                continue
            tpl = ms.get("template", {})
            out.append({"id": msid, "title": ms.get("title"), "state": ms.get("state"),
                        "owner": ms.get("owner"), "max_concurrent": ms.get("max_concurrent"),
                        "created": ms.get("created"), "counts": _meta_counts(ms),
                        "total": len(ms.get("tasks", [])), "lead_sid": ms.get("lead_sid"),
                        "template": {"model": tpl.get("model"), "effort": tpl.get("effort"),
                                     "autonomy": tpl.get("autonomy"), "preset": tpl.get("preset"),
                                     "vpn": tpl.get("vpn"), "vpn_dauerjob": bool(tpl.get("vpn_dauerjob"))}})
        out.sort(key=lambda m: m.get("created") or 0, reverse=True)
        return self._sess_json({"ok": True, "metasessions": out})

    def _api_metasession_get(self, msid):

        msid = re.sub(r"[^A-Za-z0-9]", "", str(msid))[:32]
        try:
            import portal_metasessions as _pm_ro
            ms = _pm_ro._meta_load_ro().get(msid)
        except Exception:
            ms = _meta_load().get(msid)
        if not self._meta_owner_ok(ms):
            return self._sess_json({"ok": False, "error": "unbekannt"}, 404)
        tasks = [{"tid": t.get("tid"), "prompt": t.get("prompt"), "state": t.get("state"),
                  "started": t.get("started"), "ended": t.get("ended"), "sid": t.get("sid"),
                  "result": t.get("result"), "error": t.get("error")} for t in ms.get("tasks", [])]
        return self._sess_json({"ok": True, "id": msid, "title": ms.get("title"),
                                "state": ms.get("state"), "max_concurrent": ms.get("max_concurrent"),
                                "template": ms.get("template", {}), "counts": _meta_counts(ms),
                                "lead_sid": ms.get("lead_sid"), "tasks": tasks})

    def _api_metasession_create(self, raw):

        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        uid = self._principal()
        title = str(body.get("title") or "Dauerjob").strip()[:120]
        tpl = body.get("template") or {}
        vpn = str(tpl.get("vpn") or "").strip() or None
        if vpn and not _uservpn_allowed(uid, vpn):
            return self._sess_json({"ok": False, "error": "VPN nicht freigeschaltet: %s" % vpn}, 403)

        vpn_dauerjob = bool(vpn) and bool(tpl.get("vpn_dauerjob"))
        template = {"model": str(tpl.get("model") or os.environ.get("PN_DEFAULT_MODEL") or "sonnet"),
                    "effort": str(tpl.get("effort") or "medium"),
                    "autonomy": max(0, min(5, int(tpl.get("autonomy", 3) or 3))),
                    "preset": str(tpl.get("preset") or "standard"),
                    "vpn": vpn, "vpn_dauerjob": vpn_dauerjob,
                    "caps": tpl.get("caps") if isinstance(tpl.get("caps"), dict) else {}}
        try:
            maxc = max(1, min(64, int(body.get("max_concurrent", 5) or 5)))
        except Exception:
            maxc = 5
        msid = secrets.token_hex(6)
        ms = {"id": msid, "title": title, "owner": uid, "state": "running",
              "template": template, "max_concurrent": maxc, "created": time.time(), "tasks": []}
        for p in (body.get("tasks") or []):
            p = str(p).strip()
            if p:
                ms["tasks"].append({"tid": _meta_new_tid(ms), "prompt": p, "state": "pending"})

        _meta_update(lambda d: d.__setitem__(msid, ms))
        _prov_log("metasession.create", uid, json.dumps({"id": msid, "n": len(ms["tasks"]), "vpn": vpn}), {"wire": "api"})
        return self._sess_json({"ok": True, "id": msid, "lead_sid": ms.get("lead_sid")})

    def _api_metasession_post(self, rest, raw):

        parts = [p for p in str(rest).split("/") if p]
        msid = re.sub(r"[^A-Za-z0-9]", "", parts[0])[:32] if parts else ""
        action = parts[1] if len(parts) > 1 else ""
        ms = _meta_load().get(msid)
        if not ms:
            return self._sess_json({"ok": False, "error": "unbekannt"}, 404)

        if not (self._meta_owner_ok(ms)
                and (self.authed() or (self._apikey_scoped_for() and action == "tasks"))):
            return self._sess_json({"ok": False, "error": "nicht erlaubt"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        if action == "tasks":
            added = []
            def _add(d):
                m = d.get(msid)
                for p in (body.get("tasks") or ([body.get("prompt")] if body.get("prompt") else [])):
                    p = str(p).strip()
                    if p:
                        tid = _meta_new_tid(m)
                        m["tasks"].append({"tid": tid, "prompt": p, "state": "pending"})
                        added.append(tid)
            _meta_update(_add)
            _prov_log("metasession.tasks", self._principal(), json.dumps({"id": msid, "n": len(added)}), {"wire": "api"})
            return self._sess_json({"ok": True, "added": len(added)})
        if action == "child-done":
            tid = str(body.get("tid") or "")
            summ = str(body.get("summary") or "")[:2000]
            marked = []
            def _cd(d):
                m = d.get(msid)
                if not m:
                    return
                for t in m.get("tasks", []):
                    if t.get("tid") == tid and t.get("state") not in ("done", "error"):
                        t["state"] = "done"; t["ended"] = time.time(); t["result"] = summ
                        marked.append(tid)
            _meta_update(_cd)

            if not marked:
                return self._sess_json({"ok": False, "sid": msid, "tid": tid,
                    "error": "Kein offener Worker mit dieser Kennung — nichts als erledigt vermerkt."}, 404)
            return self._sess_json({"ok": True, "tid": tid})
        if action in ("pause", "resume"):
            _meta_update(lambda d: d[msid].__setitem__("state", "paused" if action == "pause" else "running"))
            return self._sess_json({"ok": True, "state": "paused" if action == "pause" else "running"})
        if action == "say":

            import portal_metasessions as _pm
            tid = str(body.get("tid") or "")
            ok, why = _pm.meta_say(msid, tid, body.get("text"))
            if ok:
                return self._sess_json({"ok": True, "tid": tid})
            code = 500
            if "Kein Text" in why:
                code = 400
            elif "nicht gefunden" in why or "unbekannt" in why:
                code = 404
            elif "laeuft nicht mehr" in why or "läuft nicht mehr" in why:
                code = 409
            return self._sess_json({"ok": False, "tid": tid, "error": why}, code)
        if action == "delete":

            try:
                import portal_metasessions as _pm
                _pm.meta_stop_workers(msid)
            except Exception:
                _traceback_log("metasession delete workers")
            def _kill(d):
                m = d.get(msid, {})
                for t in m.get("tasks", []):
                    tn = t.get("tmux")
                    if tn:
                        subprocess.run(["tmux", "kill-session", "-t", tn], capture_output=True)
                if m.get("lead_sid"):
                    try:
                        subprocess.run(["tmux", "kill-session", "-t",
                                        _session_store(m.get("owner") or DEFAULT_PRINCIPAL, "cockpit").tmux_name(m["lead_sid"])],
                                       capture_output=True)
                    except Exception:
                        pass
                d.pop(msid, None)
            _meta_update(_kill)
            return self._sess_json({"ok": True, "deleted": True})
        return self._sess_json({"ok": False, "error": "unbekannte Aktion"}, 400)

    def _vext_json(self, obj, code=200):
        b = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _api_transcript(self, query):

        if _vext is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        q = urllib.parse.parse_qs(query)
        target = q.get("target", ["cockpit"])[0] or "cockpit"
        if target != "cockpit":

            return self._vext_json({"ok": False, "error": _HOSTSHELL_GONE_SPOKEN, "target": target}, 400)
        try:
            since = int(q.get("since", ["0"])[0])
        except Exception:
            since = 0
        session, asked = self._q_session(q)

        if asked and session == "meldungen":
            try:
                import portal_meldungen as _pm
                _turns = [t for t in _pm.meldungen_lesen(self._principal())
                          if int(t.get("i") or 0) >= max(0, since)]
            except Exception:
                _turns = []
            return self._vext_json({"ok": True, "turns": _turns, "target": target})
        if asked:
            if not session:
                return self._vext_json({"ok": False, "error": "Kein Session-Kennzeichen angegeben."}, 400)
            if self._sid_known(self._principal(), session, "cockpit") is False:
                try:
                    import portal_metafeatures as _mf0
                    _is_conv = _mf0.conv_known(self._principal(), session)
                except Exception:
                    _is_conv = False
                if not _is_conv:
                    return self._vext_json({"ok": False, "error": self._UNKNOWN_SID_MSG,
                                            "sid": session}, 404)

        if session:
            with contextlib.suppress(Exception):
                import portal_channels as _pc_catch
                _pc_catch.catch_up(self._principal(), session)
        payload, code = _vext.transcript(_vext_ctx(), self._principal(),
                                         target=target, since=max(0, since), session=session)

        if session and not [t for t in (payload.get("turns") or []) if (t.get("text") or "").strip()]:
            _bt = self._bus_turns(self._principal(), session, since=max(0, since))

            if _bt:
                payload = {**payload, "turns": _bt,
                           "next": (max(t.get("i", 0) for t in _bt) + 1)}

                payload.pop("note", None)
        if q.get("tts", ["0"])[0] in ("1", "true", "yes"):
            asst = [t for t in payload.get("turns", []) if t.get("role") == "assistant" and t.get("text")]
            if asst:
                wav = _vext_tts(asst[-1]["text"])
                if wav:
                    payload["tts_b64"] = base64.b64encode(wav).decode("ascii")
        return self._vext_json(payload, code)
