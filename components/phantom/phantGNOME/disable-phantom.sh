#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash disable-phantom.sh" >&2
    exit 1
fi

USER_NAME="${PHANTOM_AUTOLOGIN_USER:-$(getent passwd 1000 2>/dev/null | cut -d: -f1)}"
[ -n "$USER_NAME" ] || { echo "Set PHANTOM_AUTOLOGIN_USER=<your-login> and re-run." >&2; exit 1; }
FALLBACK_SESSION="${PHANTOM_FALLBACK_SESSION:-ubuntu}"
GDM_CONF="/etc/gdm3/custom.conf"
AS_USER="/var/lib/AccountsService/users/${USER_NAME}"

restore_or_note() {
    local f="$1"
    if [ -e "$f.phantom.bak" ]; then
        cp -a "$f.phantom.bak" "$f"
        rm -f "$f.phantom.bak"
        echo "restored $f from $f.phantom.bak (backup removed)"
        return 0
    fi
    echo "no backup for $f (was created fresh by enable-autologin.sh, or never touched)"
    return 1
}

if ! restore_or_note "$GDM_CONF"; then
    if [ -e "$GDM_CONF" ]; then
        python3 - "$GDM_CONF" <<'PY'
import sys,configparser
path=sys.argv[1]
cp=configparser.ConfigParser(); cp.optionxform=str; cp.read(path)
if cp.has_section('daemon'):
    cp.set('daemon','AutomaticLoginEnable','false')
    if cp.has_option('daemon','AutomaticLogin'):
        cp.remove_option('daemon','AutomaticLogin')
    with open(path,'w') as f: cp.write(f)
    print("disabled autologin in %s"%path)
PY
    fi
fi
echo "--- read-back $GDM_CONF [daemon] ---"
[ -e "$GDM_CONF" ] && awk '/^\[daemon\]/{p=1} p&&/Automatic/{print "  "$0} /^\[/{if($0!="[daemon]")p=0}' "$GDM_CONF" || echo "  (no custom.conf)"

if ! restore_or_note "$AS_USER"; then
    if [ -e "$AS_USER" ]; then
        python3 - "$AS_USER" "$FALLBACK_SESSION" <<'PY'
import sys,configparser
path,session=sys.argv[1],sys.argv[2]
cp=configparser.ConfigParser(); cp.optionxform=str; cp.read(path)
if not cp.has_section('User'): cp.add_section('User')
cp.set('User','Session',session)
cp.set('User','XSession',session)
with open(path,'w') as f: cp.write(f)
print("set default session back to %s in %s"%(session,path))
PY
    fi
fi
echo "--- read-back $AS_USER ---"
[ -e "$AS_USER" ] && sed 's/^/  /' "$AS_USER" || echo "  (no user file)"

echo
echo "DONE. This machine returned to a normal login: autologin off, default session '$FALLBACK_SESSION'."
echo "Optional: also set the boot stage back to onsite with"
echo "  sudo -u $USER_NAME bash $(dirname "$0")/set-default-stage.sh B"
echo "A reboot (or operator's 'systemctl restart gdm3') applies it."
