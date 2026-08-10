#!/usr/bin/env python3

from __future__ import annotations

import base64
import json
import os
import re
import time
from pn_cell_basis import (
    AGENTS_CA_GUEST,
    AGENTS_GEMINI_GUEST,
    AGENTS_LIB_GUEST,
    AGENTS_NODE_GUEST,
    AGENTS_OPENCODE_GUEST,
    BIOMNI_ENTRY_SRC,
    CODEX_BIN_GUEST,
    CODEX_CA_GUEST,
    CODEX_PATH_DIR_GUEST)

class CellLaufzeitenMixin:

    _codex_probe = ""

    def _codex_home_candidates(self):

        cands = []
        try:
            import glob as _glob
            for cfg in _glob.glob(os.path.expanduser("~/.config/*/llmpool.json")):
                try:
                    with open(cfg, encoding="utf-8") as f:
                        d = json.load(f)
                    accts = d.get("accounts") if isinstance(d, dict) else (d if isinstance(d, list) else [])
                    for a in (accts or []):
                        if str(a.get("provider") or "").lower() == "codex" and a.get("home"):
                            cands.append(os.path.join(os.path.expanduser(a["home"]), ".codex"))
                except Exception:
                    continue
        except Exception:
            pass
        cands.append(os.path.expanduser("~/.llmpool/codex/.codex"))
        cands.append(os.path.expanduser("~/.codex"))
        seen = set(); out = []
        for c in cands:
            if c not in seen:
                seen.add(c); out.append(c)
        return out

    def _codex_auth_source(self):

        for base in self._codex_home_candidates():
            p = os.path.join(base, "auth.json")
            if os.path.exists(p):
                return p
        return None

    def _setup_codex(self):

        self._run("busybox mount -o ro /dev/vdc /work 2>/dev/null; "
                  "echo CODEX_MNT bin=$(busybox ls %s 2>/dev/null); echo __PS__" % CODEX_BIN_GUEST,
                  "__PS__", 25)

        self._run("busybox mkdir -p /root/.codex && "
                  "busybox mount -t tmpfs -o size=64m,mode=700 tmpfs /root/.codex 2>/dev/null; "
                  "busybox chmod 700 /root/.codex; echo __PS__", "__PS__", 10)

        if self.tap is not None:
            self._run("busybox ip addr add 10.77.%d.2/30 dev eth0 2>/dev/null; "
                      "busybox ip link set eth0 up 2>/dev/null; "
                      "busybox ip route add default via 10.77.%d.1 2>/dev/null; "
                      "echo CODEX_NIC $(busybox ip -o addr show eth0 2>/dev/null | busybox awk '{print $4}'); "
                      "echo __PS__" % (self.cid, self.cid), "__PS__", 12)
        src = self._codex_auth_source()
        if not src:
            self._log("codex: kein auth.json gefunden (pool codex HOME / portal HOME) — in Admin->LLM verbinden")
            return
        try:
            with open(src, "rb") as f:
                ab64 = base64.b64encode(f.read()).decode()
        except OSError as e:
            self._log("codex: auth-Quelle nicht lesbar (%s)" % e)
            return
        self._run("printf %%s '%s' | base64 -d > /root/.codex/auth.json && "
                  "busybox chmod 600 /root/.codex/auth.json && echo __PS__" % ab64, "__PS__", 12)
        self._log("codex: auth.json injiziert aus %s (RAM-tmpfs, chmod 600)" % src)

        cfgb = base64.b64encode(b'[projects."/root"]\ntrust_level = "trusted"\n').decode()
        self._run("printf %%s '%s' | base64 -d > /root/.codex/config.toml && echo __PS__"
                  % cfgb, "__PS__", 10)

    def _codex_runnable(self):

        try:
            _ok, out = self._run("HOME=/root %s --version 2>&1 | head -2; echo __CXV__" % CODEX_BIN_GUEST,
                                 "__CXV__", 30)
            self._codex_probe = " ".join((out or "").split("__CXV__")[0].split())[:200]
            return bool(re.search(r"\d+\.\d+\.\d+", self._codex_probe))
        except Exception as e:
            self._codex_probe = str(e)
            return False

    def _codex_err_tail(self, limit=400):

        try:
            _ok, out = self._run("busybox tail -c %d /tmp/codex.err 2>/dev/null; echo __CXE__" % int(limit),
                                 "__CXE__", 10)
            t = " ".join((out or "").split("__CXE__")[0].split())
            return (" Der Agent meldet: " + t[:300]) if t else ""
        except Exception:
            return ""

    def _codex_launch_cmd(self):

        pol = self.policy or {}
        m = str(pol.get("model") or "").strip()
        model = ("-m %s " % m) if re.match(r"^[A-Za-z0-9._-]+$", m) else ""
        return ("cd /root; export HOME=/root CODEX_HOME=/root/.codex PATH=%s:$PATH; "
                "[ -f %s ] && export SSL_CERT_FILE=%s; "

                "if [ -e /sys/class/net/eth0 ]; then unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy; fi; "
                "exec %s --dangerously-bypass-approvals-and-sandbox %s2>/tmp/codex.err"
                % (CODEX_PATH_DIR_GUEST, CODEX_CA_GUEST, CODEX_CA_GUEST, CODEX_BIN_GUEST, model))

    def _setup_kits(self):

        for kid, dev in (getattr(self, "_kit_mounts", None) or []):
            mp = "/opt/kits/" + kid

            self._run("busybox mkdir -p %s && busybox mount -o ro /dev/%s %s 2>/dev/null; "
                      "if [ -d %s/lib ]; then busybox mkdir -p /usr/lib /lib; "
                      "busybox cp -a %s/lib/. /usr/lib/ 2>/dev/null; "
                      "busybox cp -a %s/lib/. /lib/ 2>/dev/null; fi; "
                      "echo KIT_MNT %s=$(busybox ls %s/bin 2>/dev/null | busybox wc -w); echo __KM__"
                      % (mp, dev, mp, mp, mp, mp, kid, mp), "__KM__", 25)
        self._stage_kit_cards()

    def _stage_kit_cards(self):

        try:
            import pn_software_shelf as _shelf
        except Exception:
            return
        for kid, _dev in (getattr(self, "_kit_mounts", None) or []):
            try:
                rec = _shelf.card_get(kid) or {}
                progs = ((rec.get("manual") or {}).get("programs")) or []
                if not progs:
                    continue
                C = ["# Werkzeug-Kiste: %s" % kid, "",
                     "Gemountet unter `/opt/kits/%s/bin/`. Bereits von einem Agenten erkundet — die" % kid,
                     "Kommandos unten wurden real ausgefuehrt und verifiziert, du musst nichts neu",
                     "ausprobieren.", ""]
                n = 0
                for prog in progs:
                    recipes = [r for r in (prog.get("recipes") or []) if r.get("verified")]
                    if not recipes:
                        continue
                    n += 1
                    C += ["## %s (%s)" % (prog.get("name", "?"), prog.get("modality", "cli")),
                          str(prog.get("purpose") or "").strip()]
                    caps = prog.get("capabilities") or []
                    if caps:
                        C += ["Kann: " + "; ".join(str(c) for c in caps)]
                    C += ["Bewaehrte Bedienwege:"]
                    for r in recipes:
                        C += ["- %s:" % str(r.get("goal") or "").strip(),
                              "  `%s`" % str(r.get("command") or "").strip()]
                    C += [""]
                if not n:
                    continue
                body = "\n".join(C)
                b64 = base64.b64encode(body.encode()).decode()
                fn = "KITS/%s.md" % kid.replace("/", "_")
                self._run("busybox mkdir -p /root/KITS 2>/dev/null; printf %%s '%s' | base64 -d > /root/%s; "
                          "busybox grep -q '@%s' /root/CLAUDE.md 2>/dev/null || "
                          "printf '\\n@%s\\n' >> /root/CLAUDE.md; echo __KC__"
                          % (b64, fn, fn, fn), "__KC__", 12)
                self._log("kit-cards: %s (%d Programme) in die Zelle gestaged" % (kid, n))
            except Exception as e:
                self._log("kit-cards %s: %s" % (kid, e))

    def _setup_agents(self):

        self._run("busybox mount -o ro /dev/vdc /work 2>/dev/null; "
                  "echo AGENTS_MNT node=$(busybox ls %s 2>/dev/null) oc=$(busybox ls %s 2>/dev/null); "
                  "echo __PS__" % (AGENTS_NODE_GUEST, AGENTS_OPENCODE_GUEST), "__PS__", 25)
        if self.tap is not None:
            self._run("busybox ip addr add 10.77.%d.2/30 dev eth0 2>/dev/null; "
                      "busybox ip link set eth0 up 2>/dev/null; "
                      "busybox ip route add default via 10.77.%d.1 2>/dev/null; "
                      "echo AGENTS_NIC $(busybox ip -o addr show eth0 2>/dev/null | busybox awk '{print $4}'); "
                      "echo __PS__" % (self.cid, self.cid), "__PS__", 12)
        if (self.policy or {}).get("runtime") != "gemini":
            return

        self._run("busybox mkdir -p /root/.gemini && "
                  "busybox mount -t tmpfs -o size=16m,mode=700 tmpfs /root/.gemini 2>/dev/null; "
                  "busybox chmod 700 /root/.gemini; echo __PS__", "__PS__", 10)
        injected = []
        home = os.path.expanduser("~")
        for fn in ("oauth_creds.json", "google_accounts.json", "settings.json", ".env"):
            src = os.path.join(home, ".gemini", fn)
            if not os.path.exists(src):
                continue
            try:
                with open(src, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
            except OSError:
                continue
            self._run("printf %%s '%s' | base64 -d > /root/.gemini/%s && "
                      "busybox chmod 600 /root/.gemini/%s && echo __PS__" % (b64, fn, fn), "__PS__", 12)
            injected.append(fn)
        if injected:
            self._log("gemini: Credentials injiziert (%s) — RAM-tmpfs" % ", ".join(injected))
        else:
            self._log("gemini: keine Credentials auf der Box (~/.gemini) — in Admin->LLM-Pool verbinden")

    def _agents_runnable(self, which):

        if which == "gemini":
            cmd = ("HOME=/root LD_LIBRARY_PATH=%s %s %s --version 2>&1 | head -2; echo __AGV__"
                   % (AGENTS_LIB_GUEST, AGENTS_NODE_GUEST, AGENTS_GEMINI_GUEST))
        else:
            cmd = ("HOME=/root LD_LIBRARY_PATH=%s %s --version 2>&1 | head -2; echo __AGV__"
                   % (AGENTS_LIB_GUEST, AGENTS_OPENCODE_GUEST))
        try:
            _ok, out = self._run(cmd, "__AGV__", 40)
            self._agents_probe = " ".join((out or "").split("__AGV__")[0].split())[:200]
            return bool(re.search(r"\d+\.\d+", self._agents_probe))
        except Exception as e:
            self._agents_probe = str(e)
            return False

    def _agents_err_tail(self, limit=400):
        try:
            _ok, out = self._run("busybox tail -c %d /tmp/agent.err 2>/dev/null; echo __AGE__" % int(limit),
                                 "__AGE__", 10)
            t = " ".join((out or "").split("__AGE__")[0].split())
            return (" Der Agent meldet: " + t[:300]) if t else ""
        except Exception:
            return ""

    def _gemini_launch_cmd(self):

        pol = self.policy or {}
        m = str(pol.get("model") or "").strip()
        model = ("-m %s " % m) if re.match(r"^[A-Za-z0-9._-]+$", m) else ""
        return ("cd /root; export HOME=/root LD_LIBRARY_PATH=%s NODE_EXTRA_CA_CERTS=%s; "
                "if [ -e /sys/class/net/eth0 ]; then unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy; fi; "
                "exec %s %s --yolo %s2>/tmp/agent.err"
                % (AGENTS_LIB_GUEST, AGENTS_CA_GUEST, AGENTS_NODE_GUEST, AGENTS_GEMINI_GUEST, model))

    def _ollama_launch_cmd(self):

        pol = self.policy or {}
        base = str(pol.get("ollama_base") or "").strip() or "http://127.0.0.1:11434"
        m = str(pol.get("model") or pol.get("ollama_model") or "").strip()
        if not re.match(r"^[A-Za-z0-9._:/-]+$", m):
            m = ""
        cfg = {"$schema": "https://opencode.ai/config.json",
               "provider": {"ollama": {"npm": "@ai-sdk/openai-compatible", "name": "Ollama (Box)",
                                        "options": {"baseURL": base.rstrip("/") + ("" if base.rstrip("/").endswith("/v1") else "/v1")},
                                        "models": ({m: {"name": m}} if m else {})}}}
        if m:
            cfg["model"] = "ollama/" + m
        cb64 = base64.b64encode(json.dumps(cfg).encode()).decode()

        return ("cd /root; export HOME=/root LD_LIBRARY_PATH=%s NODE_EXTRA_CA_CERTS=%s "
                "no_proxy=127.0.0.1,localhost NO_PROXY=127.0.0.1,localhost; "
                "busybox mkdir -p /root/.config/opencode; "
                "printf %%s '%s' | base64 -d > /root/.config/opencode/opencode.json; "

                "busybox rm -rf /root/.config/opencode/node_modules 2>/dev/null; "
                "exec %s 2>/tmp/agent.err"
                % (AGENTS_LIB_GUEST, AGENTS_CA_GUEST, cb64, AGENTS_OPENCODE_GUEST))

    def _setup_biomni(self):

        self._run("busybox mount -o ro /dev/vdc /work 2>/dev/null; "
                  "busybox mkdir -p /root/biomni-lake /root/bd && "
                  "busybox mount -o ro /dev/vdd /root/biomni-lake 2>/dev/null; "
                  "( [ -d /root/biomni-lake/data_lake ] && busybox ln -sfn /root/biomni-lake/data_lake /root/bd/data_lake ) "
                  "|| busybox ln -sfn /root/biomni-lake /root/bd/data_lake; "
                  "echo BIOMNI_MNT site=$(busybox ls -d /work/biomni-site 2>/dev/null) "
                  "libs=$(busybox ls -d /work/biomni-libs 2>/dev/null); echo __PS__", "__PS__", 25)
        try:
            with open(BIOMNI_ENTRY_SRC, "rb") as f:
                eb64 = base64.b64encode(f.read()).decode()
            self._run("busybox mkdir -p /opt/pn && printf %%s '%s' | base64 -d > /opt/pn/biomni_entry.py && echo __PS__"
                      % eb64, "__PS__", 20)
        except OSError:
            pass

    def ask_biomni(self, prompt, timeout=600):

        with self._lock:
            if not self.alive() and not self.boot():
                return "(Cell konnte nicht starten.)"
            b64 = base64.b64encode(prompt.encode()).decode()
            n = self.turns + 1
            mark = "__BIO%d__" % n
            script = (
                "cd /root && P=$(printf %%s '%s' | base64 -d); "
                "PYTHONPATH=/work/biomni-site LD_LIBRARY_PATH=/work/biomni-libs:/lib:/lib64 "
                "/bin/python3 /opt/pn/biomni_entry.py \"$P\" >/tmp/bio%d.out 2>/tmp/bio%d.err; RC=$?; "
                "echo '%sSTART'; busybox cat /tmp/bio%d.out 2>/dev/null; echo; echo \"%sEND$RC\""
                % (b64, n, n, mark, n, mark))
            ok, out = self._run(script, mark + "END", timeout)
            if ok:
                self.turns = n
                self.last = time.time()
                body = out.split(mark + "START", 1)[1] if (mark + "START") in out else out
                if "BIOMNI_OUT_BEGIN" in body and "BIOMNI_OUT_END" in body:
                    body = body.split("BIOMNI_OUT_BEGIN", 1)[1].split("BIOMNI_OUT_END", 1)[0]
                return body.strip() or "(leere Antwort)"
            self._teardown(reboot=False)
            return "(Keine Antwort aus der Biomni-Cell.)"
