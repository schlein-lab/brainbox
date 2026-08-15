#!/usr/bin/env python3

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
from pn_cell_basis import (
    KALT_REPL_WARTEN,
    KALT_STILL_HART,
    TERM_INCELL_SRC,
    TERM_RELAUNCH_MAX,
    TERM_RELAUNCH_WINDOW_S,
    TERM_START_WAIT_S,
    _VOICE_META_ARTIFACTS,
    _cli_disallowed,
    _is_injected_user_text,
    _render_prompt_tool,
    _speakable,
    llm_lane_reason)
from pn_cell_gastquellen import CLAUDE_LAUNCH_TMPL, REMOTE_CLAUDE_LAUNCH_TMPL

REPL_FRISCH_S = float(os.environ.get("PN_CELL_REPL_FRESH_S", "120"))

class CellTranscriptMixin:

    def _repl_prozess_da(self):

        rt = (self.policy or {}).get("runtime")
        muster = {"codex": "[c]odex/bin/codex",
                  "gemini": "[a]gents/",
                  "ollama": "[a]gents/"}.get(rt, "[/]bin/claude")
        try:
            ok, out = self._run("busybox ps | busybox grep -q '%s' && echo REPLDA || echo REPLNOCH; "
                                "echo __RP__" % muster, "__RP__", 8)
            return True if not ok else ("REPLDA" in out)
        except Exception:
            return True

    def _repl_abwarten(self, frist=None):

        frist = KALT_REPL_WARTEN if frist is None else frist
        t0 = time.time()
        while time.time() - t0 < frist:
            if self._repl_prozess_da():
                return True
            time.sleep(1.0)
        sys.stderr.write("[pn-session] %s: Agent war nach %.0fs noch nicht da — es wird trotzdem "
                         "getippt (die Abgabe wird danach nachgewiesen)\n" % (self.session, frist))
        return False

    def term_runner_alive(self):

        try:
            _ok, _o = self._run("busybox ps | busybox grep -q '[p]n_term_incell' "
                                "&& echo TERMALIVE || echo TERMDEAD; echo __PRB__", "__PRB__", 8)
            if not _ok:
                return True
            return "TERMALIVE" in _o
        except Exception:
            return True

    def _incell_pkill(self, pattern, timeout=12):

        if not pattern:
            return (False, "")
        pat = "[" + pattern[0] + "]" + pattern[1:]
        return self._run("for p in $(busybox ps | busybox grep '%s' | busybox awk '{print $1}'); do "
                         "busybox kill -9 \"$p\" 2>/dev/null; done; echo __PK__" % pat, "__PK__", timeout)

    def seat_echo(self):

        try:
            return self._run("echo __WD__", "__WD__", 5)[0]
        except Exception:
            return False

    def _brief_liegt_in_der_zelle(self):

        try:
            ok, out = self._run("test -s /opt/pn/voice-sys.md && echo JA || echo NEIN; echo __BR__",
                                "__BR__", 8)
            return "JA" in out.split("__BR__")[0]
        except Exception:
            return False

    def start_terminal(self, cmd=None, cols=120, rows=40, system=None):

        with self._lock:
            if not self.alive() or self.term_conn is None:
                self._term_denied = self.boot_reason() or "Die Zelle hat keine Terminal-Lane."
                return False
            self._term_system = system if system is not None else self._term_system
            runtime = (self.policy or {}).get("runtime")
            is_codex = (cmd is None and runtime == "codex")
            is_agent = (cmd is None and runtime in ("gemini", "ollama"))
            if self.term_on:

                if self.term_runner_alive():
                    return True
                self.term_on = False

            _now = time.time()
            self._term_launches = [t for t in self._term_launches if _now - t < TERM_RELAUNCH_WINDOW_S]
            if len(self._term_launches) >= TERM_RELAUNCH_MAX:
                _lane = "" if (is_codex or is_agent) else (llm_lane_reason() or "")
                _tail = (self._codex_err_tail() if is_codex
                         else self._agents_err_tail() if is_agent else self._claude_err_tail())
                self._term_denied = (_lane
                                     or ("Der Agent in der Zelle beendet sich sofort wieder (%d Starts in "
                                         "%ds).%s" % (len(self._term_launches), TERM_RELAUNCH_WINDOW_S,
                                                      _tail)))
                return False

            try:

                self._incell_pkill("pn_term_incell")
                self._incell_pkill("/bin/claude")
                if is_codex:
                    self._incell_pkill("codex/bin/codex")
                if is_agent:
                    self._incell_pkill("agents/node")
                    self._incell_pkill("agents/opencode")
                self._run("busybox sleep 0.4; echo __DDUP__", "__DDUP__", 8)
            except Exception:
                pass
            is_claude = (not is_codex) and (not is_agent) and (cmd is None or "claude" in cmd)
            if cmd is None and is_agent:

                which = "gemini" if runtime == "gemini" else "opencode"
                if not self._agents_runnable(which):
                    self._term_denied = ("Der Agent (%s) ist in der Zelle nicht lauffähig: %s"
                                         % (runtime, self._agents_probe or "keine Version gemeldet"))
                    return False
                cmd = self._gemini_launch_cmd() if runtime == "gemini" else self._ollama_launch_cmd()
            elif cmd is None and is_codex:

                if not self._codex_runnable():
                    self._term_denied = ("Der Agent (codex) ist in der Zelle nicht lauffaehig: %s"
                                         % (self._codex_probe or "keine Version gemeldet"))
                    return False
                cmd = self._codex_launch_cmd()
            elif cmd is None:

                pol = self.policy or {}
                def _flag(name, val):
                    v = str(val or "").strip()
                    return ("%s %s " % (name, v)) if v and re.match(r"^[A-Za-z0-9._-]+$", v) else ""
                ex = _flag("--model", pol.get("model")) + _flag("--effort", pol.get("effort"))
                dt = _cli_disallowed(pol.get("disallowed_tools"))
                if dt:
                    ex += "--disallowedTools %s " % ",".join(dt)
                if pol.get("phantom") in ("allow", "ask"):

                    ex += "--mcp-config /etc/pn/phantom.mcp.json "

                if not system and self._term_system:
                    system = self._term_system
                if system:

                    _sysb = base64.b64encode(system.encode()).decode()
                    self._run("busybox mkdir -p /opt/pn; printf %%s '%s' | base64 -d > /opt/pn/voice-sys.md "
                              "&& echo __PS__" % _sysb, "__PS__", 12)
                    ex += "--append-system-prompt-file /opt/pn/voice-sys.md "
                elif self._brief_liegt_in_der_zelle():
                    ex += "--append-system-prompt-file /opt/pn/voice-sys.md "

                if self._remote_node():
                    cmd = REMOTE_CLAUDE_LAUNCH_TMPL % ex
                else:
                    cmd = CLAUDE_LAUNCH_TMPL % (ex, ex, ex)
            if is_claude and not self._claude_runnable():

                self._term_denied = ("Der Agent (claude) ist in der Zelle nicht lauffaehig: %s"
                                     % (self._claude_probe or "keine Version gemeldet"))
                return False
            try:
                with open(TERM_INCELL_SRC, "rb") as f:
                    tb64 = base64.b64encode(f.read()).decode()
            except OSError:
                self._term_denied = ("Der In-Cell-Terminal-Runner fehlt auf der Box: %s" % TERM_INCELL_SRC)
                return False

            self._run("busybox mkdir -p /opt/pn /dev/pts; "
                      "busybox mount -t devpts -o mode=0620,ptmxmode=0666 devpts /dev/pts 2>/dev/null; "
                      "[ -e /dev/pts/ptmx ] && busybox ln -sf /dev/pts/ptmx /dev/ptmx; "
                      "printf %%s '%s' | base64 -d > /opt/pn/pn_term_incell.py && echo __PS__"
                      % tb64, "__PS__", 20)
            if is_claude:
                self._seed_claude_onboarding()
            safe = cmd.replace("'", "'\\''")
            self._term_launches.append(time.time())
            self._run("PN_TERM_CMD='%s' PN_TERM_COLS=%d PN_TERM_ROWS=%d /bin/python3 /opt/pn/pn_term_incell.py "
                      ">/tmp/pnterm.out 2>&1 & busybox sleep 1; echo __PS__" % (safe, cols, rows), "__PS__", 15)

            _t0 = time.time()
            _up = False
            while time.time() - _t0 < TERM_START_WAIT_S:
                if self.term_runner_alive():
                    _up = True
                    break
                time.sleep(1.0)
            if not _up:
                _lane = "" if is_codex else (llm_lane_reason() or "")
                _tail = self._codex_err_tail() if is_codex else self._claude_err_tail()
                self._term_denied = (_lane
                                     or ("Der Agent in der Zelle startete nicht (%ds).%s"
                                         % (TERM_START_WAIT_S, _tail)))
                self.term_on = False
                return False
            self._term_denied = None
            self.term_on = True
            return True

    _claude_probe = ""

    def _claude_runnable(self):

        _budget = float(os.environ.get("PN_CELL_PROBE_S", "90"))
        _kam_durch = False
        for _versuch in (1, 2):
            try:
                _ok, out = self._run(
                    "IS_SANDBOX=1 HOME=/root /bin/claude --version 2>&1 | head -2; echo __CVP__",
                    "__CVP__", _budget)
                self._claude_probe = " ".join((out or "").split("__CVP__")[0].split())[:200]
                if re.search(r"\d+\.\d+\.\d+", self._claude_probe):
                    return True
                if _ok:

                    _kam_durch = True
                    break
            except Exception as e:
                self._claude_probe = str(e)
        if not _kam_durch:

            self._claude_probe = (
                "Zeitueberschreitung: die Zelle hat auf die Versionsabfrage zweimal nicht "
                "innerhalb von %ds geantwortet. Das ist eine Aussage ueber die LAST des Knotens, "
                "nicht ueber das Abbild — vermutlich rechnen dort zu viele Zellen gleichzeitig."
                % int(_budget))
        return False

    def _claude_err_tail(self, limit=300):

        try:
            _ok, out = self._run("busybox tail -c %d /tmp/claude.err 2>/dev/null; echo __CE__" % int(limit),
                                 "__CE__", 10)
            t = " ".join((out or "").split("__CE__")[0].split())
            return (" Der Agent meldet: " + t[:300]) if t else ""
        except Exception:
            return ""

    def term_reason(self):

        if self.term_on and self.alive():
            return None
        return self._term_denied or self.boot_reason()

    def _incell_runtime(self):

        return (self.policy or {}).get("runtime")

    @staticmethod
    def _convo_turns(ev):

        out = []
        if not isinstance(ev, dict):
            return out
        if ev.get("type") == "event_msg":
            pl = ev.get("payload") or {}
            pt = pl.get("type")
            role = "user" if pt == "user_message" else ("assistant" if pt == "agent_message" else None)
            if role is not None:
                t = str(pl.get("message") or "").strip()
                if t:

                    out.append({"role": role, "text": t, "ts": ev.get("timestamp"), "model": None})
            return out
        typ = ev.get("type")
        if typ in ("user", "assistant") and not ev.get("isMeta") and not ev.get("isSidechain"):
            content = (ev.get("message") or {}).get("content")
            parts = []
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        parts.append(blk.get("text") or "")

            if typ == "assistant" and isinstance(content, list):
                for blk in content:
                    if not (isinstance(blk, dict) and blk.get("type") == "tool_use"):
                        continue
                    rendered = _render_prompt_tool(blk.get("name"), blk.get("input"))
                    if rendered:
                        parts.append(rendered)
            t = "\n".join(p for p in parts if p).strip()
            if t:

                _mdl = (ev.get("message") or {}).get("model") if typ == "assistant" else None
                if _mdl and str(_mdl).startswith("<"):
                    _mdl = None
                out.append({"role": typ, "text": t, "ts": ev.get("timestamp"), "model": _mdl})
        return out

    def _incell_active_jsonl(self):

        if self._incell_runtime() == "codex":

            ok, out = self._run("ls -1t /root/.codex/sessions/*/*/*/rollout-*.jsonl 2>/dev/null "
                                "| head -1; echo __J__", "__J__", 12)
            lines = [l for l in out.split("__J__")[0].splitlines() if l.strip().endswith(".jsonl")]
            return lines[0].strip() if lines else None

        ok, out = self._run("ls -1t /root/.claude/projects/*/*.jsonl 2>/dev/null "
                            "| busybox grep -v -e '-root--obs' -e '-root-.obs' -e '-root-w[0-9]' "
                            "| head -1; echo __J__",
                            "__J__", 12)
        lines = [l for l in out.split("__J__")[0].splitlines() if l.strip().endswith(".jsonl")]
        return lines[0].strip() if lines else None

    _AGENT_SLOT_MAX = 8

    def _agent_slot_ok(self, slot):
        try:
            n = int(slot)
        except (TypeError, ValueError):
            return None
        return n if 2 <= n <= self._AGENT_SLOT_MAX else None

    def agent_dir(self, slot):

        n = self._agent_slot_ok(slot)
        return ("/root/w%d" % n) if n else None

    def agent_tmux(self, slot):
        n = self._agent_slot_ok(slot)
        return ("repl%d" % n) if n else None

    def agent_projekt(self, slot):

        n = self._agent_slot_ok(slot)
        return ("/root/.claude/projects/-root-w%d" % n) if n else None

    def agent_alive(self, slot):

        t = self.agent_tmux(slot)
        if not t or not self.alive():
            return False
        ok, out = self._run("tmux has-session -t %s 2>/dev/null && echo JA || echo NEIN; echo __AL__"
                            % t, "__AL__", 10)
        return bool(ok) and "JA" in out.split("__AL__")[0]

    def agent_slots(self):

        if not self.alive():
            return []
        ok, out = self._run("tmux ls 2>/dev/null | busybox cut -d: -f1; echo __SL__", "__SL__", 10)
        if not ok:
            return []
        raus = []
        for ln in out.split("__SL__")[0].splitlines():
            ln = ln.strip()
            if ln.startswith("repl") and ln[4:].isdigit():
                n = self._agent_slot_ok(ln[4:])
                if n:
                    raus.append(n)
        return sorted(raus)

    def agent_start(self, slot, flags="", claude_bin="/bin/claude"):

        n = self._agent_slot_ok(slot)
        if not n:
            return False
        if not self.alive() and not self.boot():
            return False
        if self.agent_alive(n):
            return True
        d, t = self.agent_dir(n), self.agent_tmux(n)
        f = str(flags or "")

        skript = (
            "command -v tmux >/dev/null 2>&1 || { echo KEINTMUX; echo __ST__; exit 0; }; "
            "busybox mkdir -p %(d)s 2>/dev/null; "
            "cd %(d)s && tmux -u new-session -d -s %(t)s "
            "\"IS_SANDBOX=1 HOME=/root %(cb)s --continue --dangerously-skip-permissions %(f)s "
            "2>/tmp/claude-w%(n)d.err || IS_SANDBOX=1 HOME=/root %(cb)s "
            "--dangerously-skip-permissions %(f)s 2>>/tmp/claude-w%(n)d.err\" "
            "2>/tmp/pnlaunch-w%(n)d.log && echo GESTARTET || echo FEHLER; echo __ST__"
            % {"d": d, "t": t, "cb": claude_bin, "f": f, "n": n})
        ok, out = self._run(skript, "__ST__", 30)
        return bool(ok) and "GESTARTET" in out.split("__ST__")[0]

    def agent_submit(self, slot, text):

        n = self._agent_slot_ok(slot)
        if not n or not text:
            return False
        if not self.agent_alive(n) and not self.agent_start(n):
            return False
        b64 = base64.b64encode(str(text).encode("utf-8")).decode()
        t = self.agent_tmux(n)
        ok, out = self._run(
            "printf %%s '%(b)s' | base64 -d > /tmp/w%(n)d.msg && "
            "tmux load-buffer -b w%(n)d /tmp/w%(n)d.msg && "
            "tmux paste-buffer -b w%(n)d -t %(t)s && "
            "tmux send-keys -t %(t)s Enter && echo OK; echo __SU__"
            % {"b": b64, "n": n, "t": t}, "__SU__", 25)
        if ok and "OK" in out.split("__SU__")[0]:
            self.last = time.time()
            return True
        return False

    def agent_jsonl(self, slot):

        p = self.agent_projekt(slot)
        if not p:
            return None
        ok, out = self._run("ls -1t %s/*.jsonl 2>/dev/null | head -1; echo __AJ__" % p, "__AJ__", 12)
        zeilen = [l.strip() for l in out.split("__AJ__")[0].splitlines()
                  if l.strip().endswith(".jsonl")]
        return zeilen[0] if zeilen else None

    def agent_busy(self, slot):

        p = self.agent_jsonl(slot)
        if not p:
            return (True, "nojsonl")
        try:
            return (bool(self._incell_turn_busy(p)), "turn")
        except Exception:
            return (None, None)

    def agent_tail(self, slot, n=8, maxbytes=200000):

        p = self.agent_jsonl(slot)
        if not p:
            return []
        try:
            ok, out = self._run("tail -c %d '%s' 2>/dev/null; echo __AT__" % (int(maxbytes), p),
                                "__AT__", 20)
        except Exception:
            return []
        turns = []
        for ln in out.split("__AT__")[0].splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                ev = json.loads(ln)
            except Exception:
                continue
            turns.extend(self._convo_turns(ev))
        return turns[-int(n):]

    def agent_stop(self, slot):

        t = self.agent_tmux(slot)
        if not t or not self.alive():
            return False
        ok, _ = self._run("tmux kill-session -t %s 2>/dev/null; echo __AK__" % t, "__AK__", 12)
        return bool(ok)

    def _incell_jsonl_size(self, path):
        if not path:
            return 0
        ok, out = self._run("wc -c < '%s' 2>/dev/null; echo __SZ__" % path, "__SZ__", 12)
        try:
            return int((out.split("__SZ__")[0].strip().split() or ["0"])[0])
        except Exception:
            return 0

    def _incell_assistant_tail(self, path, off):

        if not path:
            return []
        ok, out = self._run("tail -c +%d '%s' 2>/dev/null; echo __TE__" % (off + 1, path), "__TE__", 20)
        body = out.split("__TE__")[0]
        cut = body.rfind("\n")
        if cut == -1:
            return []
        texts = []
        for ln in body[:cut + 1].splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                ev = json.loads(ln)
            except Exception:
                continue
            for turn in self._convo_turns(ev):
                if turn["role"] != "assistant":
                    continue
                t = turn["text"].strip()
                if t and t.rstrip(".").strip().lower() not in _VOICE_META_ARTIFACTS:
                    texts.append(t)
        return texts

    def bus_tail(self, off):

        path = self._incell_active_jsonl()
        if not path:
            return {"texts": [], "models": [], "turns": [], "off": off, "path": None}
        ok, out = self._run("tail -c +%d '%s' 2>/dev/null; echo __BT__" % (off + 1, path), "__BT__", 20)
        body = out.split("__BT__")[0]
        cut = body.rfind("\n")
        if cut == -1:
            return {"texts": [], "models": [], "turns": [], "off": off, "path": path}
        complete = body[:cut + 1]
        texts = []; models = []
        turns = []
        for ln in complete.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                ev = json.loads(ln)
            except Exception:
                continue
            for turn in self._convo_turns(ev):
                t = (turn.get("text") or "").strip()
                if not t or t.rstrip(".").strip().lower() in _VOICE_META_ARTIFACTS:
                    continue
                if turn["role"] == "user" and _is_injected_user_text(t):
                    continue
                turns.append({"role": turn["role"], "text": t, "model": turn.get("model")})
                if turn["role"] == "assistant":
                    texts.append(t); models.append(turn.get("model"))
        new_off = off + len(complete.encode("utf-8", "replace"))
        return {"texts": texts, "models": models, "turns": turns, "off": new_off, "path": path}

    def transcript_tail(self, off=0, maxbytes=120000, path=None):

        path = path or self._incell_active_jsonl()
        if not path:
            return {"lines": [], "off": max(0, int(off or 0)), "path": None, "size": 0}
        size = self._incell_jsonl_size(path)
        off = max(0, int(off or 0))
        if off > size:
            off = 0
        if off >= size:
            return {"lines": [], "off": off, "path": path, "size": size}
        want = max(1024, min(int(maxbytes or 0) or 120000, 900000))
        ok, out = self._run("tail -c +%d '%s' 2>/dev/null | head -c %d; echo __XT__"
                            % (off + 1, path, want), "__XT__", 25)
        body = out.split("__XT__")[0]
        cut = body.rfind("\n")
        if cut == -1:
            return {"lines": [], "off": off, "path": path, "size": size}
        complete = body[:cut + 1]
        new_off = off + len(complete.encode("utf-8", "replace"))
        return {"lines": [l for l in complete.splitlines() if l.strip()],
                "off": new_off, "path": path, "size": size}

    def conversation_tail(self, n=40, maxbytes=200000):

        try:
            path = self._incell_active_jsonl()
            if not path:
                return []
            ok, out = self._run("tail -c %d '%s' 2>/dev/null; echo __CT__" % (int(maxbytes), path),
                                "__CT__", 20)
        except Exception:
            return []
        body = out.split("__CT__")[0]
        turns = []
        for ln in body.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                ev = json.loads(ln)
            except Exception:
                continue
            turns.extend(self._convo_turns(ev))
        return turns[-int(n):]

    def _incell_turn_busy(self, path):

        if not path:
            return False
        ok, out = self._run("tail -c 24000 '%s' 2>/dev/null; echo __TB__" % path, "__TB__", 12)
        body = out.split("__TB__")[0]
        cut = body.rfind("\n")
        if cut <= 0:
            return True
        if self._incell_runtime() == "codex":

            busy = False
            for ln in body[:cut].splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    ev = json.loads(ln)
                except Exception:
                    continue
                if ev.get("type") == "event_msg":
                    pt = (ev.get("payload") or {}).get("type")
                    if pt == "task_started":
                        busy = True
                    elif pt == "task_complete":
                        busy = False
            return busy
        last_assist_sr = None
        continued_after = False
        for ln in body[:cut].splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                ev = json.loads(ln)
            except Exception:
                continue
            typ = ev.get("type")
            if typ == "assistant":
                last_assist_sr = (ev.get("message") or {}).get("stop_reason")
                continued_after = False
            elif typ == "user":
                continued_after = True
        if last_assist_sr is None or continued_after:
            return True
        return last_assist_sr not in ("end_turn", "stop_sequence", "max_tokens")

    def voice_turn(self, text, on_sentence=None, timeout=120, settle=1.6, system=None):

        with self._lock:
            if not self.alive() and not self.boot():

                return {"text": ("Die isolierte Sitzung konnte nicht starten. "
                                 + (self.boot_reason() or "")).strip(),
                        "done": True, "busy": False}

            _log_da = self._incell_active_jsonl()
            _frisch = bool(self._term_launches) and (time.time() - self._term_launches[-1]) < REPL_FRISCH_S
            kalt = ((not self.term_on) or (not self.term_runner_alive())
                    or (_log_da is None) or _frisch)
            started = self.start_terminal(system=system)
            tc = self.term_conn
            denied = None if started else (self.term_reason() or llm_lane_reason())
        if denied:
            return {"text": denied, "done": True, "busy": False}
        if tc is None:
            return {"text": (self.term_reason() or llm_lane_reason()
                             or "In der Zelle läuft gerade kein Terminal."),
                    "done": True, "busy": False}
        if kalt:

            self._repl_abwarten()
            self._drain_until_quiet(tc, hard=KALT_STILL_HART, quiet=1.3)
        path = self._incell_active_jsonl()
        off0 = self._incell_jsonl_size(path)
        try:
            tc.setblocking(True)
            tc.sendall(text.encode())
            time.sleep(0.35)
            tc.sendall(b"\r")
        except OSError:
            return {"text": "(Eingabe in die Zelle fehlgeschlagen)", "done": True, "busy": False}
        self.last = time.time()
        collected = []; emitted = 0; t0 = time.time(); last_new = time.time()
        last_sz = off0 or 0; last_grow = time.time()
        _wurf = 1; _getippt = time.time()
        while time.time() - t0 < timeout:
            time.sleep(0.6)
            if path is None:
                path = self._incell_active_jsonl(); off0 = 0; last_sz = 0
                if path is None:

                    if kalt and _wurf == 1 and (time.time() - _getippt) > 25.0:
                        _wurf = 2
                        self._drain_until_quiet(tc, hard=20.0, quiet=1.3)
                        try:
                            tc.setblocking(True)
                            tc.sendall(text.encode()); time.sleep(0.35); tc.sendall(b"\r")
                            _getippt = time.time()
                        except OSError:
                            pass
                    continue
            sz = self._incell_jsonl_size(path)
            if sz > last_sz:
                last_sz = sz; last_grow = time.time()
            texts = self._incell_assistant_tail(path, off0)
            if len(texts) > emitted:
                for t in texts[emitted:]:
                    t = _speakable(t)
                    if not t:
                        continue
                    collected.append(t)
                    if on_sentence:
                        try: on_sentence(t)
                        except Exception: pass
                emitted = len(texts); last_new = time.time()
            elif collected and (time.time() - last_new) > settle:
                break
        busy = self._incell_turn_busy(path)

        text = "\n".join(collected).strip()
        if not text and not busy:
            text = self.term_reason() or llm_lane_reason() or "(keine Antwort erhalten)"
        return {"text": text,
                "done": not busy, "busy": busy, "path": path, "off0": off0, "emitted": emitted}

    def submit(self, text, system=None, ready_timeout=14.0):

        cold = False
        with self._lock:
            if not self.alive() and not self.boot():
                return False
            cold = (not self.term_on) or (not self.term_runner_alive())

            started = self.start_terminal(system=system)
            tc = self.term_conn
        if not started or tc is None:
            return False
        if cold:

            if not self._drain_until_quiet(tc, hard=ready_timeout, quiet=1.3):

                self._drain_until_quiet(tc, hard=45.0, quiet=1.3)

        path0 = self._incell_active_jsonl()
        off0 = self._incell_jsonl_size(path0) if path0 else 0
        try:
            tc.setblocking(True)
        except Exception:
            pass
        for _versuch in (1, 2):
            try:
                tc.sendall(text.encode())
                time.sleep(0.35)
                tc.sendall(b"\r")
            except OSError:
                return False
            self.last = time.time()
            _t0 = time.time()
            while time.time() - _t0 < 25.0:
                time.sleep(2.5)
                _p = self._incell_active_jsonl()
                if _p and self._incell_jsonl_size(_p) > (off0 if _p == path0 else 0):
                    return True
            if _versuch == 1:

                self._drain_until_quiet(tc, hard=20.0, quiet=1.3)
        return False

    def _drain_until_quiet(self, tc, hard=14.0, quiet=1.3):

        import select
        t0 = time.time(); last = time.time(); saw = False
        try:
            tc.setblocking(False)
        except Exception:
            pass
        while time.time() - t0 < hard:
            try:
                rl, _, _ = select.select([tc], [], [], 0.3)
            except Exception:
                break
            if rl:
                try:
                    d = tc.recv(65536)
                except BlockingIOError:
                    continue
                except Exception:
                    break
                if d:
                    saw = True; last = time.time(); continue
                break
            if saw and (time.time() - last) >= quiet:
                return True
            if (not saw) and (time.time() - t0) >= min(hard, 6.0):
                return False
        return saw

    def kontext_tokens(self):

        path = self._incell_active_jsonl()
        if not path:
            return None
        ok, out = self._run("tail -c 200000 '%s' 2>/dev/null; echo __KT__" % path, "__KT__", 20)
        body = out.split("__KT__")[0]
        letzte = None
        for ln in body.splitlines():
            ln = ln.strip()
            if not ln or '"usage"' not in ln:
                continue
            try:
                ev = json.loads(ln)
            except Exception:
                continue
            if ev.get("type") != "assistant":
                continue
            u = (ev.get("message") or {}).get("usage")
            if isinstance(u, dict):
                letzte = u
        if not letzte:
            return None
        return sum(int(letzte.get(k) or 0) for k in
                   ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))

    def verdichten(self, timeout=420):

        with self._lock:
            if not self.alive():
                return False, "Zelle laeuft nicht"
            if not self.start_terminal():
                return False, (self.term_reason() or "kein REPL in der Zelle")
            tc = self.term_conn
        if tc is None:
            return False, "keine Terminal-Bahn"
        path = self._incell_active_jsonl()
        if path and self._incell_turn_busy(path):
            return False, "Zelle arbeitet gerade"
        vorher = self._verdichtungs_marken(path)
        try:
            tc.setblocking(True)
            tc.sendall(b"/compact")
            time.sleep(0.35)
            tc.sendall(b"\r")
        except OSError:
            return False, "Eingabe in die Zelle fehlgeschlagen"
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(5.0)
            p = self._incell_active_jsonl()
            if not p:
                continue
            if self._verdichtungs_marken(p) > (vorher if p == path else 0):
                self.last = time.time()
                return True, "verdichtet"
        return False, "keine Verdichtungs-Marke innerhalb von %ds gesehen" % int(timeout)

    def _verdichtungs_marken(self, path):

        if not path:
            return 0
        ok, out = self._run("tail -c 200000 '%s' 2>/dev/null | busybox grep -c "
                            "-e isCompactSummary -e compact_boundary; echo __CV__"
                            % path, "__CV__", 20)
        try:
            return int((out.split("__CV__")[0].strip().splitlines() or ["0"])[0])
        except (ValueError, IndexError):
            return 0

    def ask(self, text, timeout=120, settle=2.0, system=None):

        cold = False
        with self._lock:
            if not self.alive() and not self.boot():
                return {"text": "", "path": None, "off": 0}
            cold = (not self.term_on) or (not self.term_runner_alive())
            self.start_terminal(system=system)
            tc = self.term_conn
        if tc is None:
            return {"text": "", "path": None, "off": 0}
        if cold:

            self._repl_abwarten()
            self._drain_until_quiet(tc, hard=KALT_STILL_HART, quiet=1.3)
        path = self._incell_active_jsonl()
        off0 = self._incell_jsonl_size(path) if path else 0
        try:
            tc.setblocking(True)
            tc.sendall(text.encode()); time.sleep(0.35); tc.sendall(b"\r")
        except OSError:
            return {"text": "", "path": path, "off": off0}
        if cold:
            path, off0 = self._abgabe_bestaetigen(tc, path, off0)
        self.last = time.time()
        collected = []; t0 = time.time(); last = time.time()
        while time.time() - t0 < timeout:
            time.sleep(0.6)
            if path is None:
                path = self._incell_active_jsonl(); off0 = 0
                if path is None:
                    continue
            try:
                texts = self._incell_assistant_tail(path, off0)
            except Exception:
                break
            if len(texts) > len(collected):
                collected = texts; last = time.time()
            elif collected and (time.time() - last) > settle:
                break
        new_off = (self._incell_jsonl_size(path) if path else off0) or off0
        return {"text": "\n".join(collected).strip(), "path": path, "off": new_off}

    def _abgabe_bestaetigen(self, tc, path, off0, runden=(6.0, 8.0, 10.0, 12.0, 15.0)):

        for i, budget in enumerate(runden):
            t0 = time.time()
            while time.time() - t0 < budget:
                time.sleep(0.4)
                if path is None:
                    path = self._incell_active_jsonl()
                    if path:
                        return path, 0
                    continue
                try:
                    if self._incell_jsonl_size(path) > off0:
                        return path, off0
                except Exception:
                    return path, off0
            if i + 1 >= len(runden):
                break
            try:
                tc.sendall(b"\r")
            except OSError:
                break
            sys.stderr.write("[pn-session] %s: Kaltstart — Abgabe der ersten Zeile wiederholt (%d)\n"
                             % (self.session, i + 1))

        sys.stderr.write("[pn-session] %s: Kaltstart — die erste Zeile wurde nach %.0fs NICHT "
                         "angenommen (Gespraechs-Log unveraendert)\n"
                         % (self.session, sum(runden)))
        return path, off0

    def voice_watch(self, path, off0, emitted, on_sentence=None, should_continue=None,
                    budget=600, idle=2.5):

        if not path:
            return emitted
        t0 = time.time(); last_new = time.time()
        while time.time() - t0 < budget:
            if should_continue is not None:
                try:
                    if not should_continue():
                        break
                except Exception:
                    break
            time.sleep(1.5)
            texts = self._incell_assistant_tail(path, off0)
            if len(texts) > emitted:
                for t in texts[emitted:]:
                    t = _speakable(t)
                    if not t:
                        continue
                    if on_sentence:
                        try: on_sentence(t)
                        except Exception: pass
                emitted = len(texts); last_new = time.time()
                self.last = time.time()
            elif not self._incell_turn_busy(path) and (time.time() - last_new) > idle:
                break
        return emitted
