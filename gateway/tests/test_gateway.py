#!/usr/bin/env python3

from __future__ import annotations
import os, sys, json, socket, struct, base64, threading, time, tempfile, unittest, urllib.request, urllib.error

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
sys.path.insert(0, _ROOT)

from gateway.server.config import Config, assert_safe_to_start
from gateway.server import app as gw_app
from gateway.server.auth import twofactor as totp
from gateway.server.auth import registry_shim as R
from gateway.server.adapters import media as media_adapter
from gateway.server.adapters import submit as submit_adapter
from gateway.server.adapters import webhooks as wh

class MockPnd:
    def __init__(self, path):
        self.path = path
        self.calls = []
        self._sock = None
        self._t = None

    def start(self):
        if os.path.exists(self.path):
            os.unlink(self.path)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(self.path)
        s.listen(16)
        self._sock = s
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def stop(self):
        try:
            self._sock.close()
        except Exception:
            pass

    def _serve(self):
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        try:
            buf = b""
            while b"\n" not in buf:
                ch = conn.recv(65536)
                if not ch:
                    return
                buf += ch
            req = json.loads(buf.split(b"\n", 1)[0].decode())
            self.calls.append((req.get("verb"), req.get("_method"), req.get("_selector")))
            resp = self._reply(req)
            if isinstance(resp, dict) and resp.get("_stream") == "events":

                conn.sendall((json.dumps({"ok": True, "type": "subscribed",
                                          "topics": req.get("topics"), "cursor": 0}) + "\n").encode())
                conn.sendall((json.dumps({"type": "event", "event": {
                    "id": 1, "ts": time.time(), "kind": "result",
                    "topic": req.get("topics", ["user/x"])[0], "data": {"job_id": 7}}}) + "\n").encode())
                return
            conn.sendall((json.dumps(resp) + "\n").encode())
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _reply(self, req):
        v = req.get("verb")
        if v == "ping":
            return {"ok": True, "version": "mock", "pid": 1}
        if v == "submit":

            assert req.get("_method") == "device-channel"
            assert "principal" not in req and "uid" not in req
            assert req.get("task_type") and "cmd" not in req
            staged = bool(req.get("needs_confirmation"))
            if staged:
                return {"ok": True, "id": 7, "state": "staged", "nonce": "NONCE7", "gate": "pre"}
            return {"ok": True, "id": 7, "pos": 1}
        if v == "cvm":
            return {"ok": True, "cvm": {"id": req.get("id"), "state": "running"}}
        if v == "result":
            return {"ok": True, "result": {"verdict": "ok", "confidence": 0.9}}
        if v == "my-jobs":
            return {"ok": True, "jobs": [{"id": 7, "state": "running"}]}
        if v == "pending":
            return {"ok": True, "pending": [{"nonce": "NONCE7", "job_id": 7}]}
        if v in ("approve", "reject", "revise", "deny"):
            return {"ok": True, "id": 7, "decision": v, "state": "done"}
        if v == "cancel":
            return {"ok": True, "state": "cancelled"}
        if v == "status":
            return {"ok": True, "counts": {"running": 1}, "version": "mock"}
        if v == "subscribe":
            return {"_stream": "events"}
        if v == "replay":
            return {"ok": True, "events": [], "cursor": 0}
        return {"ok": False, "error": f"unknown verb {v}"}

def _maybe_json(raw):
    try:
        return json.loads(raw.decode() or "{}")
    except ValueError:
        return raw.decode("utf-8", "replace")

def _http(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, _maybe_json(r.read())
    except urllib.error.HTTPError as e:
        return e.code, _maybe_json(e.read())

class GatewayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bb-gw-test-")
        cls.sock = os.path.join(cls.tmp, "pnd.sock")
        cls.relay_db = os.path.join(cls.tmp, "relay.db")
        cls.dropzone = os.path.join(cls.tmp, "intake")

        cls.pnd = MockPnd(cls.sock)
        cls.pnd.start()

        Config.PND_SOCK = cls.sock
        Config.RELAY_DB = cls.relay_db
        Config.DROPZONE = cls.dropzone
        Config.HOST = "127.0.0.1"
        Config.PORT = 0
        Config.REQUIRE_2FA = True
        srv = gw_app.make_server(Config)
        cls.port = srv.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}/v1"
        cls.srv = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.pnd.stop()

    _seq = 0

    def setUp(self):
        GatewayTest._seq += 1
        self.principal = f"u{self._seq}"
        self.did = f"did:key:z{self._seq}"
        cx = R.connect(self.relay_db)
        self.secret = R.arm_2fa(cx, self.principal)
        self.token = R.create_alliance(cx, device_did=self.did, principal=self.principal,
                                       caps=["task.submit"], label="t")
        cx.close()
        self._step = 0

    def _auth(self, code=None):
        if code is None:

            offsets = [-30, 0, 30]
            off = offsets[min(self._step, 2)]
            self._step += 1
            code = totp.code_at(self.secret, ts=time.time() + off)
        return {"Authorization": f"Bearer {self.did}.{self.token}",
                "X-Brainarbeit-2FA": code}

    def test_health_unauthenticated(self):
        st, body = _http("GET", self.base + "/health")
        self.assertEqual(st, 200)
        self.assertTrue(body["ok"])

    def test_missing_credential_401(self):
        st, body = _http("GET", self.base + "/jobs/mine")
        self.assertEqual(st, 401)

    def test_missing_2fa_401(self):
        st, body = _http("GET", self.base + "/jobs/mine",
                         headers={"Authorization": f"Bearer {self.did}.{self.token}"})
        self.assertEqual(st, 401)

    def test_bad_token_401(self):
        st, _ = _http("GET", self.base + "/jobs/mine",
                      headers={"Authorization": f"Bearer {self.did}.WRONG",
                               "X-Brainarbeit-2FA": totp.code_at(self.secret)})
        self.assertEqual(st, 401)

    def test_totp_replay_rejected(self):
        code = totp.code_at(self.secret)
        st1, _ = _http("GET", self.base + "/jobs/mine", headers=self._auth(code))
        self.assertEqual(st1, 200)
        st2, _ = _http("GET", self.base + "/jobs/mine", headers=self._auth(code))
        self.assertEqual(st2, 401)

    def test_submit_typed_ok_and_broker_stamped(self):
        before = len(self.pnd.calls)
        st, body = _http("POST", self.base + "/jobs",
                         {"task_type": "summarize.document", "params": {"x": 1}},
                         headers=self._auth())
        self.assertEqual(st, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["id"], 7)
        verb, method, selector = self.pnd.calls[-1]
        self.assertEqual(verb, "submit")
        self.assertEqual(method, "device-channel")
        self.assertEqual(selector, self.did)

    def test_raw_cmd_refused_403(self):
        st, body = _http("POST", self.base + "/jobs",
                         {"task_type": "x", "cmd": "rm -rf /"}, headers=self._auth())
        self.assertEqual(st, 403)
        self.assertIn("raw", body["error"])

    def test_attachment_staged_to_pathref(self):
        payload = base64.b64encode(b"hello-bytes").decode()
        st, body = _http("POST", self.base + "/jobs",
                         {"task_type": "fill.form",
                          "attachments": [{"filename": "a.txt", "content_b64": payload}]},
                         headers=self._auth())
        self.assertEqual(st, 200)

        found = []
        for root, _dirs, files in os.walk(self.dropzone):
            for f in files:
                found.append(os.path.join(root, f))
        self.assertTrue(any(p.endswith("a.txt") for p in found))
        with open([p for p in found if p.endswith("a.txt")][0], "rb") as fh:
            self.assertEqual(fh.read(), b"hello-bytes")

    def test_read_status_result_mine(self):
        self.assertEqual(_http("GET", self.base + "/jobs/7/cvm", headers=self._auth())[0], 200)
        self.assertEqual(_http("GET", self.base + "/jobs/7/result", headers=self._auth())[0], 200)
        self.assertEqual(_http("GET", self.base + "/jobs/mine", headers=self._auth())[0], 200)

    def test_read_approvals_engine(self):
        self.assertEqual(_http("GET", self.base + "/approvals", headers=self._auth())[0], 200)
        self.assertEqual(_http("GET", self.base + "/engine/status", headers=self._auth())[0], 200)

    def test_approval_resolution(self):
        st, body = _http("POST", self.base + "/approvals/NONCE7",
                         {"decision": "approve"}, headers=self._auth())
        self.assertEqual(st, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["decision"], "approve")

    def test_bad_decision_400(self):
        st, _ = _http("POST", self.base + "/approvals/NONCE7",
                      {"decision": "nuke"}, headers=self._auth())
        self.assertEqual(st, 400)

    def test_steer_and_cancel(self):
        self.assertEqual(_http("POST", self.base + "/jobs/7/steer",
                               {"input": "go metric"}, headers=self._auth())[0], 200)
        self.assertEqual(_http("POST", self.base + "/jobs/7/cancel", headers=self._auth())[0], 200)

    def test_specs_served(self):
        for name in ("openapi.yaml", "asyncapi.yaml"):
            st, _ = _http("GET", self.base + "/" + name)
            self.assertEqual(st, 200)

    def test_revocation_blocks(self):

        self.assertEqual(_http("GET", self.base + "/jobs/mine", headers=self._auth())[0], 200)
        cx = R.connect(self.relay_db)
        R.revoke(cx, self.did)
        cx.close()

        self.assertEqual(_http("GET", self.base + "/jobs/mine", headers=self._auth())[0], 401)

    def test_ws_event_stream(self):
        frames = self._ws_collect("/stream?topics=user/alice", limit=2)
        kinds = [f.get("type") for f in frames]
        self.assertIn("subscribed", kinds)
        self.assertIn("event", kinds)

    def _ws_collect(self, path, limit=2):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        code = self._auth()["X-Brainarbeit-2FA"]
        req = (f"GET /v1{path} HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
               f"Authorization: Bearer {self.did}.{self.token}\r\n"
               f"X-Brainarbeit-2FA: {code}\r\n\r\n")
        s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += s.recv(4096)
        pending = buf.split(b"\r\n\r\n", 1)[1]
        out = []

        def recv(n):
            nonlocal pending
            while len(pending) < n:
                c = s.recv(65536)
                if not c:
                    return None
                pending += c
            r, pending = pending[:n], pending[n:]
            return r
        while len(out) < limit:
            hdr = recv(2)
            if not hdr:
                break
            ln = hdr[1] & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", recv(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", recv(8))[0]
            payload = recv(ln) if ln else b""
            if (hdr[0] & 0x0F) == 0x8 or payload is None:
                break
            try:
                out.append(json.loads(payload.decode()))
            except ValueError:
                pass
        s.close()
        return out

    def _token_hash(self, did):
        cx = R.connect(self.relay_db)
        try:
            return R.get_alliance(cx, did)["token_hash"]
        finally:
            cx.close()

    def test_media_ticket_minted_and_verifies_with_bind(self):

        st, body = _http("POST", self.base + "/media/ticket", {}, headers=self._auth())
        self.assertEqual(st, 200)
        ticket = body["ticket"]

        import hashlib
        base = (Config.SECRET or Config.DEV_SECRET).encode()
        dev_secret = hashlib.blake2s(self.did.encode(), key=base[:32].ljust(32, b"0"),
                                     digest_size=32).digest()
        bind = self._token_hash(self.did)

        self.assertEqual(media_adapter.verify_ticket(dev_secret, ticket, bind=bind), self.did)

        self.assertIsNone(media_adapter.verify_ticket(dev_secret, ticket, bind=None))
        self.assertIsNone(media_adapter.verify_ticket(dev_secret, ticket, bind="not-the-hash"))

    def test_forged_media_ticket_rejected_even_with_default_secret(self):

        import hashlib
        default_base = Config.DEV_SECRET.encode()
        victim_secret = hashlib.blake2s(self.did.encode(),
                                        key=default_base[:32].ljust(32, b"0"),
                                        digest_size=32).digest()
        forged = media_adapter.mint_ticket(victim_secret, self.did, bind=None)

        real_bind = self._token_hash(self.did)
        self.assertIsNone(media_adapter.verify_ticket(victim_secret, forged, bind=real_bind))

        st = self._ws_status(f"/stream?ticket={urllib.parse.quote(forged)}")
        self.assertEqual(st, 401)

    def test_valid_media_ticket_opens_stream(self):

        st, body = _http("POST", self.base + "/media/ticket", {}, headers=self._auth())
        self.assertEqual(st, 200)
        ticket = body["ticket"]
        st = self._ws_status(f"/stream?ticket={urllib.parse.quote(ticket)}")
        self.assertEqual(st, 101)

    def _ws_status(self, path):

        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET /v1{path} HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        s.sendall(req.encode())
        buf = b""
        try:
            while b"\r\n" not in buf:
                c = s.recv(4096)
                if not c:
                    break
                buf += c
        finally:
            s.close()
        line = buf.split(b"\r\n", 1)[0].decode("latin1")
        try:
            return int(line.split(" ")[1])
        except (IndexError, ValueError):
            return 0

class _Cfg:

    REQUIRE_2FA = True
    ALLOW_NO_2FA = False
    HOST = "127.0.0.1"
    CERT = None
    KEY = None
    DEV_SECRET = Config.DEV_SECRET
    SECRET = None

class ConfigSafetyTest(unittest.TestCase):
    def _cfg(self, **kw):
        c = _Cfg()
        for k, v in kw.items():
            setattr(c, k, v)
        return c

    def test_offloopback_tls_but_secret_unset_refuses(self):
        c = self._cfg(HOST="0.0.0.0", CERT="/x/cert.pem", KEY="/x/key.pem", SECRET=None)
        with self.assertRaises(SystemExit):
            assert_safe_to_start(c)

    def test_offloopback_tls_but_secret_is_default_refuses(self):
        c = self._cfg(HOST="0.0.0.0", CERT="/x/cert.pem", KEY="/x/key.pem",
                      SECRET=Config.DEV_SECRET)
        with self.assertRaises(SystemExit):
            assert_safe_to_start(c)

    def test_offloopback_tls_with_good_secret_ok(self):
        c = self._cfg(HOST="0.0.0.0", CERT="/x/cert.pem", KEY="/x/key.pem",
                      SECRET="x9f2c7c8e1a64b0d-very-long-random-operator-secret-value")

        assert_safe_to_start(c)

    def test_offloopback_without_tls_still_refuses(self):
        c = self._cfg(HOST="0.0.0.0", CERT=None, KEY=None,
                      SECRET="x9f2c7c8e1a64b0d-very-long-random-operator-secret-value")
        with self.assertRaises(SystemExit):
            assert_safe_to_start(c)

    def test_loopback_with_default_secret_allowed_but_warns(self):
        c = self._cfg(HOST="127.0.0.1", SECRET=None)

        assert_safe_to_start(c)

class _WhCfg:
    def __init__(self, allow):
        self.WEBHOOK_ALLOW_HOSTS = list(allow)

class WebhookSsrfTest(unittest.TestCase):
    def test_empty_allowlist_denies_everything(self):
        cfg = _WhCfg([])
        for host in ("attacker.example", "169.254.169.254", "localhost", "example.com"):
            with self.assertRaises(wh.WebhookError) as ctx:
                wh.validate_url(cfg, f"https://{host}/hook")
            self.assertEqual(ctx.exception.code, 403)

    def test_non_https_rejected(self):
        with self.assertRaises(wh.WebhookError):
            wh.validate_url(_WhCfg(["hooks.example.com"]), "http://hooks.example.com/h")

    def test_allowlisted_public_host_passes(self):

        cfg = _WhCfg(["hooks.example.com"])
        orig = wh.socket.getaddrinfo
        wh.socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]
        try:
            out = wh.validate_url(cfg, "https://hooks.example.com/hook")
            self.assertEqual(out, "https://hooks.example.com/hook")
        finally:
            wh.socket.getaddrinfo = orig

    def test_allowlisted_name_resolving_internal_blocked(self):

        cfg = _WhCfg(["sneaky.example.com"])
        orig = wh.socket.getaddrinfo
        wh.socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", ("169.254.169.254", 443))]
        try:
            with self.assertRaises(wh.WebhookError) as ctx:
                wh.validate_url(cfg, "https://sneaky.example.com/hook")
            self.assertEqual(ctx.exception.code, 403)
        finally:
            wh.socket.getaddrinfo = orig

    def test_allowlisted_name_to_internal_ip_blocked(self):

        for ip in ("169.254.169.254", "127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1"):
            cfg = _WhCfg([ip])
            with self.assertRaises(wh.WebhookError) as ctx:
                wh.validate_url(cfg, f"https://{ip}/hook")
            self.assertEqual(ctx.exception.code, 403)

    def test_allowlisted_public_literal_ip_passes(self):
        cfg = _WhCfg(["93.184.216.34"])
        out = wh.validate_url(cfg, "https://93.184.216.34/hook")
        self.assertEqual(out, "https://93.184.216.34/hook")

    def test_cgnat_and_benchmark_literal_ips_blocked(self):

        for ip in ("100.64.13.37", "100.64.0.0", "100.127.255.255", "198.18.0.5", "198.19.255.255"):
            cfg = _WhCfg([ip])
            with self.assertRaises(wh.WebhookError) as ctx:
                wh.validate_url(cfg, f"https://{ip}/hook")
            self.assertEqual(ctx.exception.code, 403)

    def test_ip_is_blocked_unit_cgnat(self):

        self.assertTrue(wh._ip_is_blocked("100.64.13.37"))
        self.assertTrue(wh._ip_is_blocked("::ffff:100.64.13.37"))
        self.assertTrue(wh._ip_is_blocked("198.18.0.5"))
        self.assertFalse(wh._ip_is_blocked("93.184.216.34"))

    def test_allowlisted_name_resolving_cgnat_blocked(self):

        cfg = _WhCfg(["sneaky.example.com"])
        orig = wh.socket.getaddrinfo
        wh.socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", ("100.64.13.37", 443))]
        try:
            with self.assertRaises(wh.WebhookError) as ctx:
                wh.validate_url(cfg, "https://sneaky.example.com/hook")
            self.assertEqual(ctx.exception.code, 403)
        finally:
            wh.socket.getaddrinfo = orig

    def test_submit_reply_to_userinfo_at_rejected(self):

        cfg = _WhCfg(["good.example.com"])
        orig = wh.socket.getaddrinfo
        wh.socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]
        try:
            body = {"task_type": "x",
                    "reply_to": "webhook:https://169.254.169.254@good.example.com/x"}
            with self.assertRaises(submit_adapter.SubmitError):
                submit_adapter.build_submit_request(cfg, body, [])
        finally:
            wh.socket.getaddrinfo = orig

    def test_submit_reply_to_backslash_rejected(self):

        cfg = _WhCfg(["good.example.com"])
        orig = wh.socket.getaddrinfo
        wh.socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]
        try:
            body = {"task_type": "x",
                    "reply_to": "webhook:https://good.example.com/a\\..\\evil"}
            with self.assertRaises(submit_adapter.SubmitError):
                submit_adapter.build_submit_request(cfg, body, [])
        finally:
            wh.socket.getaddrinfo = orig

    def test_submit_reply_to_canonicalized_no_ambiguity(self):

        cfg = _WhCfg(["hooks.example.com"])
        orig = wh.socket.getaddrinfo
        wh.socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]
        try:
            body = {"task_type": "x", "reply_to": "webhook:https://hooks.example.com/p?q=1"}
            req = submit_adapter.build_submit_request(cfg, body, [])
            self.assertEqual(req["reply_to"], "webhook:https://hooks.example.com/p?q=1")
        finally:
            wh.socket.getaddrinfo = orig

    def test_submit_reply_to_space_inside_scheme_rejected(self):

        cfg = _WhCfg([])
        body = {"task_type": "x", "reply_to": "web hook:https://169.254.169.254/steal"}
        with self.assertRaises(submit_adapter.SubmitError) as ctx:
            submit_adapter.build_submit_request(cfg, body, [])
        self.assertEqual(ctx.exception.code, 403)

    def test_submit_reply_to_space_before_colon_rejected(self):

        cfg = _WhCfg([])
        body = {"task_type": "x", "reply_to": "webhook :https://169.254.169.254/steal"}
        with self.assertRaises(submit_adapter.SubmitError) as ctx:
            submit_adapter.build_submit_request(cfg, body, [])
        self.assertEqual(ctx.exception.code, 403)

    def test_submit_reply_to_nul_prefixed_scheme_rejected(self):

        cfg = _WhCfg([])
        body = {"task_type": "x", "reply_to": "\x00webhook:https://169.254.169.254/steal"}
        with self.assertRaises(submit_adapter.SubmitError) as ctx:
            submit_adapter.build_submit_request(cfg, body, [])
        self.assertEqual(ctx.exception.code, 403)

    def test_submit_reply_to_cgnat_host_rejected(self):

        cfg = _WhCfg([])
        body = {"task_type": "x", "reply_to": "webhook:https://100.64.13.37/steal"}
        with self.assertRaises(submit_adapter.SubmitError) as ctx:
            submit_adapter.build_submit_request(cfg, body, [])
        self.assertEqual(ctx.exception.code, 403)

    def test_submit_reply_to_webhook_denied_host_rejected(self):
        cfg = _WhCfg([])
        body = {"task_type": "x", "reply_to": "webhook:https://169.254.169.254/steal"}
        with self.assertRaises(submit_adapter.SubmitError) as ctx:
            submit_adapter.build_submit_request(cfg, body, [])
        self.assertEqual(ctx.exception.code, 403)

    def test_submit_reply_to_webhook_allowed_host_passes(self):
        cfg = _WhCfg(["93.184.216.34"])
        body = {"task_type": "x", "reply_to": "webhook:https://93.184.216.34/hook"}
        req = submit_adapter.build_submit_request(cfg, body, [])
        self.assertEqual(req["reply_to"], "webhook:https://93.184.216.34/hook")

    def test_submit_non_webhook_reply_to_untouched(self):

        cfg = _WhCfg([])
        body = {"task_type": "x", "reply_to": "telegram:12345"}
        req = submit_adapter.build_submit_request(cfg, body, [])
        self.assertEqual(req["reply_to"], "telegram:12345")

    def test_submit_reply_to_mixed_case_scheme_to_denied_host_rejected(self):

        cfg = _WhCfg([])
        body = {"task_type": "x", "reply_to": "Webhook:https://169.254.169.254/steal"}
        with self.assertRaises(submit_adapter.SubmitError) as ctx:
            submit_adapter.build_submit_request(cfg, body, [])
        self.assertEqual(ctx.exception.code, 403)

    def test_submit_reply_to_leading_space_scheme_to_denied_host_rejected(self):

        cfg = _WhCfg([])
        body = {"task_type": "x", "reply_to": " webhook:https://169.254.169.254/steal"}
        with self.assertRaises(submit_adapter.SubmitError) as ctx:
            submit_adapter.build_submit_request(cfg, body, [])
        self.assertEqual(ctx.exception.code, 403)

    def test_submit_reply_to_mixed_case_allowed_host_passes_and_normalized(self):

        cfg = _WhCfg(["93.184.216.34"])
        body = {"task_type": "x", "reply_to": " Webhook:https://93.184.216.34/hook"}
        req = submit_adapter.build_submit_request(cfg, body, [])
        self.assertEqual(req["reply_to"], "webhook:https://93.184.216.34/hook")

class WebhookRebindDeliverTest(unittest.TestCase):
    def test_deliver_blocked_when_name_rebinds_to_internal(self):

        cfg = _WhCfg(["flip.example.com"])
        seq = iter([
            [(2, 1, 6, "", ("93.184.216.34", 443))],
            [(2, 1, 6, "", ("169.254.169.254", 443))],
        ])
        orig_gai = wh.socket.getaddrinfo
        wh.socket.getaddrinfo = lambda *a, **k: next(seq)

        orig_conn = wh._PinnedHTTPSConnection

        class _ExplodingConn(orig_conn):
            def connect(self):
                raise AssertionError("connect() reached despite a rebound internal address")
        wh._PinnedHTTPSConnection = _ExplodingConn
        try:
            url = wh.validate_url(cfg, "https://flip.example.com/hook")
            ok, detail = wh.deliver(url, {"id": 1, "kind": "result"}, b"secret")
            self.assertFalse(ok)
            self.assertIn("169.254.169.254", detail)
        finally:
            wh.socket.getaddrinfo = orig_gai
            wh._PinnedHTTPSConnection = orig_conn

    def test_deliver_blocked_when_name_rebinds_to_cgnat(self):

        cfg = _WhCfg(["flip.example.com"])
        seq = iter([
            [(2, 1, 6, "", ("93.184.216.34", 443))],
            [(2, 1, 6, "", ("100.64.13.37", 443))],
        ])
        orig_gai = wh.socket.getaddrinfo
        wh.socket.getaddrinfo = lambda *a, **k: next(seq)
        orig_conn = wh._PinnedHTTPSConnection

        class _ExplodingConn(orig_conn):
            def connect(self):
                raise AssertionError("connect() reached despite a rebound CGNAT address")
        wh._PinnedHTTPSConnection = _ExplodingConn
        try:
            url = wh.validate_url(cfg, "https://flip.example.com/hook")
            ok, detail = wh.deliver(url, {"id": 1, "kind": "result"}, b"secret")
            self.assertFalse(ok)
            self.assertIn("100.64.13.37", detail)
        finally:
            wh.socket.getaddrinfo = orig_gai
            wh._PinnedHTTPSConnection = orig_conn

    def test_deliver_to_legitimate_public_host_succeeds(self):

        cfg = _WhCfg(["hooks.example.com"])
        orig_gai = wh.socket.getaddrinfo
        wh.socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]
        recorded = {}

        class _FakeResp:
            status = 204
            def read(self):
                return b""

        class _FakeConn:
            def __init__(self, host, pinned_ip, **kw):
                recorded["host"] = host
                recorded["pinned_ip"] = pinned_ip
            def request(self, method, path, body=None, headers=None):
                recorded["method"] = method
                recorded["path"] = path
                recorded["headers"] = headers
            def getresponse(self):
                return _FakeResp()
            def close(self):
                pass

        orig_conn = wh._PinnedHTTPSConnection
        wh._PinnedHTTPSConnection = _FakeConn
        try:
            url = wh.validate_url(cfg, "https://hooks.example.com/hook")
            ok, detail = wh.deliver(url, {"id": 5, "kind": "result"}, b"secret")
            self.assertTrue(ok, detail)
            self.assertEqual(detail, "HTTP 204")

            self.assertEqual(recorded["pinned_ip"], "93.184.216.34")
            self.assertEqual(recorded["host"], "hooks.example.com")

            self.assertIn("X-Brainarbeit-Signature", recorded["headers"])
            self.assertEqual(recorded["headers"]["Host"], "hooks.example.com")
        finally:
            wh.socket.getaddrinfo = orig_gai
            wh._PinnedHTTPSConnection = orig_conn

    def test_deliver_pinned_connection_preserves_tls_sni_hostname(self):

        captured = {}

        class _FakeCtx:
            def wrap_socket(self, sock, server_hostname=None):
                captured["server_hostname"] = server_hostname
                return sock

        class _FakeSock:
            def close(self):
                pass

        orig_create = wh.socket.create_connection
        wh.socket.create_connection = lambda addr, **k: (captured.update(connect_addr=addr)
                                                         or _FakeSock())
        try:
            conn = wh._PinnedHTTPSConnection("hooks.example.com", "93.184.216.34",
                                             port=443, context=_FakeCtx())
            conn.connect()

            self.assertEqual(captured["connect_addr"], ("93.184.216.34", 443))
            self.assertEqual(captured["server_hostname"], "hooks.example.com")
        finally:
            wh.socket.create_connection = orig_create

if __name__ == "__main__":
    unittest.main(verbosity=2)
