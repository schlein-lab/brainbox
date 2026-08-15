#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash enable-autologin.sh" >&2
    exit 1
fi

USER_NAME="${PHANTOM_AUTOLOGIN_USER:-$(getent passwd 1000 2>/dev/null | cut -d: -f1)}"
[ -n "$USER_NAME" ] || { echo "Set PHANTOM_AUTOLOGIN_USER=<your-login> and re-run." >&2; exit 1; }
SESSION="phantom"
GDM_CONF="/etc/gdm3/custom.conf"
AS_USER="/var/lib/AccountsService/users/${USER_NAME}"

backup_once() {
    local f="$1"
    if [ -e "$f" ] && [ ! -e "$f.phantom.bak" ]; then
        cp -a "$f" "$f.phantom.bak"
        echo "backed up $f -> $f.phantom.bak"
    elif [ -e "$f.phantom.bak" ]; then
        echo "backup already exists, kept: $f.phantom.bak"
    fi
}

backup_once "$GDM_CONF"
[ -e "$GDM_CONF" ] || { mkdir -p "$(dirname "$GDM_CONF")"; printf '[daemon]\n' > "$GDM_CONF"; }

python3 - "$GDM_CONF" "$USER_NAME" <<'PY'
import sys,configparser
path,user=sys.argv[1],sys.argv[2]
cp=configparser.ConfigParser()
cp.optionxform=str           # preserve key case
cp.read(path)
if not cp.has_section('daemon'):
    cp.add_section('daemon')
cp.set('daemon','AutomaticLoginEnable','true')
cp.set('daemon','AutomaticLogin',user)
with open(path,'w') as f:
    cp.write(f)
print("wrote [daemon] AutomaticLoginEnable=true AutomaticLogin=%s -> %s"%(user,path))
PY

echo "--- read-back $GDM_CONF [daemon] ---"
awk '/^\[daemon\]/{p=1} p&&/AutomaticLogin/{print "  "$0} /^\[/{if($0!="[daemon]")p=0}' "$GDM_CONF"

backup_once "$AS_USER"
mkdir -p "$(dirname "$AS_USER")"
[ -e "$AS_USER" ] || printf '[User]\n' > "$AS_USER"

python3 - "$AS_USER" "$SESSION" <<'PY'
import sys,configparser
path,session=sys.argv[1],sys.argv[2]
cp=configparser.ConfigParser()
cp.optionxform=str
cp.read(path)
if not cp.has_section('User'):
    cp.add_section('User')
cp.set('User','Session',session)
cp.set('User','XSession',session)
cp.set('User','SystemAccount','false')
with open(path,'w') as f:
    cp.write(f)
print("wrote [User] Session=%s XSession=%s -> %s"%(session,session,path))
PY

echo "--- read-back $AS_USER ---"
sed 's/^/  /' "$AS_USER"

echo
echo "DONE. Autologin enabled for '$USER_NAME' into the '$SESSION' session."
echo "Pair with: bash set-default-stage.sh A   (boot into full-auto Mode A)."
echo "Revert everything with: sudo bash disable-phantom.sh"
echo "NOTE: a reboot (or 'systemctl restart gdm3', operator's call) applies it."
