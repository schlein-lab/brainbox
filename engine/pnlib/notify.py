
from __future__ import annotations
import os, time, threading, json, inspect
from collections import OrderedDict

try:
    from pnlib import redact as _redact
    _REDACT_IMPORT_ERR = None
except Exception as _redact_err:
    _redact = None
    _REDACT_IMPORT_ERR = repr(_redact_err)
    import sys as _sys
    _sys.stderr.write(
        "[notify] P3 redaction module (pnlib.redact) FAILED to import: %s — the notify-broker will "
        "REFUSE to deliver (fail-closed) rather than send UN-REDACTED content. Inject redact_fn or "
        "fix the import.\n" % _REDACT_IMPORT_ERR)

def _accepts_4_positional(fn) -> bool:

    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return False
    if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params):
        return True
    positional = [p for p in params if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                                  inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    return len(positional) >= 4

def parse_reply_to(reply_to: str | None):

    if not reply_to or ":" not in reply_to:
        return (None, None)
    adapter, _, address = reply_to.partition(":")
    return (adapter.strip() or None, address.strip() or None)

class Adapter:

    name = "base"

    def deliver(self, address, content, kind, meta):
        raise NotImplementedError

class MockAdapter(Adapter):

    def __init__(self, name="mock"):
        self.name = name
        self.sent = []
        self._lock = threading.Lock()

    def deliver(self, address, content, kind, meta):
        with self._lock:
            self.sent.append({"address": address, "content": content, "kind": kind,
                              "meta": meta, "ts": time.time()})
        return {"ok": True, "id": f"mock-{len(self.sent)}"}

class TelegramAdapter(Adapter):

    name = "telegram"

    def __init__(self, cred_for, send_fn=None):
        self._cred_for = cred_for
        self._send_fn = send_fn

    def deliver(self, address, content, kind, meta):
        if self._send_fn is None:
            return {"ok": False, "error": "telegram send_fn not wired (zyrkel strand)"}
        token = self._cred_for(self.name)
        if not token:
            return {"ok": False, "error": "no sealed telegram credential"}
        try:
            return self._send_fn(token, address, content, meta) or {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

class EmailAdapter(Adapter):

    name = "email"

    def __init__(self, send_email_fn=None, subject_prefix="[portioneer] ", cred_for=None):
        self._send = send_email_fn
        self._prefix = subject_prefix
        self._cred_for = cred_for

    def deliver(self, address, content, kind, meta):
        if self._send is None:
            return {"ok": False, "error": "send_email not wired"}
        subj = self._prefix + (meta.get("subject")
                               or f"{(meta.get('task_type') or 'job')} {meta.get('state') or kind}")
        try:

            if self._cred_for is not None and _accepts_4_positional(self._send):
                self._send(address, subj, content, lambda: self._cred_for(self.name))
            else:
                self._send(address, subj, content)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

class WebhookAdapter(Adapter):

    name = "webhook"

    def __init__(self, cred_for=None, post_fn=None):
        self._cred_for = cred_for
        self._post = post_fn

    def deliver(self, address, content, kind, meta):
        if self._post is None:
            return {"ok": False, "error": "webhook post_fn not wired"}
        secret = self._cred_for(self.name) if self._cred_for else None
        body = {"kind": kind, "content": content, "meta": meta}
        try:
            return self._post(address, body, secret) or {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

class NativeAdapter(Adapter):

    name = "native"

    def __init__(self):
        self.acked = []

    def deliver(self, address, content, kind, meta):
        self.acked.append({"address": address, "kind": kind, "job_id": meta.get("job_id")})
        return {"ok": True, "id": "native-bus"}

class FileAdapter(Adapter):

    name = "file"
    _SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.@")

    def __init__(self, dir_path, max_bytes=4_000_000, keep=3):
        self.dir = dir_path
        self.max_bytes = int(max_bytes)
        self.keep = max(0, int(keep))
        self._lock = threading.Lock()

    def mailbox_name(self, address):

        raw = (address or "").strip()
        safe = "".join(c if c in self._SAFE else "_" for c in raw).lstrip(".")
        return (safe[:64] or "unbekannt")

    def mailbox_path(self, address):
        return os.path.join(self.dir, self.mailbox_name(address) + ".jsonl")

    def _rotate_if_needed(self, path):

        try:
            if os.path.getsize(path) < self.max_bytes:
                return
        except OSError:
            return
        if self.keep <= 0:
            try:
                os.unlink(path)
            except OSError:
                pass
            return
        for i in range(self.keep, 0, -1):
            src = path if i == 1 else f"{path}.{i - 1}"
            try:
                if os.path.exists(src):
                    os.replace(src, f"{path}.{i}")
            except OSError:
                pass

    def deliver(self, address, content, kind, meta):
        path = self.mailbox_path(address)
        rec = {"ts": round(time.time(), 3), "kind": kind, "to": address,
               "text": content,
               "job_id": meta.get("job_id"), "state": meta.get("state"),
               "task_type": meta.get("task_type"), "meta": meta}

        if meta.get("ereignis_ts") is not None:
            try:
                rec["ereignis_ts"] = round(float(meta["ereignis_ts"]), 3)
            except (TypeError, ValueError):
                pass
        try:
            line = json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str) + "\n"
        except (TypeError, ValueError) as e:

            return {"ok": False, "error": f"unserialisable payload: {e}", "permanent": True}
        try:
            with self._lock:
                os.makedirs(self.dir, mode=0o700, exist_ok=True)
                self._rotate_if_needed(path)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    os.write(fd, line.encode())
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except OSError as e:

            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return {"ok": True, "id": f"file:{os.path.basename(path)}"}

def read_postfach(dir_path, address, limit=20):

    path = os.path.join(dir_path, FileAdapter(dir_path).mailbox_name(address) + ".jsonl")
    out = []
    try:
        with open(path, "r", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        return []
    return out[-int(limit):] if limit else out

class RateLimiter:

    def __init__(self, capacity=10, refill_per_sec=1.0):
        self.capacity = float(capacity)
        self.refill = float(refill_per_sec)
        self._buckets = {}
        self._lock = threading.Lock()

    def allow(self, principal, now=None):
        now = now if now is not None else time.time()
        with self._lock:
            tokens, last = self._buckets.get(principal, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill)
            if tokens >= 1.0:
                self._buckets[principal] = (tokens - 1.0, now)
                return True
            self._buckets[principal] = (tokens, now)
            return False

class DedupeLedger:

    def __init__(self, maxsize=50000):
        self._seen = OrderedDict()
        self._attempts = OrderedDict()
        self._max = maxsize
        self._lock = threading.Lock()

    def key(self, job_id, event_id):
        return f"{job_id}:{event_id}"

    def seen(self, job_id, event_id):
        k = self.key(job_id, event_id)
        with self._lock:
            return k in self._seen

    def mark(self, job_id, event_id):

        k = self.key(job_id, event_id)
        with self._lock:
            self._attempts.pop(k, None)
            if k in self._seen:
                self._seen.move_to_end(k)
                return
            self._seen[k] = True
            while len(self._seen) > self._max:
                self._seen.popitem(last=False)

    def record_attempt(self, job_id, event_id):

        k = self.key(job_id, event_id)
        with self._lock:
            n = self._attempts.get(k, 0) + 1
            self._attempts[k] = n
            self._attempts.move_to_end(k)
            while len(self._attempts) > self._max:
                self._attempts.popitem(last=False)
            return n

    def attempts(self, job_id, event_id):
        with self._lock:
            return self._attempts.get(self.key(job_id, event_id), 0)

def render_content(kind, payload):

    p = payload if isinstance(payload, dict) else {}
    jid = p.get("job_id")
    tt = p.get("task_type") or "job"
    if kind == "notify":
        state = p.get("state")

        if state == "walltime-warn":
            rem, lim = p.get("remaining_s"), p.get("walltime_s")
            how = (f"`pn extend {jid} <seconds>` buys more time"
                   if p.get("extendable") else "only an admin can extend this class")
            return (f"job {jid} ({tt}) hits its {lim}s walltime in ~{rem}s "
                    f"and will be KILLED — {how}")
        if state == "walltime-extended":
            return (f"job {jid} ({tt}) walltime extended by {p.get('granted_s')}s "
                    f"(total extra {p.get('total_extra_s')}s, by {p.get('by')})")
        if state == "oom-retry":
            return (f"job {jid} ({tt}) was OOM-killed and is requeued with "
                    f"{p.get('mem_from')} -> {p.get('mem_to')} MiB "
                    f"(retry {p.get('retry')}/{p.get('max')})")
        if state == "timeout":
            return f"job {jid} ({tt}) was KILLED at its walltime (timeout)"
        return f"job {jid} ({tt}) -> {state}"
    if kind == "partial":
        msg = p.get("msg") or p.get("note") or json.dumps(p, separators=(",", ":"))
        return f"job {jid} ({tt}) partial: {msg}"
    if kind == "approval-request":
        gate = p.get("gate")
        summary = p.get("summary") or tt
        return (f"job {jid} ({tt}) needs your approval [{gate}]: {summary} "
                f"(nonce {p.get('nonce')})")
    return f"job {jid} ({tt}) event: {kind}"

DELIVERABLE_KINDS = ("notify", "partial", "approval-request")

class NotifyBroker:

    routes = {}

    def __init__(self, adapters, cred_for=None, limiter=None, dedupe=None, redact_fn=None,
                 max_attempts=5, default_adapter="native"):
        self.adapters = dict(adapters)

        self.default_adapter = default_adapter or "native"
        self.cred_for = cred_for or (lambda _name: None)
        self.limiter = limiter or RateLimiter()
        self.dedupe = dedupe or DedupeLedger()
        self.max_attempts = int(max_attempts)

        self._redact = redact_fn or (_redact.redact if _redact else None)
        self._redaction_ok = self._redact is not None
        self.redaction_refused = 0
        if not self._redaction_ok:
            import sys as _sys
            _sys.stderr.write("[notify] redaction UNAVAILABLE (%s) — broker REFUSES all deliveries "
                              "until it is restored.\n"
                              % (_REDACT_IMPORT_ERR or "no redact_fn and no pnlib.redact"))
        self.delivered = 0
        self.dropped_rate = 0
        self.deduped = 0
        self.retried = 0
        self.dead_lettered = 0

    def _coerce_payload(self, data):
        if isinstance(data, dict):
            return data
        if isinstance(data, str):
            try:
                return json.loads(data)
            except (ValueError, TypeError):
                return {"_text": data}
        return {}

    def handle_event(self, event):

        kind = event.get("kind")
        if kind not in DELIVERABLE_KINDS:
            return {"ok": True, "skipped": "kind", "kind": kind}
        eid = event.get("id")
        jid = event.get("job_id")
        if eid is not None and jid is not None and self.dedupe.seen(jid, eid):
            self.deduped += 1
            return {"ok": True, "deduped": True, "job_id": jid, "event_id": eid}
        payload = self._coerce_payload(event.get("data"))
        payload.setdefault("job_id", jid)

        if event.get("ts") is not None:
            payload.setdefault("ereignis_ts", event.get("ts"))

        reply_to = payload.get("reply_to")
        principal_frueh = payload.get("principal") or _principal_from_topic(event.get("topic"))
        if not reply_to and principal_frueh:

            reply_to = (self.routes or {}).get(str(principal_frueh))

        adapter_name, address = parse_reply_to(reply_to)
        principal = payload.get("principal") or _principal_from_topic(event.get("topic"))

        if not adapter_name:

            adapter_name = (self.default_adapter if self.default_adapter in self.adapters
                            else "native")
            address = principal or "unbekannt"
            disposition_fallback = True
        else:
            disposition_fallback = False
        adapter = self.adapters.get(adapter_name)
        if adapter is None:
            return {"ok": False, "error": f"no adapter '{adapter_name}'",
                    "job_id": jid, "event_id": eid}

        if principal and not self.limiter.allow(principal):
            self.dropped_rate += 1
            return {"ok": False, "rate_limited": True, "principal": principal,
                    "job_id": jid, "event_id": eid}

        if not self._redaction_ok:
            self.redaction_refused += 1
            return {"ok": False, "redaction_unavailable": True, "principal": principal,
                    "job_id": jid, "event_id": eid}

        content = self._redact(render_content(kind, payload))
        meta = _redact.redact_obj(payload) if _redact else {}

        res = adapter.deliver(address, content, kind, meta) or {}
        has_key = eid is not None and jid is not None
        ok = bool(res.get("ok"))
        disposition = {"ok": ok, "adapter": adapter_name, "address": address,
                       "kind": kind, "job_id": jid, "event_id": eid, "delivery": res,
                       "content": content, "fallback": disposition_fallback}

        if ok:

            if has_key:
                self.dedupe.mark(jid, eid)
            self.delivered += 1
            return disposition

        permanent = bool(res.get("permanent"))
        if not has_key:

            return disposition
        if permanent:
            self.dedupe.mark(jid, eid)
            self.dead_lettered += 1
            disposition["dead_lettered"] = True
            disposition["reason"] = "permanent"
            return disposition
        attempt = self.dedupe.record_attempt(jid, eid)
        disposition["attempt"] = attempt
        if attempt >= self.max_attempts:

            self.dedupe.mark(jid, eid)
            self.dead_lettered += 1
            disposition["dead_lettered"] = True
            disposition["reason"] = "max_attempts"
            return disposition

        self.retried += 1
        disposition["retryable"] = True
        return disposition

    def run_feed(self, feed):

        out = []
        for ev in feed:
            out.append(self.handle_event(ev))
        return out

def _principal_from_topic(topic):
    if topic and topic.startswith("user/"):
        return topic.split("/", 1)[1]
    return None

def build_from_config(config=None, cred_for=None, *, enable_mock=False,
                      telegram_send_fn=None):

    from . import notifycfg, chsecrets
    cfg = config if config is not None else notifycfg.load()
    cred_for = cred_for or chsecrets.read_channel_cred
    ad = (cfg.get("adapters") or {})
    adapters = {"native": NativeAdapter()}

    if enable_mock or (ad.get("mock") or {}).get("enabled"):
        adapters["mock"] = MockAdapter()

    fl = ad.get("file") or {}
    if fl.get("enabled", True):
        adapters["file"] = FileAdapter(fl.get("dir") or notifycfg.POSTFACH_DIR,
                                       max_bytes=fl.get("max_bytes", 4_000_000),
                                       keep=fl.get("keep", 3))

    tg = ad.get("telegram") or {}
    if tg.get("enabled"):
        send_fn = telegram_send_fn or notifycfg.telegram_send(tg)
        adapters["telegram"] = TelegramAdapter(cred_for=cred_for, send_fn=send_fn)

    em = ad.get("email") or {}
    if em.get("enabled"):
        adapters["email"] = EmailAdapter(send_email_fn=notifycfg.smtp_send(em),
                                         subject_prefix=em.get("subject_prefix", "[portioneer] "),
                                         cred_for=cred_for)

    wh = ad.get("webhook") or {}
    if wh.get("enabled"):
        adapters["webhook"] = WebhookAdapter(cred_for=cred_for, post_fn=notifycfg.https_post(wh))

    rate = cfg.get("rate") or {}
    limiter = RateLimiter(capacity=rate.get("capacity", 10),
                          refill_per_sec=rate.get("refill_per_sec", 1.0))

    max_attempts = (cfg.get("delivery") or {}).get("max_attempts", 5)
    fallback = (cfg.get("fallback") or {}).get("adapter") or "file"
    broker = NotifyBroker(adapters, cred_for=cred_for, limiter=limiter, max_attempts=max_attempts,
                        default_adapter=fallback)

    broker.routes = dict(cfg.get("routes") or {})
    return broker
