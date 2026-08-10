
from __future__ import annotations
import json, hmac, hashlib, time, ipaddress, socket, urllib.parse, urllib.request, ssl
import http.client

class WebhookError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code

_DENY_CIDRS = tuple(ipaddress.ip_network(c) for c in (
    "100.64.0.0/10",
    "198.18.0.0/15",
))

def _ip_is_blocked(ip: str) -> bool:

    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if (a.is_loopback or a.is_link_local or a.is_private or a.is_reserved
            or a.is_multicast or a.is_unspecified):
        return True

    if a.version == 6 and a.ipv4_mapped is not None:
        a = a.ipv4_mapped
    return any(a in net for net in _DENY_CIDRS)

def _vet_addresses(hostname: str, port: int) -> list[str]:

    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except OSError as e:
        raise WebhookError(f"host {hostname} did not resolve: {e}", code=403)
    ips = [info[4][0] for info in infos]
    if not ips:
        raise WebhookError(f"host {hostname} did not resolve to any address", code=403)
    for ip in ips:
        if _ip_is_blocked(ip):
            raise WebhookError(f"host {hostname} resolves to internal address {ip} "
                               "(SSRF / metadata-endpoint guard)", code=403)
    return ips

def canonicalize_url(url: str) -> str:

    if "\\" in url:
        raise WebhookError("webhook URL must not contain a backslash (parser-ambiguity guard)")
    u = urllib.parse.urlparse(url)
    if u.scheme != "https":
        raise WebhookError("webhook URL must be https://")
    if not u.hostname:
        raise WebhookError("webhook URL has no host")

    if u.username is not None or u.password is not None or "@" in (u.netloc or ""):
        raise WebhookError("webhook URL must not contain embedded userinfo '@' "
                           "(parser-ambiguity / SSRF differential guard)")

    host = u.hostname
    if ":" in host:
        host = f"[{host}]"
    netloc = host if u.port is None else f"{host}:{u.port}"
    return urllib.parse.urlunparse(("https", netloc, u.path, u.params, u.query, u.fragment))

def validate_url(cfg, url: str) -> str:

    u = urllib.parse.urlparse(url)
    if u.scheme != "https":
        raise WebhookError("webhook URL must be https://")
    if not u.hostname:
        raise WebhookError("webhook URL has no host")
    allow = cfg.WEBHOOK_ALLOW_HOSTS

    if not allow or u.hostname not in allow:
        raise WebhookError(f"host {u.hostname} not in the webhook allow-list "
                           "(GW_WEBHOOK_HOSTS) — default-deny (empty list permits no host)",
                           code=403)

    try:
        ipaddress.ip_address(u.hostname)
        if _ip_is_blocked(u.hostname):
            raise WebhookError(f"host {u.hostname} resolves to a non-routable/internal address "
                               "(SSRF guard)", code=403)
        return url
    except ValueError:
        pass

    _vet_addresses(u.hostname, u.port or 443)
    return url

def sign(secret: bytes, body: bytes) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()

class _PinnedHTTPSConnection(http.client.HTTPSConnection):

    def __init__(self, host, pinned_ip, **kw):
        super().__init__(host, **kw)
        self._pinned_ip = pinned_ip

    def connect(self):

        sock = socket.create_connection((self._pinned_ip, self.port), timeout=self.timeout,
                                        source_address=self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)

def deliver(url: str, event: dict, secret: bytes, *, timeout=8) -> tuple[bool, str]:

    u = urllib.parse.urlparse(url)
    if u.scheme != "https":
        return False, "WebhookError: webhook URL must be https://"
    if not u.hostname:
        return False, "WebhookError: webhook URL has no host"
    port = u.port or 443

    body = json.dumps(event, separators=(",", ":")).encode()
    ctx = ssl.create_default_context()

    try:
        ipaddress.ip_address(u.hostname)
        if _ip_is_blocked(u.hostname):
            return False, ("WebhookError: host resolves to a non-routable/internal address "
                           "(SSRF guard)")
        pinned_ip = u.hostname
    except ValueError:
        try:
            pinned_ip = _vet_addresses(u.hostname, port)[0]
        except WebhookError as e:
            return False, f"WebhookError: {e}"

    conn = _PinnedHTTPSConnection(u.hostname, pinned_ip, port=port, timeout=timeout, context=ctx)
    path = u.path or "/"
    if u.query:
        path += "?" + u.query
    headers = {
        "Content-Type": "application/json",
        "X-Brainarbeit-Signature": sign(secret, body),
        "X-Brainarbeit-Delivery": str(event.get("id", "")),
        "User-Agent": "Brainarbeit-Gateway/1.0",

        "Host": u.netloc,
    }
    try:
        conn.request("POST", path, body=body, headers=headers)
        r = conn.getresponse()
        r.read()
        return (200 <= r.status < 300), f"HTTP {r.status}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass

class WebhookRelay:

    def __init__(self, cfg, pnd, device_did: str, topics: list[str], url: str, secret: bytes):
        self.cfg = cfg
        self.pnd = pnd
        self.device_did = device_did
        self.topics = topics
        self.url = validate_url(cfg, url)
        self.secret = secret
        self.cursor = 0
        self._stop = False
        self._sock = None

    def run(self):
        sub = {"verb": "subscribe", "topics": self.topics}
        if self.cursor:
            sub["after_id"] = self.cursor
        self._sock, frames = self.pnd.stream(sub, device_did=self.device_did)
        for frame in frames:
            if self._stop:
                break
            if frame.get("type") != "event":
                continue
            ev = frame["event"]
            ok, _ = deliver(self.url, ev, self.secret)
            if ok:
                self.cursor = max(self.cursor, ev.get("id", self.cursor))

    def stop(self):
        self._stop = True
        try:
            self._sock and self._sock.close()
        except Exception:
            pass
