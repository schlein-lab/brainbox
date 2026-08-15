
from __future__ import annotations

import socket
import struct
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_PROBE_BYTES = 1 << 20

class DriverError(Exception):
    pass

class DriverUnavailable(DriverError):
    pass

KIND_NET = "net"
KIND_RF = "rf"
KIND_IR = "ir"
KIND_BT = "bt"
KIND_ZIGBEE = "zigbee"
KIND_SERIAL = "serial"
KIND_BRIDGE = "bridge"

@dataclass
class Probe:

    scheme: str
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    stub: bool = False

class TransportDriver:

    scheme: str = ""
    kind: str = KIND_NET
    default_port: int | None = None
    readonly: bool = True

    contributes: tuple[str, ...] = ()
    description: str = ""

    def available(self) -> bool:

        return True

    def probe(self, host: str, port: int | None = None, timeout: float = 2.0, **kw) -> Probe:
        raise NotImplementedError

class SNMPDriver(TransportDriver):

    scheme = "snmp"
    kind = KIND_NET
    default_port = 161
    contributes = ("identify", "health-read")
    description = "SNMP sysDescr.0 GET (read-only fingerprint)"

    _SYSDESCR_OID = (1, 3, 6, 1, 2, 1, 1, 1, 0)

    @staticmethod
    def _tlv(tag: int, value: bytes) -> bytes:

        return bytes([tag, len(value)]) + value

    @classmethod
    def _encode_oid(cls, oid: tuple[int, ...]) -> bytes:

        body = bytes([40 * oid[0] + oid[1]])
        for sub in oid[2:]:
            if sub < 128:
                body += bytes([sub])
            else:

                chunks = []
                while sub:
                    chunks.insert(0, sub & 0x7F)
                    sub >>= 7
                for i in range(len(chunks) - 1):
                    chunks[i] |= 0x80
                body += bytes(chunks)
        return cls._tlv(0x06, body)

    @classmethod
    def build_get_request(cls, community: str = "public", request_id: int = 1) -> bytes:

        varbind = cls._tlv(0x30, cls._encode_oid(cls._SYSDESCR_OID) + cls._tlv(0x05, b""))
        varbind_list = cls._tlv(0x30, varbind)
        pdu = cls._tlv(
            0xA0,
            cls._tlv(0x02, request_id.to_bytes(1, "big")) +
            cls._tlv(0x02, b"\x00") +
            cls._tlv(0x02, b"\x00") +
            varbind_list)
        msg = cls._tlv(
            0x30,
            cls._tlv(0x02, b"\x01") +
            cls._tlv(0x04, community.encode()) +
            pdu)
        return msg

    @staticmethod
    def _parse_octet_string(resp: bytes) -> str | None:

        i = resp.find(b"\xA2")
        scan = resp[i:] if i >= 0 else resp
        j = 0
        seen_oid = False
        while j < len(scan) - 1:
            tag = scan[j]
            ln = scan[j + 1]
            val = scan[j + 2:j + 2 + ln]
            if tag == 0x06:
                seen_oid = True
                j += 2 + ln
                continue
            if tag == 0x04 and seen_oid:
                try:
                    return val.decode("utf-8", "replace")
                except Exception:
                    return None
            j += 1
        return None

    def probe(self, host: str, port: int | None = None, timeout: float = 2.0,
              community: str = "public", **kw) -> Probe:
        port = port or self.default_port
        pkt = self.build_get_request(community)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            s.sendto(pkt, (host, port))
            resp, _ = s.recvfrom(4096)
        except (socket.timeout, OSError) as e:
            return Probe(self.scheme, False, error=f"snmp probe failed: {e}")
        finally:
            s.close()
        descr = self._parse_octet_string(resp)
        if descr is None:
            return Probe(self.scheme, False, error="no sysDescr in response")
        return Probe(self.scheme, True, data={"sysDescr": descr})

class SMBDriver(TransportDriver):

    scheme = "smb"
    kind = KIND_NET
    default_port = 445
    contributes = ("file-read", "file-write", "drop-zone")
    description = "SMB/CIFS read-only share enumeration (guest LIST)"

    def probe(self, host: str, port: int | None = None, timeout: float = 2.0, **kw) -> Probe:
        port = port or self.default_port
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((host, port))

            s.sendall(b"LIST\n")
            buf = b""
            while b"\n" not in buf:
                ch = s.recv(4096)
                if not ch:
                    break
                buf += ch
                if len(buf) > MAX_PROBE_BYTES:
                    return Probe(self.scheme, False, error="smb list response too large")
        except (socket.timeout, OSError) as e:
            return Probe(self.scheme, False, error=f"smb list failed: {e}")
        finally:
            s.close()
        import json
        try:
            shares = json.loads(buf.split(b"\n", 1)[0].decode())
        except (ValueError, UnicodeDecodeError) as e:
            return Probe(self.scheme, False, error=f"bad smb response: {e}")
        return Probe(self.scheme, True, data={"shares": shares})

class IPPDriver(TransportDriver):

    scheme = "ipp"
    kind = KIND_NET
    default_port = 631
    contributes = ("print", "identify", "health-read")
    description = "IPP Get-Printer-Attributes (read-only)"

    OP_GET_PRINTER_ATTRIBUTES = 0x000B

    @classmethod
    def build_get_printer_attributes(cls, uri: str, request_id: int = 1) -> bytes:

        def attr(value_tag, name, value):
            return (bytes([value_tag])
                    + len(name).to_bytes(2, "big") + name.encode()
                    + len(value).to_bytes(2, "big") + value.encode())
        body = b"\x01\x01"
        body += cls.OP_GET_PRINTER_ATTRIBUTES.to_bytes(2, "big")
        body += request_id.to_bytes(4, "big")
        body += b"\x01"
        body += attr(0x47, "attributes-charset", "utf-8")
        body += attr(0x48, "attributes-natural-language", "en")
        body += attr(0x45, "printer-uri", uri)
        body += b"\x03"
        return body

    def probe(self, host: str, port: int | None = None, timeout: float = 2.0, **kw) -> Probe:
        port = port or self.default_port
        uri = f"ipp://{host}:{port}/ipp/print"
        body = self.build_get_printer_attributes(uri)
        req = (f"POST /ipp/print HTTP/1.1\r\nHost: {host}:{port}\r\n"
               f"Content-Type: application/ipp\r\n"
               f"Content-Length: {len(body)}\r\n\r\n").encode() + body
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            s.sendall(req)
            buf = b""
            while b"\r\n\r\n" not in buf:
                ch = s.recv(8192)
                if not ch:
                    break
                buf += ch
                if len(buf) > MAX_PROBE_BYTES:
                    return Probe(self.scheme, False, error="ipp response headers too large")

            extra = b""
            try:
                s.settimeout(0.5)
                while True:
                    ch = s.recv(8192)
                    if not ch:
                        break
                    extra += ch
                    if len(buf) + len(extra) > MAX_PROBE_BYTES:
                        break
            except (socket.timeout, OSError):
                pass
            buf += extra
        except (socket.timeout, OSError) as e:
            return Probe(self.scheme, False, error=f"ipp probe failed: {e}")
        finally:
            s.close()

        import json
        attrs = {}
        marker = b"\nIPP-ATTRS "
        if marker in buf:
            try:
                attrs = json.loads(buf.split(marker, 1)[1].split(b"\n", 1)[0].decode())
            except (ValueError, UnicodeDecodeError):
                attrs = {}
        if b" 200 " not in buf.split(b"\r\n", 1)[0] and not attrs:
            return Probe(self.scheme, False, error="no IPP attributes in response")
        return Probe(self.scheme, True, data={"printer": attrs})

class StubDriver(TransportDriver):

    def __init__(self, scheme, kind, default_port=None, contributes=(), description=""):
        self.scheme = scheme
        self.kind = kind
        self.default_port = default_port
        self.contributes = tuple(contributes)
        self.description = description or f"{scheme} transport (documented stub)"

    def available(self) -> bool:
        return False

    def probe(self, host: str, port: int | None = None, timeout: float = 2.0, **kw) -> Probe:
        raise DriverUnavailable(
            f"transport '{self.scheme}' is a documented stub in this build (no live I/O); "
            f"kind={self.kind}, would contribute {list(self.contributes)}")

_STUB_SPECS = [

    ("nfs", KIND_NET, 2049, ("file-read", "file-write"), "NFS export mount (stub)"),
    ("ftp", KIND_NET, 21, ("file-read", "file-write", "drop-zone"), "FTP/SFTP transfer (stub)"),
    ("webdav", KIND_NET, 80, ("file-read", "file-write", "drop-zone"), "WebDAV (stub)"),
    ("mqtt", KIND_NET, 1883, ("telemetry", "control"), "MQTT pub/sub (stub)"),
    ("rtsp", KIND_NET, 554, ("video-in", "mic"), "RTSP/ONVIF camera stream (stub)"),
    ("tftp", KIND_NET, 69, ("firmware-xfer",), "TFTP firmware transfer (stub — firmware-gated)"),
    ("telnet", KIND_NET, 23, ("control",), "Telnet control (stub)"),
    ("serial", KIND_SERIAL, None, ("control", "console"), "serial console (stub)"),

    ("rf433", KIND_RF, None, ("sensor-in", "rf-tx"), "sub-GHz 433/868 via rtl_433/CC1101 (stub)"),
    ("ir", KIND_IR, None, ("ir-tx", "ir-rx"), "LIRC IR remote (stub)"),
    ("bt", KIND_BT, None, ("mic", "speaker", "beacon-in"), "BlueZ classic/BLE; A2DP/HFP (stub)"),
    ("zigbee", KIND_ZIGBEE, None, ("sensor-in", "control"), "zigbee2mqtt via USB stick (stub)"),
]

class TransportRegistry:

    def __init__(self):
        self._drivers: dict[str, TransportDriver] = {}

    def register(self, driver: TransportDriver):
        if not driver.scheme:
            raise ValueError("driver must declare a scheme")
        if not driver.readonly:
            raise ValueError(f"refusing to register non-read-only driver {driver.scheme!r} "
                             "(L8 transports are observe-only)")
        self._drivers[driver.scheme] = driver

    def get(self, scheme: str) -> TransportDriver | None:
        return self._drivers.get(scheme)

    def schemes(self) -> list[str]:
        return sorted(self._drivers)

    def real_schemes(self) -> list[str]:
        return sorted(s for s, d in self._drivers.items() if d.available())

    def stub_schemes(self) -> list[str]:
        return sorted(s for s, d in self._drivers.items() if not d.available())

    def describe(self) -> list[dict]:
        out = []
        for s in self.schemes():
            d = self._drivers[s]
            out.append({"scheme": s, "kind": d.kind, "port": d.default_port,
                        "available": d.available(), "real": d.available(),
                        "contributes": list(d.contributes), "description": d.description})
        return out

    def probe(self, scheme: str, host: str, port: int | None = None, **kw) -> Probe:
        d = self.get(scheme)
        if not d:
            return Probe(scheme, False, error=f"no driver for scheme {scheme!r}")
        if not d.available():
            return Probe(scheme, False, stub=True,
                         error=f"{scheme} is a documented stub (no live I/O in this build)")
        return d.probe(host, port, **kw)

def default_registry() -> TransportRegistry:

    reg = TransportRegistry()
    for d in (SNMPDriver(), SMBDriver(), IPPDriver()):
        reg.register(d)
    for scheme, kind, port, contributes, desc in _STUB_SPECS:
        reg.register(StubDriver(scheme, kind, port, contributes, desc))
    return reg

_COMPOSITION_RULES: dict[str, list[tuple[frozenset, str]]] = {

    "voice-endpoint": [
        (frozenset({"mic", "speaker"}),
         "mic + speaker on any transport -> full voice-agent endpoint"),
    ],

    "print-to-task": [
        (frozenset({"print"}), "print path -> archive/OCR/summarize/reply task"),
    ],

    "drop-zone-task": [
        (frozenset({"drop-zone"}),
         "writable share/FTP/WebDAV -> inbox/<task-type> drop-zone (identity-by-channel)"),
        (frozenset({"file-write"}),
         "writable file transport -> inbox/<task-type> drop-zone (identity-by-channel)"),
    ],

    "autonomous-maintenance": [
        (frozenset({"health-read"}),
         "reachable read interface -> CVE/health audit + maintenance DAG (architecture §11)"),
        (frozenset({"control"}),
         "reachable control interface -> autonomous upgrade/abandonware revival"),
    ],

    "input-cannon": [
        (frozenset({"sensor-in"}), "433/zigbee sensor -> emit-only input cannon"),
        (frozenset({"beacon-in"}), "BT beacon -> emit-only input cannon"),
        (frozenset({"ir-rx"}), "IR remote -> emit-only input cannon"),
    ],

    "vision-task": [
        (frozenset({"video-in"}), "RTSP/ONVIF stream -> snapshot/vision task"),
    ],
}

def compose_capabilities(transports: list[str], registry: TransportRegistry | None = None) -> dict:

    reg = registry or default_registry()

    contrib_src: dict[str, list[tuple[str, bool]]] = {}
    for scheme in transports:
        d = reg.get(scheme)
        if not d:
            continue
        for c in d.contributes:
            contrib_src.setdefault(c, []).append((scheme, d.available()))

    out: dict[str, list[dict]] = {}
    for cap, rules in _COMPOSITION_RULES.items():
        for needed, note in rules:
            if needed <= set(contrib_src):

                via, needs_stub = [], False
                for c in needed:
                    srcs = contrib_src[c]
                    via.extend(s for s, _live in srcs)
                    if not any(live for _s, live in srcs):
                        needs_stub = True
                out.setdefault(cap, []).append(
                    {"via": sorted(set(via)), "note": note, "needs_stub": needs_stub})
    return out
