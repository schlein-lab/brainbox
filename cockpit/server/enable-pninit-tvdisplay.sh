#!/bin/bash
set -e
CONF=/etc/pn-init.conf
BRAINARBEIT_ROOT="${BRAINARBEIT_ROOT:-$HOME/brainarbeit}"
LINE="pn-tvdisplay|user=1000|${BRAINARBEIT_ROOT}/cockpit/server/tvdisplay-run.sh"
COMMENT='# Nachtrag 8: Samsung-TV DLNA display driver (governed pn-misc, restart-on-death; wrapper sets PATH/HOME).'

set -a; source ~/.env 2>/dev/null; set +a
sudo -S -v <<<"$SUDO_PASSWORD" 2>/dev/null

sudo cp -a "$CONF" "${CONF}.bak-pretvdisplay"
echo "== backed up -> ${CONF}.bak-pretvdisplay =="
if grep -qF "$LINE" "$CONF"; then
  echo "== already present; no change =="
else
  printf '\n%s\n%s\n' "$COMMENT" "$LINE" | sudo tee -a "$CONF" >/dev/null
  echo "== appended pn-tvdisplay =="
fi
echo "== new tail =="; tail -4 "$CONF"

echo "== AUTHORITATIVE validation (replicates pn-init.c load_conf) =="
python3 - "$CONF" <<'PY'
import sys, os, pwd
conf = sys.argv[1]
def resolve_uid(s):
    if s.isdigit(): return int(s)
    try: return pwd.getpwnam(s).pw_uid
    except KeyError: return -1
found = None
for raw in open(conf, encoding="utf-8", errors="replace"):
    line = raw.lstrip(" \t")
    if not line.strip() or line.startswith("#"): continue        # blank/comment skip
    if "|" not in line: continue
    name, rest = line.split("|", 1)
    if "|" not in rest: continue                                  # <2 pipes -> load_conf skips
    flags, args = rest.split("|", 1)
    name = name.strip(); args = args.strip()
    if name != "pn-tvdisplay": continue
    # flags tokenize (bare + user=)
    sacred=oneshot=disabled=False; uid=0
    for tok in flags.replace(","," ").split():
        if tok=="sacred": sacred=True
        elif tok=="oneshot": oneshot=True
        elif tok=="disabled": disabled=True
        elif tok.startswith("user="): uid=resolve_uid(tok[5:])
    argv = args.split()
    argv0 = argv[0] if argv else ""
    found = dict(name=name, uid=uid, sacred=sacred, oneshot=oneshot, disabled=disabled,
                 argv0=argv0, abs_ok=argv0.startswith("/"), exists=os.path.exists(argv0),
                 execable=os.access(argv0, os.X_OK))
if not found:
    print("FAIL: pn-tvdisplay not parsed"); sys.exit(1)
print("parsed:", found)
ok = (found["uid"]==1000 and found["abs_ok"] and found["exists"] and found["execable"]
      and not found["disabled"] and not found["oneshot"] and not found["sacred"])
print("VALID" if ok else "INVALID")
sys.exit(0 if ok else 1)
PY
if [ $? -ne 0 ]; then
  echo "!! authoritative parse INVALID — reverting"; sudo cp -a "${CONF}.bak-pretvdisplay" "$CONF"; echo "reverted"; exit 3
fi
echo "== (info) pnctl view of the tail =="; pnctl list 2>&1 | tail -4 || true
echo "== DONE: reboot-persistent. Starts under pn-init supervision (uid 1000, pn-misc) on next boot;"
echo "         the running manual instance keeps the TV live until then. =="
