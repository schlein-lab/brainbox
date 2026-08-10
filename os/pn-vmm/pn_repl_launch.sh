#!/bin/sh
if [ -z "$PN_GATE_ACTIVE" ] && [ -x /opt/pn/pn-gate ]; then
  KMAJ=$(busybox uname -r 2>/dev/null | busybox cut -d. -f1)
  if [ "${KMAJ:-0}" -ge 5 ] 2>/dev/null; then
    export PN_GATE_ACTIVE=1
    exec /opt/pn/pn-gate -- /bin/sh "$0" "$@"
  fi
fi
cd /root
F="${PN_CLAUDE_FLAGS:-}"
busybox mount -t devpts -o mode=0620,ptmxmode=0666 devpts /dev/pts 2>/dev/null
[ -e /dev/pts/ptmx ] && busybox ln -sf /dev/pts/ptmx /dev/ptmx 2>/dev/null
if [ -f /opt/pn/pn_repl_banner.py ]; then
  /bin/python3 /opt/pn/pn_repl_banner.py >/tmp/pnbanner.out 2>&1 &
fi
CL="IS_SANDBOX=1 HOME=/root /bin/claude --continue --dangerously-skip-permissions $F"
CF="IS_SANDBOX=1 HOME=/root /bin/claude --dangerously-skip-permissions $F"
if command -v tmux >/dev/null 2>&1; then
  tmux -u new-session -d -s repl "$CL 2>/tmp/claude.err || $CF 2>>/tmp/claude.err" 2>/tmp/pnlaunch.log
  exec tmux -u attach -t repl
else
  exec /bin/sh -c "$CL 2>/tmp/claude.err || $CF 2>>/tmp/claude.err"
fi
