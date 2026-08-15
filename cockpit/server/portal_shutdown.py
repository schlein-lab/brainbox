
import json
import os
import subprocess
import time

DATA_DIR = None
_prov_log = None

_HELPER_NAME = "pn-shutdown"

_HELPER_ROOT = "/usr/local/sbin/pn-shutdown"
_APPROVAL_TTL = 180
_EARLY_FAIL_S = 2.5

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

class ShutdownRoutes:
    def _shd_json(self, obj, code=200):
        return self.send_html(json.dumps(obj, ensure_ascii=False), code,
                              [("Content-Type", "application/json")])

    @staticmethod
    def _shd_helper_path():

        cands = [_HELPER_ROOT,
                 os.path.expanduser("~/.local/bin/" + _HELPER_NAME),
                 os.path.join(os.path.dirname(os.path.realpath(__file__)), _HELPER_NAME)]
        for c in cands:
            if os.path.isfile(c) and os.access(c, os.X_OK):
                return c
        return None

    @staticmethod
    def _shd_sudo_lane(helper):

        if os.geteuid() == 0:
            return [], "root"
        for cand in dict.fromkeys([_HELPER_ROOT, helper]):
            if not (cand and os.path.isfile(cand)):
                continue
            try:
                r = subprocess.run(["sudo", "-n", "-l", cand],
                                   capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    return ["sudo", "-n", cand], "sudo:" + cand
            except (OSError, subprocess.TimeoutExpired):
                pass
        return None, "keine sudo-Regel"

    def _shd_has_2fa(self):

        try:
            reg = self._relay_registry()
            cx = reg.connect()
            return bool(reg.has_2fa(cx, "win-thin"))
        except Exception:
            return False

    def _api_admin_shutdown_status(self):

        if not self._is_admin():
            return self._shd_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        helper = self._shd_helper_path()
        lane, why = self._shd_sudo_lane(helper) if helper else (None, "Helfer fehlt")
        return self._shd_json({
            "ok": True,
            "helper": helper,
            "lane": (why if lane is not None else None),
            "ready": bool(helper and lane is not None),
            "reason": (None if (helper and lane is not None) else
                       ("Das Werkzeug pn-shutdown fehlt auf dieser Box." if not helper else
                        "Es fehlt die sudo-Regel fuer pn-shutdown (Root-Lane) — Herunterfahren "
                        "aus dem Portal ist damit nicht moeglich.")),
            "orderly_pid1": os.path.exists("/run/pn-init/shutdown.v2"),
            "totp_required": self._shd_has_2fa(),
        })

    def _api_admin_shutdown(self, raw):

        if not self._is_admin():
            return self._shd_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return self._shd_json({"ok": False, "error": "Anfrage unlesbar — nichts passiert."}, 400)
        mode = str(body.get("mode") or "poweroff")
        if mode not in ("poweroff", "reboot"):
            return self._shd_json({"ok": False,
                                   "error": "mode muss poweroff oder reboot sein — nichts passiert."}, 400)
        verb_de = "Neustart" if mode == "reboot" else "Herunterfahren"
        action = "shutdown:" + mode

        helper = self._shd_helper_path()
        if not helper:
            return self._shd_json({"ok": False, "error":
                "Das Werkzeug pn-shutdown fehlt auf dieser Box — %s ist nicht moeglich, "
                "es wurde nichts ausgefuehrt." % verb_de}, 503)

        if body.get("dry_run"):
            try:
                r = subprocess.run([helper, "--dry-run"] + (["--reboot"] if mode == "reboot" else []),
                                   capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired) as e:
                return self._shd_json({"ok": False, "error":
                    "pn-shutdown --dry-run liess sich nicht starten: %s" % e}, 500)
            return self._shd_json({"ok": r.returncode == 0, "dry_run": True, "mode": mode,
                                   "plan": (r.stdout or "") + (r.stderr or "")},
                                  200 if r.returncode == 0 else 500)

        totp_required = self._shd_has_2fa()

        aid = str(body.get("approval") or "").strip()
        if not aid:
            aid = self._approval_create(action,
                                        detail="%s der ganzen Box ueber das Portal" % verb_de,
                                        ttl=_APPROVAL_TTL)
            return self._shd_json({
                "ok": False, "need_approval": True, "approval": aid, "mode": mode,
                "totp_required": totp_required,
                "message": ("%s angefragt. Zum Bestaetigen bitte den Handy-Code (2FA) eingeben."
                            % verb_de) if totp_required else
                           ("%s angefragt. Es ist KEIN 2FA/Handy-Code eingerichtet — die "
                            "Bestaetigung laeuft daher nur ueber diesen Dialog. (Empfehlung: "
                            "unter Freigaben einen zweiten Faktor einrichten.)" % verb_de)})

        r = self._approval_load(aid)
        if not r:
            return self._shd_json({"ok": False, "error":
                "Diese Freigabe gibt es nicht (mehr) — nichts wurde ausgefuehrt."}, 404)
        rec, path = r
        if rec.get("action") != action:
            return self._shd_json({"ok": False, "error":
                "Die Freigabe gehoert zu einer anderen Aktion (%s) — nichts wurde ausgefuehrt."
                % rec.get("action")}, 409)
        age = int(time.time()) - int(rec.get("created", 0))
        if rec.get("status") not in ("pending", "approved") or age > int(rec.get("ttl", _APPROVAL_TTL)):
            return self._shd_json({"ok": False, "error":
                "Die Freigabe ist abgelaufen oder verbraucht (%s) — bitte neu anstossen; "
                "nichts wurde ausgefuehrt." % rec.get("status")}, 409)

        if totp_required and rec.get("status") != "approved":
            vok, reason = self._verify_winthin_totp(str(body.get("totp") or "").strip())
            if not vok:
                return self._shd_json({"ok": False, "need_totp": True, "error":
                    "2FA-Code fehlt oder ist ungueltig (%s) — nichts wurde ausgefuehrt." % reason}, 403)

        lane, why = self._shd_sudo_lane(helper)
        if lane is None:
            return self._shd_json({"ok": False, "error":
                "Keine Root-Lane fuer pn-shutdown (sudo-Regel fehlt auf dieser Box) — "
                "%s ist aus dem Portal nicht moeglich, es wurde nichts ausgefuehrt. "
                "Am Geraet geht: sudo pn-shutdown%s"
                % (verb_de, " --reboot" if mode == "reboot" else "")}, 503)

        rec.update({"status": "executed", "decided_by": self._principal(),
                    "decided_ts": int(time.time())})
        try:
            open(path, "w").write(json.dumps(rec))
        except OSError:
            pass

        cmd = (lane + ["--yes"]) if lane else [helper, "--yes"]
        if mode == "reboot":
            cmd.append("--reboot")
        logp = os.path.join(DATA_DIR, "shutdown-helper.log")
        try:
            lf = open(logp, "ab")
            lf.write(("\n==== %s %s durch %s ====\n"
                      % (time.strftime("%Y-%m-%d %H:%M:%S"), mode, self._principal())).encode())
            proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        except OSError as e:
            _prov_log("admin.shutdown.failed", self._principal(), mode, {"error": str(e)})
            return self._shd_json({"ok": False, "error":
                "pn-shutdown liess sich nicht starten (%s) — nichts wurde ausgefuehrt." % e}, 500)
        time.sleep(_EARLY_FAIL_S)
        rc = proc.poll()
        if rc is not None and rc != 0:
            tail = ""
            try:
                with open(logp, "rb") as f:
                    tail = f.read()[-800:].decode("utf-8", "replace")
            except OSError:
                pass
            _prov_log("admin.shutdown.failed", self._principal(), mode, {"rc": rc, "tail": tail[-300:]})
            return self._shd_json({"ok": False, "error":
                "pn-shutdown wurde gestartet, brach aber sofort ab (rc=%d) — die Box laeuft "
                "weiter. Ausgabe: %s" % (rc, tail.strip() or "(keine)")}, 502)
        _prov_log("admin.shutdown", self._principal(), mode,
                  {"approval": aid, "lane": why, "verified_2fa": bool(totp_required)})
        return self._shd_json({"ok": True, "initiated": True, "mode": mode,
                               "message": ("%s eingeleitet — pn-shutdown laeuft (Lane: %s). Die Box "
                                           "stoppt jetzt alle Dienste geordnet; diese Verbindung "
                                           "bricht gleich ab." % (verb_de, why))})
