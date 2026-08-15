#!/usr/bin/env python3

import os, sys, json, time, tempfile, socket, subprocess, threading
os.environ.setdefault("PN_DISPATCH_BACKEND", "systemd")
from importlib.machinery import SourceFileLoader
import importlib.util

_NB_DATA = tempfile.mkdtemp(prefix="pn_nb_cfg_")
os.environ["PN_DATA_DIR"] = _NB_DATA
os.environ["PN_NOTIFY_CONFIG"] = os.path.join(_NB_DATA, "notify", "config.json")
os.environ["PN_BROKER_SECRETS_DIR"] = os.path.join(_NB_DATA, "broker-secrets")
os.environ["PN_SECRETS_ALLOW_INSECURE"] = "1"

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from pnlib import notify, redact, notifycfg, chsecrets
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pn_voraussetzung import live_moeglich

PASS, FAIL = 0, 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")

def ev(eid, jid, kind, payload, topic=None):
    return {"id": eid, "job_id": jid, "kind": kind, "topic": topic or "user/alice",
            "data": json.dumps(payload)}

def test_routing():
    print("[a] a done `notify` routes to the right reply_to via a mock adapter")
    m_tg = notify.MockAdapter("telegram")
    m_em = notify.MockAdapter("email")
    b = notify.NotifyBroker({"telegram": m_tg, "email": m_em, "native": notify.NativeAdapter()})
    r1 = b.handle_event(ev(1, 10, "notify", {"task_type": "echo.test", "state": "done",
                                             "principal": "alice", "reply_to": "telegram:555"}))
    r2 = b.handle_event(ev(2, 11, "notify", {"task_type": "echo.test", "state": "done",
                                             "principal": "bob", "reply_to": "email:b@x.io"},
                           topic="user/bob"))
    check(r1["ok"] and r1["adapter"] == "telegram" and r1["address"] == "555",
          "telegram reply_to routed to the telegram adapter @555")
    check(r2["ok"] and r2["adapter"] == "email" and r2["address"] == "b@x.io",
          "email reply_to routed to the email adapter @b@x.io")
    check(len(m_tg.sent) == 1 and len(m_em.sent) == 1, "exactly one delivery per channel")
    check("job 10" in m_tg.sent[0]["content"] and "done" in m_tg.sent[0]["content"],
          "the delivered content summarizes the job + terminal state")

def test_redaction():
    print("[b] a planted secret in the outbound content is MASKED (P3 redaction)")
    m = notify.MockAdapter("mock")
    b = notify.NotifyBroker({"mock": m})
    leak = "sk-ant-AAAABBBBCCCCDDDDEEEEFFFF"
    r = b.handle_event(ev(1, 20, "partial", {"task_type": "t", "principal": "alice",
                                             "reply_to": "mock:dev", "msg": f"key {leak} oops"}))
    content = m.sent[0]["content"]
    check(r["ok"] and leak not in content, "the planted Anthropic key is NOT in the delivered text")
    check("redacted" in content, "the secret is replaced by a redaction marker")

    meta_blob = json.dumps(m.sent[0]["meta"])
    check(leak not in meta_blob, "the structured meta carries no raw secret either")

def test_rate_limit():
    print("[c] per-principal token-bucket rate-limit drops a flood")
    m = notify.MockAdapter("mock")

    b = notify.NotifyBroker({"mock": m}, limiter=notify.RateLimiter(capacity=3, refill_per_sec=0))
    res = [b.handle_event(ev(i, 30, "notify", {"task_type": "t", "principal": "alice",
                                               "state": "done", "reply_to": "mock:dev"}))
           for i in range(1, 7)]
    delivered = sum(1 for r in res if r.get("ok"))
    limited = sum(1 for r in res if r.get("rate_limited"))
    check(delivered == 3 and limited == 3, f"3 delivered, 3 rate-limited (got {delivered}/{limited})")

    rb = b.handle_event(ev(99, 31, "notify", {"task_type": "t", "principal": "carol",
                                              "state": "done", "reply_to": "mock:c"},
                          topic="user/carol"))
    check(rb.get("ok"), "a different principal still gets delivery (per-principal buckets)")

def test_no_cred_leak():
    print("[d] a task cannot read the channel cred: cred read ONLY from the sealed store by the adapter")
    seen = {}

    def sealed_cred(name):
        seen["read"] = name
        return "BOT-TOKEN-SEALED-XYZ"

    captured = {}

    def fake_tg_send(token, chat_id, text, meta):
        captured["token"] = token; captured["chat_id"] = chat_id; captured["text"] = text
        return {"ok": True}

    tg = notify.TelegramAdapter(cred_for=sealed_cred, send_fn=fake_tg_send)
    b = notify.NotifyBroker({"telegram": tg}, cred_for=sealed_cred)

    e = ev(1, 40, "notify", {"task_type": "t", "principal": "alice", "state": "done",
                             "reply_to": "telegram:1234"})
    check("BOT-TOKEN" not in e["data"], "the bus event carries NO channel credential")
    r = b.handle_event(e)
    check(r["ok"] and captured.get("token") == "BOT-TOKEN-SEALED-XYZ",
          "the token reached the transport ONLY via the sealed cred_for seam")
    check(captured.get("chat_id") == "1234", "the per-requester address came from reply_to")
    check("BOT-TOKEN" not in r["content"], "the delivered content never contains the token")

def test_idempotent():
    print("[e] idempotent delivery: a replayed event delivers AT MOST ONCE (dedupe job+event)")
    m = notify.MockAdapter("mock")
    b = notify.NotifyBroker({"mock": m})
    e = ev(7, 50, "notify", {"task_type": "t", "principal": "alice", "state": "done",
                             "reply_to": "mock:dev"})
    r1 = b.handle_event(e)
    r2 = b.handle_event(e)
    r3 = b.handle_event(e)
    check(r1["ok"] and not r1.get("deduped"), "first delivery goes through")
    check(r2.get("deduped") and r3.get("deduped"), "subsequent identical events are deduped")
    check(len(m.sent) == 1, "the channel saw the message exactly once")

    r4 = b.handle_event(ev(8, 50, "notify", {"task_type": "t", "principal": "alice",
                                             "state": "failed", "reply_to": "mock:dev"}))
    check(r4["ok"] and len(m.sent) == 2, "a distinct event id for the same job is delivered")

def test_adapter_interface():
    print("[f] adapter interface: telegram plugs in (zyrkel seam), email wraps send_email, native acks")

    tg_calls = []
    tg = notify.TelegramAdapter(cred_for=lambda n: "T", send_fn=lambda tok, cid, txt, meta:
                                tg_calls.append((tok, cid, txt)) or {"ok": True})
    check(tg.deliver("99", "hi", "notify", {})["ok"] and tg_calls,
          "telegram adapter delegates to the injected (zyrkel) send_fn")

    tg_unwired = notify.TelegramAdapter(cred_for=lambda n: "T", send_fn=None)
    check(not tg_unwired.deliver("1", "x", "notify", {})["ok"],
          "an unwired telegram adapter reports 'not wired' (no crash)")

    em_calls = []
    em = notify.EmailAdapter(send_email_fn=lambda to, subj, body: em_calls.append((to, subj, body)))
    check(em.deliver("a@b.c", "body", "notify", {"task_type": "t", "state": "done"})["ok"]
          and em_calls and em_calls[0][0] == "a@b.c",
          "email adapter wraps send_email(to, subject, body)")

    nat = notify.NativeAdapter()
    check(nat.deliver("alice", "x", "notify", {"job_id": 1})["ok"] and nat.acked,
          "native adapter acks (cockpit got it off the bus)")

    a, addr = notify.parse_reply_to("webhook:https://h.io/cb?x=1")
    check(a == "webhook" and addr == "https://h.io/cb?x=1", "reply_to splits on the FIRST ':' only")

def test_retry_on_transient_failure():
    print("[k] at-least-once: a transiently-FAILED notification is retried (dedupe NOT burned on "
          "failure) and eventually delivered; a success is still deduped; poison is bounded")

    class FlakyAdapter(notify.Adapter):
        name = "flaky"
        def __init__(self, fail_first=1):
            self.fail_first = fail_first
            self.attempts = 0
            self.delivered = 0
        def deliver(self, address, content, kind, meta):
            self.attempts += 1
            if self.attempts <= self.fail_first:
                return {"ok": False, "error": "ConnectionError: transient"}
            self.delivered += 1
            return {"ok": True, "id": f"flaky-{self.delivered}"}

    fa = FlakyAdapter(fail_first=2)
    b = notify.NotifyBroker({"flaky": fa})
    e = ev(7, 60, "notify", {"task_type": "t", "principal": "alice", "state": "done",
                             "reply_to": "flaky:dev"})
    r1 = b.handle_event(e)
    check(not r1.get("ok") and not r1.get("deduped") and r1.get("retryable"),
          "1st attempt fails transiently and stays eligible for retry (not deduped, not delivered)")
    r2 = b.handle_event(e)
    check(not r2.get("ok") and not r2.get("deduped") and r2.get("retryable"),
          "2nd attempt (replay) is RE-DELIVERED to the adapter, not suppressed by dedupe")
    r3 = b.handle_event(e)
    check(r3.get("ok") and not r3.get("deduped"),
          "3rd attempt finally delivers (transient failure never burned the dedupe key)")
    check(fa.attempts == 3 and fa.delivered == 1,
          f"adapter saw every retry then delivered once (attempts={fa.attempts}, sent={fa.delivered})")

    r4 = b.handle_event(e)
    r5 = b.handle_event(e)
    check(r4.get("deduped") and r5.get("deduped"),
          "after a successful delivery the event is deduped on replay")
    check(fa.delivered == 1 and fa.attempts == 3,
          "a successfully-delivered notification is NOT delivered twice (idempotency preserved)")

    class PoisonAdapter(notify.Adapter):
        name = "poison"
        def __init__(self):
            self.attempts = 0
        def deliver(self, address, content, kind, meta):
            self.attempts += 1
            return {"ok": False, "permanent": True, "error": "bad address (4xx, will never accept)"}

    pa = PoisonAdapter()
    bp = notify.NotifyBroker({"poison": pa})
    ep = ev(8, 61, "notify", {"task_type": "t", "principal": "alice", "state": "done",
                              "reply_to": "poison:dev"})
    p1 = bp.handle_event(ep)
    p2 = bp.handle_event(ep)
    check(not p1.get("ok") and p1.get("dead_lettered") and p1.get("reason") == "permanent",
          "a permanent failure is dead-lettered immediately")
    check(p2.get("deduped") and pa.attempts == 1,
          "a poison message is NOT retried after a permanent failure (attempted exactly once)")

    always_fail = FlakyAdapter(fail_first=10**9)
    bb = notify.NotifyBroker({"flaky": always_fail}, max_attempts=3)
    eb = ev(9, 62, "notify", {"task_type": "t", "principal": "alice", "state": "done",
                              "reply_to": "flaky:dev"})
    results = [bb.handle_event(eb) for _ in range(8)]
    dead = [r for r in results if r.get("dead_lettered")]
    deduped = [r for r in results if r.get("deduped")]
    check(always_fail.attempts == 3,
          f"a forever-transient message is attempted at most max_attempts=3 (got {always_fail.attempts})")
    check(len(dead) == 1 and dead[0].get("reason") == "max_attempts",
          "it is dead-lettered exactly once when the retry budget is exhausted")
    check(len(deduped) == 5,
          "after dead-lettering, further replays are deduped (no infinite retry loop)")

def _scratch_pnd():

    rt = tempfile.mkdtemp(prefix="pn_nb_rt_")
    data = tempfile.mkdtemp(prefix="pn_nb_data_")
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = rt
    env["XDG_DATA_HOME"] = data
    env["PN_DATA_DIR"] = data
    env["PN_DURABILITY"] = "normal"
    env.pop("NOTIFY_SOCKET", None)
    boot = os.path.join(rt, "boot.py")
    with open(boot, "w") as f:
        f.write(
            "import sys, runpy\n"
            f"sys.path.insert(0, {ROOT!r})\n"
            "from pnlib import sched\n"
            "_orig = sched.Config.autoscale\n"
            "def _permissive():\n"
            "    c = _orig(); c.psi_stop = 1e9; c.mem_floor = 1; c.batch_high = 1<<30\n"
            "    c.slack = 0; return c\n"
            "sched.Config.autoscale = staticmethod(_permissive)\n"
            f"runpy.run_path({os.path.join(ROOT, 'tools', 'pnd')!r}, run_name='__main__')\n")
    proc = subprocess.Popen([sys.executable, boot], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    sock = os.path.join(rt, "pnd.sock")
    for _ in range(50):
        if os.path.exists(sock):
            break
        time.sleep(0.1)

    def ipc(req, timeout=20):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout); s.connect(sock)
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            ch = s.recv(65536)
            if not ch:
                break
            buf += ch
        s.close()
        return json.loads(buf.split(b"\n", 1)[0].decode())
    return proc, rt, data, sock, ipc

def _wait_state(ipc, jid, want, tries=120, every=0.2):
    st = None
    for _ in range(tries):
        jr = ipc({"verb": "job", "id": jid})
        if jr.get("ok"):
            st = jr["job"]["state"]
            if (want(st) if callable(want) else st == want):
                return st
        time.sleep(every)
    return st

def test_live_e2e():
    if not live_moeglich('test_live_e2e'):
        return
    print("[g] LIVE: a done job emits `notify` on user/<principal> with reply_to + digest; the broker "
          "subscribed to the bus delivers it once via a mock adapter (scratch pnd)")
    proc, rt, data, sock, ipc = _scratch_pnd()
    try:
        check(ipc({"verb": "ping"}).get("ok"), "scratch pnd up")
        worker = os.path.join(rt, "w.sh")
        with open(worker, "w") as f:
            f.write("#!/bin/bash\necho 'result' > \"$PN_WORKSPACE/artifacts/r.txt\"\necho ok\n")
        os.chmod(worker, 0o755)

        r = ipc({"verb": "submit", "cmd": [worker], "class": "worker", "tag": "notify-canary",
                 "reply_to": "mock:admin-device"})
        check(r.get("ok"), f"submit ok (id={r.get('id')})")
        jid = r["id"]
        st = _wait_state(ipc, jid, "done")
        check(st == "done", f"job reached done (state={st})")

        rep = ipc({"verb": "replay", "topics": ["user/admin"], "after_id": 0})
        notifies = [e for e in rep.get("events", []) if e["kind"] == "notify"]
        check(len(notifies) >= 1, "a notify event was emitted on user/admin")
        nd = json.loads(notifies[-1]["data"])
        check(nd.get("reply_to") == "mock:admin-device" and nd.get("principal") == "admin",
              "the notify payload carries the reply_to + the owning principal (no credential)")
        check(bool(nd.get("record_commit")) or nd.get("state") == "done",
              "the notify payload carries the Record digest / terminal state")

        mock = notify.MockAdapter("mock")
        b = notify.NotifyBroker({"mock": mock, "native": notify.NativeAdapter()})
        out = b.run_feed(rep["events"])
        out += b.run_feed(rep["events"])
        delivered = [o for o in out if o.get("ok") and o.get("adapter") == "mock"]
        check(len(mock.sent) == 1, f"the mock adapter delivered the notify EXACTLY once (got {len(mock.sent)})")
        check(mock.sent and mock.sent[0]["address"] == "admin-device",
              "delivered to the requester's channel address from reply_to")

        out_log = ipc({"verb": "log", "id": jid})
        check("BOT" not in (out_log.get("stdout") or "") and "token" not in (out_log.get("stdout") or "").lower(),
              "the task's own output never contained a channel credential")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

        try:
            import sqlite3
            cxv = sqlite3.connect(os.path.join(data, "portioneer", "queue.db"))
            ic = cxv.execute("PRAGMA integrity_check").fetchone()[0]
            cxv.close()
            check(ic == "ok", f"scratch DB integrity_check = {ic!r}")
        except Exception as e:
            check(False, f"scratch DB integrity_check raised {e}")
        subprocess.run(["rm", "-rf", rt, data])

def test_config_store_separate():
    print("[h] config lives in a SEPARATE store; only config-enabled real adapters are built")
    cfg = notifycfg.load()
    check(cfg["adapters"]["native"]["enabled"] and not cfg["adapters"]["telegram"]["enabled"]
          and not cfg["adapters"]["email"]["enabled"] and not cfg["adapters"]["webhook"]["enabled"],
          "shipped default: native ON, telegram/email/webhook OFF until configured")

    cfg["adapters"]["telegram"]["enabled"] = True
    cfg["adapters"]["email"]["enabled"] = True
    cfg["adapters"]["email"]["smtp_host"] = "mail.test"
    cfg["adapters"]["webhook"]["enabled"] = True
    saved = notifycfg.save(cfg)
    check(os.path.isfile(saved) and saved == notifycfg.CONFIG_PATH
          and saved.startswith(_NB_DATA) and "queue.db" not in saved,
          f"config persisted to a SEPARATE store under DATA (not queue.db): {os.path.basename(saved)}")
    b = notify.build_from_config(notifycfg.load(), cred_for=lambda n: None)
    check(set(b.adapters) >= {"native", "telegram", "email", "webhook"},
          "build_from_config assembled every config-enabled adapter + native")

    cfg2 = notifycfg.load(); cfg2["adapters"]["webhook"]["enabled"] = False
    b2 = notify.build_from_config(cfg2, cred_for=lambda n: None)
    check("webhook" not in b2.adapters, "a config-disabled adapter is NOT assembled")

    raw = open(saved).read().lower()
    check("token" not in raw and "password" not in raw and "secret" not in raw.replace("sign_header", ""),
          "the config file holds NO credential (token/password/secret)")

def test_sealed_channel_creds():
    print("[i] channel creds come ONLY from the SEALED per-channel store (never config/bus/task)")
    receipt = chsecrets.write_channel_cred("telegram", "BOT-TOKEN-SEALED-123")
    check(receipt["channel"] == "telegram" and "fingerprint" in receipt
          and "BOT-TOKEN" not in json.dumps(receipt),
          "write_channel_cred returns a receipt (fingerprint), NEVER the value")

    cred_file = os.path.join(os.environ["PN_BROKER_SECRETS_DIR"], "telegram.cred")
    raw = open(cred_file, "rb").read()
    check(b"BOT-TOKEN-SEALED-123" not in raw, "the sealed file does NOT contain the plaintext token")

    check(chsecrets.read_channel_cred("telegram") == "BOT-TOKEN-SEALED-123",
          "read_channel_cred unseals the value via the broker-only seam")

    st = chsecrets.status()
    check("telegram" in st["sealed_channels"] and st["dir"].startswith(_NB_DATA)
          and "secrets" != os.path.basename(st["dir"]),
          "the channel-cred store is a SEPARATE dir (not the brain secrets, not queue.db)")

    try:
        chsecrets.write_channel_cred("../escape", "x")
        ok = False
    except ValueError:
        ok = True
    check(ok, "an unsafe channel name is rejected (no path traversal)")

def test_real_transports_mocked():
    print("[j] the REAL transports send via stdlib (smtplib/urllib) — MOCKED here, no real I/O")

    import smtplib
    sent_mail = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=0):
            sent_mail["host"] = host; sent_mail["port"] = port
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, context=None): sent_mail["starttls"] = True
        def login(self, u, p): sent_mail["login"] = (u, p)
        def send_message(self, msg):
            sent_mail["to"] = msg["To"]; sent_mail["subj"] = msg["Subject"]
            sent_mail["body"] = msg.get_content()
    orig_smtp = smtplib.SMTP
    smtplib.SMTP = FakeSMTP
    try:
        cfg_email = {"smtp_host": "mail.test", "smtp_port": 587, "from_addr": "pn@test",
                     "smtp_user": "pn", "starttls": True}
        send = notifycfg.smtp_send(cfg_email)
        em = notify.EmailAdapter(send_email_fn=send, cred_for=lambda n: "SMTP-PASS-SEALED")
        r = em.deliver("a@b.c", "hello body", "notify", {"task_type": "t", "state": "done"})
        check(r["ok"] and sent_mail.get("to") == "a@b.c" and sent_mail.get("host") == "mail.test",
              "EmailAdapter -> real smtplib path (mocked): message built + sent")
        check(sent_mail.get("login") == ("pn", "SMTP-PASS-SEALED"),
              "the SMTP password came from the SEALED cred seam, not the config/bus")
    finally:
        smtplib.SMTP = orig_smtp

    import urllib.request
    posted = {}

    class FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def getcode(self): return 200
    def fake_urlopen(req, timeout=0):
        posted["url"] = req.full_url; posted["data"] = req.data
        posted["headers"] = {k.lower(): v for k, v in req.headers.items()}
        return FakeResp()
    orig_open = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        post = notifycfg.https_post({"timeout_s": 5, "sign_header": "X-PN-Signature"})
        wh = notify.WebhookAdapter(cred_for=lambda n: "WEBHOOK-SIGN-SECRET", post_fn=post)
        r = wh.deliver("https://hook.test/cb", "payload-content", "notify", {"job_id": 9})
        check(r["ok"] and posted.get("url") == "https://hook.test/cb",
              "WebhookAdapter -> real urllib POST (mocked): URL posted")
        check(any(h == "x-pn-signature" for h in posted.get("headers", {})),
              "the webhook body is HMAC-signed with the SEALED signing secret")
        check(b"payload-content" in posted.get("data", b""),
              "the redacted content is in the POSTed JSON body")
    finally:
        urllib.request.urlopen = orig_open

    tg_post = {}
    def fake_urlopen2(req, timeout=0):
        tg_post["url"] = req.full_url; tg_post["data"] = json.loads(req.data.decode())
        return FakeResp()
    urllib.request.urlopen = fake_urlopen2
    try:
        send_fn = notifycfg.telegram_send({"api_base": "https://api.telegram.org", "timeout_s": 5})
        tg = notify.TelegramAdapter(cred_for=lambda n: "TG-BOT-TOKEN", send_fn=send_fn)
        r = tg.deliver("123456", "tg text", "notify", {})
        check(r["ok"] and "/botTG-BOT-TOKEN/sendMessage" in tg_post.get("url", "")
              and tg_post.get("data", {}).get("chat_id") == "123456",
              "TelegramAdapter -> real Bot-API path (mocked): token from seal, chat_id from reply_to")
    finally:
        urllib.request.urlopen = orig_open

def main():
    print("=== notify-broker — test suite ===")
    test_routing()
    test_redaction()
    test_rate_limit()
    test_no_cred_leak()
    test_idempotent()
    test_retry_on_transient_failure()
    test_adapter_interface()
    test_config_store_separate()
    test_sealed_channel_creds()
    test_real_transports_mocked()
    test_live_e2e()
    import shutil as _sh
    _sh.rmtree(_NB_DATA, ignore_errors=True)
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
