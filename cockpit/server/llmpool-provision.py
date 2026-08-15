#!/usr/bin/env python3
"""llmpool-provision — manage the Claude Max account pool (llmpool.json) from the CLI.

The pool routes the governed LLM lane (/api/llm/chat + the /v1 shim) across N Max subs,
each isolated by its own HOME ($HOME/.claude.json holds that account's OAuth). This tool
scaffolds account HOMEs and edits the config; the login itself is one interactive
`HOME=<dir> claude login` per account (Anthropic OAuth — cannot be scripted).

Usage:
  llmpool-provision.py list
  llmpool-provision.py add <id> [<home>]     # register a slot (disabled until logged in)
  llmpool-provision.py login <id>            # run the interactive OAuth for that account's HOME
  llmpool-provision.py enable <id>           # activate a slot in the pool (after login)
  llmpool-provision.py disable <id>
  llmpool-provision.py set-concurrency <n>   # cap concurrent governed calls (portal restart to apply)

After enable/disable/add, activate without a portal restart:
  curl -sk -X POST https://127.0.0.1:8077/api/admin/llm/pool -H 'Cookie: …' -d '{"action":"reload"}'
(or just restart brainbox-portal). Account #1 ('primary') is the portal's own HOME and is
always present implicitly; you do NOT need to add it.
"""
import os
import sys
import json
import subprocess

CFG = os.path.expanduser("~/.config/brainbox-portal/llmpool.json")
DEFAULT_HOME_BASE = os.path.expanduser("~/.llmpool")

def load():
    try:
        with open(CFG) as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}

def save(cfg):
    os.makedirs(os.path.dirname(CFG), exist_ok=True)
    tmp = CFG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CFG)
    print("wrote", CFG)

def accounts(cfg):
    return cfg.setdefault("accounts", [])

def logged_in(home):
    try:
        with open(os.path.join(home, ".claude.json")) as f:
            return bool((json.load(f) or {}).get("oauthAccount"))
    except (OSError, ValueError):
        return False

def find(cfg, aid):
    for a in accounts(cfg):
        if a.get("id") == aid:
            return a
    return None

def cmd_list(_):
    cfg = load()
    accts = accounts(cfg)
    print("max_concurrent:", cfg.get("max_concurrent", 4))
    print("portal HOME (implicit account #1 'primary'):", os.path.expanduser("~"),
          "· logged_in=%s" % logged_in(os.path.expanduser("~")))
    if not accts:
        print("(no extra accounts configured — pool = single account = today's behavior)")
        return
    for a in accts:
        home = os.path.expanduser(a.get("home", ""))
        print("  - %-14s home=%s enabled=%s logged_in=%s" % (
            a.get("id"), home, a.get("enabled", True), logged_in(home)))

def cmd_add(argv):
    if not argv:
        sys.exit("usage: add <id> [<home>]")
    aid = argv[0]
    home = os.path.expanduser(argv[1]) if len(argv) > 1 else os.path.join(DEFAULT_HOME_BASE, aid)
    cfg = load()
    if find(cfg, aid):
        sys.exit("account id already exists: " + aid)
    os.makedirs(home, exist_ok=True)
    accounts(cfg).append({"id": aid, "home": home, "enabled": False, "weight": 1})
    save(cfg)
    print("\nadded (DISABLED). Next:")
    print("  1) log this account in:   %s login %s" % (os.path.basename(sys.argv[0]), aid))
    print("  2) enable it:             %s enable %s" % (os.path.basename(sys.argv[0]), aid))
    print("  3) reload the pool (POST /api/admin/llm/pool {\"action\":\"reload\"}) or restart the portal")

def cmd_login(argv):
    if not argv:
        sys.exit("usage: login <id>")
    aid = argv[0]
    cfg = load()
    a = find(cfg, aid)
    if not a:
        sys.exit("unknown account: " + aid)
    home = os.path.expanduser(a["home"])
    os.makedirs(home, exist_ok=True)
    claude = os.path.expanduser("~/.local/bin/claude")
    print("Launching `claude login` with HOME=%s (interactive OAuth — use this account's credentials)…" % home)
    env = dict(os.environ, HOME=home)
    rc = subprocess.call([claude, "login"], env=env)
    print("claude login exit=%d · logged_in=%s" % (rc, logged_in(home)))

def cmd_enable(argv, val=True):
    if not argv:
        sys.exit("usage: enable|disable <id>")
    aid = argv[0]
    cfg = load()
    a = find(cfg, aid)
    if not a:
        sys.exit("unknown account: " + aid)
    if val and not logged_in(os.path.expanduser(a["home"])):
        print("WARNING: %s is not logged in yet — enabling anyway; it will error+failover until you `login %s`." % (aid, aid))
    a["enabled"] = val
    save(cfg)

def cmd_concurrency(argv):
    if not argv:
        sys.exit("usage: set-concurrency <n>")
    cfg = load()
    cfg["max_concurrent"] = max(1, min(int(argv[0]), 16))
    save(cfg)
    print("set max_concurrent=%d (restart brainbox-portal to resize the semaphore)" % cfg["max_concurrent"])

def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, rest = sys.argv[1], sys.argv[2:]
    table = {
        "list": cmd_list, "add": cmd_add, "login": cmd_login,
        "enable": lambda a: cmd_enable(a, True), "disable": lambda a: cmd_enable(a, False),
        "set-concurrency": cmd_concurrency,
    }
    fn = table.get(cmd)
    if not fn:
        sys.exit(__doc__)
    fn(rest)

if __name__ == "__main__":
    main()
