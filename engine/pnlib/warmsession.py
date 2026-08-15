
from __future__ import annotations
import os, json, time, shlex, signal, subprocess, threading

class WarmSessionError(Exception):
    pass

class Saturated(WarmSessionError):
    pass

class WarmSession:

    def __init__(self, model, cmd_tmpl, env=None, *, session_id=None,
                 spawn_timeout=20.0, request_timeout=300.0, ping_timeout=5.0,
                 clock=time.time, _proc_factory=None):
        self.model = model
        self.cmd_tmpl = cmd_tmpl
        self.env = env or {}
        self.session_id = session_id or f"warm-{int(clock()*1000) & 0xffffff:06x}"
        self.spawn_timeout = spawn_timeout
        self.request_timeout = request_timeout
        self.ping_timeout = ping_timeout
        self._clock = clock
        self._proc_factory = _proc_factory
        self.proc = None
        self.state = "new"
        self.created = clock()
        self.last_used = 0.0
        self.served = 0
        self.errors = 0
        self.spawn_count = 0
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()

    def spawn(self):

        if self.proc is not None and self._alive():
            return self
        self.state = "spawning"
        env = dict(os.environ)
        env.update(self.env)
        argv = shlex.split(self.cmd_tmpl.format(model=self.model))
        if self._proc_factory is not None:
            self.proc = self._proc_factory(argv, env)
        else:
            self.proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env,
                start_new_session=True)
        self.spawn_count += 1
        self.state = "ready"
        self.created = self._clock()
        return self

    def health_check(self):

        if self.proc is None or not self._alive():
            return False

        if not self._lock.acquire(blocking=False):
            return True
        try:
            r = self._roundtrip({"ping": 1}, timeout=self.ping_timeout)
            return bool(r is not None and r.get("ok", True))
        except Exception:
            return False
        finally:
            self._lock.release()

    def retire(self, *, grace=3.0):

        self.state = "retired"
        p = self.proc
        if p is None:
            return
        try:
            if self._proc_factory is not None:
                p.terminate()
            else:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except Exception:
            try:
                p.terminate()
            except Exception:
                pass
        try:
            p.wait(timeout=grace)
        except Exception:
            try:
                if self._proc_factory is not None:
                    p.kill()
                else:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                pass
        self.proc = None

    def ask(self, prompt, *, timeout=None, block=True):

        timeout = self.request_timeout if timeout is None else timeout
        if not self._lock.acquire(blocking=block):
            raise Saturated(f"pinned session {self.session_id} is serving another request")
        try:
            if self.proc is None or not self._alive():
                self.spawn()
            self.state = "serving"
            try:
                r = self._roundtrip({"prompt": prompt}, timeout=timeout)
            except Exception as e:
                self.errors += 1
                self.state = "dead"
                return {"ok": False, "error": f"backend io: {type(e).__name__}: {e}",
                        "session": self.session_id}
            self.last_used = self._clock()
            self.served += 1
            self.state = "ready"
            if r is None:
                self.errors += 1
                self.state = "dead"
                return {"ok": False, "error": "backend produced no response (eof/timeout)",
                        "session": self.session_id}
            text = r.get("text", "")
            if r.get("ok") is False:
                self.errors += 1
                return {"ok": False, "error": r.get("error") or "backend error",
                        "session": self.session_id, "raw": text}
            return {"ok": True, "text": text, "session": self.session_id}
        finally:
            self._lock.release()

    def _alive(self):
        return self.proc is not None and self.proc.poll() is None

    def _roundtrip(self, obj, *, timeout):

        with self._io_lock:
            line = json.dumps(obj, separators=(",", ":")) + "\n"
            wedged = {"v": False}

            def _watchdog():
                if not done.wait(timeout):
                    wedged["v"] = True

                    try:
                        if self._proc_factory is not None:
                            self.proc.kill()
                        else:
                            os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                    except Exception:
                        pass

            done = threading.Event()
            wd = threading.Thread(target=_watchdog, daemon=True)
            wd.start()
            try:
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
                resp = self.proc.stdout.readline()
            finally:
                done.set()
            wd.join(timeout=0.5)
            if wedged["v"]:
                raise WarmSessionError(f"backend wedged > {timeout}s (killed)")
            if not resp:
                return None
            try:
                return json.loads(resp)
            except (ValueError, TypeError) as e:
                raise WarmSessionError(f"backend emitted non-json: {e}: {resp[:120]!r}")

    def info(self):
        return {"session_id": self.session_id, "state": self.state, "model": self.model,
                "served": self.served, "errors": self.errors, "spawns": self.spawn_count,
                "age": round(self._clock() - self.created, 1),
                "alive": self._alive()}

class PinnedReasoner:

    def __init__(self, model, cmd_tmpl, env=None, *, session_factory=None,
                 spawn_timeout=20.0, request_timeout=300.0, ping_timeout=5.0,
                 clock=time.time):
        self.model = model
        self.cmd_tmpl = cmd_tmpl
        self.env = env or {}
        self._clock = clock
        self._kw = dict(spawn_timeout=spawn_timeout, request_timeout=request_timeout,
                        ping_timeout=ping_timeout, clock=clock)

        self._factory = session_factory or (
            lambda sid: WarmSession(self.model, self.cmd_tmpl, dict(self.env),
                                    session_id=sid, **self._kw))
        self._lock = threading.Lock()
        self.session = None
        self.rotations = 0
        self.spawns = 0
        self.stats = {"asks": 0, "errors": 0, "saturated": 0, "rotations": 0, "health_fail": 0}

    def start(self, *, digest=None):
        with self._lock:
            self._spawn_locked(digest=digest)
        return self.session.session_id

    def _spawn_locked(self, *, digest=None):
        sid = f"warm-{int(self._clock()*1000) & 0xffffff:06x}-{self.spawns}"
        self.session = self._factory(sid)
        self.session.spawn()
        self.spawns += 1
        if digest:

            try:
                self.session.ask(json.dumps({"prime": "digest", "digest": digest[:8000]}),
                                 timeout=self.session.ping_timeout * 4)
            except Exception:
                pass

    def ask(self, prompt, *, block=True):

        self.stats["asks"] += 1
        if self.session is None:
            self.start()
        try:
            r = self.session.ask(prompt, block=block)
        except Saturated:
            self.stats["saturated"] += 1
            raise
        if not r.get("ok"):
            self.stats["errors"] += 1
        return r

    def health(self):
        if self.session is None:
            return False
        ok = self.session.health_check()
        if not ok:
            self.stats["health_fail"] += 1
        return ok

    def rotate(self, *, digest=None):

        with self._lock:
            old = self.session.session_id if self.session else None

            if self.session is not None:
                got = self.session._lock.acquire(timeout=self.session.request_timeout)
                try:
                    self.session.retire()
                finally:
                    if got:
                        self.session._lock.release()
            self._spawn_locked(digest=digest)
            self.rotations += 1
            self.stats["rotations"] += 1
            return self.session.session_id, old

    def retire(self):

        with self._lock:
            if self.session is not None:
                self.session.retire()
                self.session = None

    def info(self):
        return {"model": self.model, "rotations": self.rotations, "spawns": self.spawns,
                "stats": dict(self.stats),
                "session": self.session.info() if self.session else None}
