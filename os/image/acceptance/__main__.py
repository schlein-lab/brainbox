

import argparse
import json
import os
import sys
import time

from . import Report, RED
from . import checks, discover
from .portal import Portal, Guest

def load_creds(path):
    with open(path) as f:
        raw = f.read()
    raw_s = raw.strip()
    if raw_s.startswith("{"):
        j = json.loads(raw_s)
        return j.get("user") or j.get("username"), j.get("password") or j.get("pin")
    lines = [l for l in raw.splitlines() if l.strip()]
    if len(lines) >= 2:
        return lines[0].strip(), lines[1].strip()
    if len(lines) == 1:
        return "tester", lines[0].strip()
    raise SystemExit("creds file %s is empty" % path)

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="acceptance",
        description="Prove the Brainbox PRODUCT works, not just that it boots.",
    )
    ap.add_argument("--target", required=True, help="https://host:port of the portal")
    ap.add_argument("--creds", required=True, help="path to credentials file (never argv)")
    ap.add_argument("--json", dest="json_out", default=None, help="write machine-readable results here")
    ap.add_argument("--ws-seconds", type=int, default=30, help="WS stability window (default 30)")
    ap.add_argument("--ssh-host", default=None, help="enable in-guest checks over SSH")
    ap.add_argument("--ssh-key", default=None,
                    help="privater Schluessel fuer die Pruefungen in der Box (der oeffentliche "
                         "Teil wurde im Assistenten hinterlegt)")
    ap.add_argument("--ssh-user", default="brainbox")
    ap.add_argument("--ssh-port", type=int, default=22)
    ap.add_argument("--ssh-pass-file", default=None, help="file holding the SSH password")
    ap.add_argument("--page", action="append", default=[],
                    help="extra UI page to walk (repeatable), e.g. /sessions_live.html")
    ap.add_argument("--skip-session", action="store_true",
                    help="do not create/stop a session (read-only run)")
    ap.add_argument("--nested", action="store_true",
                    help="target is itself a VM (image mode): /dev/kvm absence is expected")
    ap.add_argument("--post-probe", action="store_true",
                    help="actually POST to POST-only endpoints. Causes side effects -- "
                         "intended for the throwaway VM in --image mode, not a live box.")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    user, password = load_creds(args.creds)
    rep = Report()
    portal = Portal(args.target)
    guest = Guest(args.ssh_host, args.ssh_user, args.ssh_pass_file, args.ssh_port,
                  enabled=bool(args.ssh_host), key_file=args.ssh_key)

    say = (lambda *a: None) if args.quiet else (lambda *a: print(*a, file=sys.stderr))

    say("== target %s ==" % args.target)
    started = time.time()

    if guest.enabled and not guest.available():
        rep.amber("in-guest SSH probe", "unreachable -- in-guest checks skipped", "service health")
        guest.enabled = False
    marker = checks.portal_log_marker(guest)

    if checks.check_portal(rep, portal) is None:
        return finish(rep, args, started)

    checks.check_auth_enforced(rep, args.target)
    if not checks.check_login(rep, portal, user, password):
        return finish(rep, args, started)

    say("-- walking served UI --")
    pages = ["/"] + list(args.page)
    surf = discover.collect(portal, roots=pages)
    checks.check_real_ui(rep, portal, surf)
    checks.check_discovered_surface(rep, portal, surf, post_probe=args.post_probe)

    caps = checks.check_capability_honesty(rep, portal, guest)
    checks.check_cell_substrate(rep, guest, nested=args.nested)

    say("-- websocket routes (%ds window) --" % args.ws_seconds)
    checks.check_ws_routes(rep, portal, surf, args.ws_seconds)

    say("-- host shell retired --")
    checks.check_hostshell_retired(rep, portal, guest)

    if args.skip_session:
        rep.skip("session lifecycle", "--skip-session", "sessions")
    else:
        say("-- session lifecycle --")
        checks.check_session_lifecycle(rep, portal, caps, args.ws_seconds)

    say("-- feature surfaces --")
    checks.check_screen(rep, portal, caps, guest)
    checks.check_claude_signin(rep, portal)
    checks.check_queue(rep, portal)
    checks.check_admin(rep, portal)
    checks.check_vault(rep, portal)

    checks.check_still_up(rep, portal)
    checks.check_services(rep, guest)
    checks.check_portal_log(rep, guest, marker)

    return finish(rep, args, started)

def finish(rep, args, started):
    elapsed = time.time() - started
    print()
    print("==== BRAINBOX ACCEPTANCE: %s ====" % args.target)
    print(rep.render())
    print("  %.0fs elapsed" % elapsed)
    if args.json_out:
        data = rep.as_dict()
        data["target"] = args.target
        data["elapsed_s"] = round(elapsed, 1)
        data["ts"] = int(time.time())
        with open(args.json_out, "w") as f:
            json.dump(data, f, indent=2)
        print("  JSON: %s" % args.json_out)
    return 1 if rep.failed else 0

if __name__ == "__main__":
    sys.exit(main())
