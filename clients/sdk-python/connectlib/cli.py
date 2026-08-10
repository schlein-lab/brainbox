"""connectlib.cli — the pn-connect CLI/tray, packaged. The `pn-connect` script is a thin shim over
this `main()`. See the pn-connect docstring for usage.

The account-bound connect-once client: pair ONCE (code + MANDATORY 2FA -> durable token), then
connect with one button, forever. Voice/text/file/path intake; approve/reject/revise; one reality.
"""
import argparse, json, os, sys, time, threading

HERE = os.path.dirname(os.path.realpath(__file__))

DEFAULT_ENGINE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "engine"))

from connectlib.client import ConnectClient
from connectlib.keystore import Keystore
from connectlib.transports import LanTransport, RelayTransport
from connectlib import contract

def _add_engine_to_path(engine):
    engine = engine or os.environ.get("PN_ENGINE") or DEFAULT_ENGINE
    if engine and os.path.isdir(engine) and engine not in sys.path:
        sys.path.insert(0, engine)

def build_client(args) -> ConnectClient:
    ks = Keystore(path=args.keystore) if args.keystore else Keystore()
    if args.lan:
        sock = args.sock or os.path.join(
            os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "pnd.sock")
        tr = LanTransport(sock)
        box = args.box or "lan"
        principal = args.principal
        return ConnectClient(box, keystore=ks, transport=tr, principal=principal)

    _add_engine_to_path(args.engine)
    al = ks.alliance(args.box) or {}
    relay_url = args.relay or al.get("relay_url")
    id_pub = args.id_pub or al.get("appliance_id_pubkey")
    x_pub = args.x_pub or al.get("appliance_x_pubkey")
    if not (relay_url and id_pub and x_pub):
        sys.exit("relay path needs --relay/--id-pub/--x-pub (or a prior pairing in the keystore)")
    if args.relay or args.id_pub or args.x_pub:
        ks.save_box_keys(args.box, relay_url=relay_url,
                         appliance_id_pubkey=id_pub, appliance_x_pubkey=x_pub)
    device_keys = ks.device_identity(args.box)
    tr = RelayTransport(relay_url=relay_url, appliance_id_pub_hex=id_pub,
                        appliance_x_pub_hex=x_pub, device_keys=device_keys)
    return ConnectClient(args.box, keystore=ks, transport=tr, principal=al.get("principal"))

def cmd_pair(c, args):
    if not args.code or not args.totp:
        sys.exit("pair requires --code and --totp (2FA is MANDATORY)")
    resp = c.pair(args.code, totp_code=args.totp, label=args.label)
    if resp.get("t") == "pair_ok":
        print(f"PAIRED. principal={resp['principal']} did={resp.get('did')}")
        print("This device is now connected-until-revoked. No more logins.")
    else:
        print("pairing failed:", json.dumps(resp))
        if resp.get("need_2fa"):
            print("  -> a second-factor (2FA) code is required.")
        sys.exit(1)

def cmd_connect(c, args):
    print(json.dumps(c.connect(), indent=2))

def cmd_watch(c, args):
    st = c.connect()
    if not st.get("ok"):
        sys.exit(json.dumps(st))
    print(f"watching user/{c.principal} (Ctrl-C to stop) ...")

    def on_cvm(cvm):
        if contract.is_awaiting(cvm):
            print(f"\n[APPROVAL #{cvm['id']}] {contract.approval_summary(cvm)}")
            d = contract.digest(cvm)
            if d:
                print("  digest:", d.splitlines()[0] if d else "")
            print(f"  approve: pn-connect ... approve {cvm['id']}  | reject {cvm['id']} "
                  f"| revise {cvm['id']} \"...\"")
        else:
            print(f"[job #{cvm['id']}] state={cvm.get('state')}"
                  + (f"  {cvm.get('notify')}" if cvm.get("notify") else ""))
    c.watch(on_cvm)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        c.disconnect()

def cmd_type(c, args):
    c.connect()
    print(json.dumps(c.type(" ".join(args.text)), indent=2))

def cmd_say(c, args):
    c.connect()

    print(json.dumps(c.say(" ".join(args.text)), indent=2))

def cmd_attach(c, args):
    c.connect()
    print(json.dumps(c.attach(args.text or "", files=args.file, paths=args.path), indent=2))

def _resolve_cvm(c, job_id):

    resp = c.transport.call(contract.cvm_request(job_id))
    return (resp.get("cvm") or {}) if resp.get("ok") else {}

def cmd_approve(c, args):
    c.connect()
    cvm = _resolve_cvm(c, args.job_id)
    print(json.dumps(c.approve(cvm), indent=2))

def cmd_reject(c, args):
    c.connect()
    cvm = _resolve_cvm(c, args.job_id)
    print(json.dumps(c.reject(cvm), indent=2))

def cmd_revise(c, args):
    c.connect()
    print(json.dumps(c.revise({"id": args.job_id}, " ".join(args.feedback)), indent=2))

def cmd_devices(c, args):
    for al in c.ks.list_alliances():
        print(f"{al['box_label']:<12} principal={al['principal']} paired={al['paired']} "
              f"did={al['did']} relay={al['relay_url']}")

def cmd_tray(c, args):

    cmd_watch(c, args)

def main():
    ap = argparse.ArgumentParser(prog="pn-connect", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--box", default="home", help="alliance label in the keystore")
    ap.add_argument("--lan", action="store_true", help="on-VM/LAN transport (peercred identity)")
    ap.add_argument("--sock", help="pnd unix socket (LAN mode)")
    ap.add_argument("--relay", help="relay url (off-LAN; mock://... for local/test)")
    ap.add_argument("--id-pub", help="appliance Ed25519 identity pubkey (hex; pinned)")
    ap.add_argument("--x-pub", help="appliance X25519 static pubkey (hex; pinned)")
    ap.add_argument("--principal", help="override principal (LAN mode)")
    ap.add_argument("--keystore", help="keystore dir (default OS user data dir)")
    ap.add_argument("--engine", help="path to the engine clone (relaylib); default sibling submodule")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pair"); p.add_argument("--code"); p.add_argument("--totp")
    p.add_argument("--label")
    sub.add_parser("connect")
    sub.add_parser("watch")
    sub.add_parser("tray")
    p = sub.add_parser("type"); p.add_argument("text", nargs="+")
    p = sub.add_parser("say"); p.add_argument("text", nargs="+")
    p = sub.add_parser("attach"); p.add_argument("--text"); p.add_argument("--file", action="append")
    p.add_argument("--path", action="append")
    p = sub.add_parser("approve"); p.add_argument("job_id", type=int)
    p = sub.add_parser("reject"); p.add_argument("job_id", type=int)
    p = sub.add_parser("revise"); p.add_argument("job_id", type=int); p.add_argument("feedback", nargs="+")
    sub.add_parser("devices")

    args = ap.parse_args()
    if args.cmd == "devices":
        return cmd_devices(ConnectClient(args.box, keystore=Keystore()), args)
    c = build_client(args)
    {"pair": cmd_pair, "connect": cmd_connect, "watch": cmd_watch, "tray": cmd_tray,
     "type": cmd_type, "say": cmd_say, "attach": cmd_attach, "approve": cmd_approve,
     "reject": cmd_reject, "revise": cmd_revise}[args.cmd](c, args)

