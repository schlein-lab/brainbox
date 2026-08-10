@echo off
REM --- edit these three, then double-click ---
REM HA sites: set BOX to the VIP URL (brainbox.local mDNS does not follow VRRP failover)
if not defined BOX set BOX=wss://brainbox.local:8077
set AGENT=laptop
set KEY=PASTE-YOUR-DEVINPUT-SCOPED-KEY
python "%~dp0win_input_agent.py" --box %BOX% --agent %AGENT% --key %KEY% --insecure
pause
