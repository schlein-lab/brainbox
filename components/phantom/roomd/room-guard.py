#!/usr/bin/env python3

import sys, json, re, os

HOME = os.path.expanduser("~")

HOME_FRAG = re.escape(HOME.lower().lstrip("/"))

SECRETS = [

    r'[\w.-]*credentials[\w.-]*\.txt', r'/\.env(\b|$|[^a-zA-Z])', r'/\.ssh/', r'\.credentials\.json',
    r'\bid_rsa\b', r'\bid_ed25519\b', r'\.pem(\b|$)', r'(?<![a-zA-Z])\.key(\b|$)',
]
SECRET_SHAPED = [r'ghp_[A-Za-z0-9]', r'\bsk-[A-Za-z0-9]', r'\bAKIA[0-9A-Z]', r'-----BEGIN ',
                 r'\.env\b', r'/\.ssh/', r'credentials\.json']

def hits(s, pats):
    return any(re.search(p, s) for p in pats)

def deny(reason):
    sys.stderr.write("room-guard: " + reason)
    sys.exit(2)

def main():
    try:
        data = json.load(sys.stdin)
    except Exception as e:

        print("room-guard: Eingabe nicht lesbar (%s) -> gesperrt" % e.__class__.__name__,
              file=sys.stderr)
        sys.exit(2)
    tool = data.get("tool_name", "")
    inp = data.get("tool_input", {}) or {}

    if tool == "Bash":
        cmd = inp.get("command", "") or ""
        low = cmd.lower()
        if hits(cmd, SECRETS):
            deny("Zugriff auf Geheimnisse (Passwort-Datei/.env/.ssh/Token) ist im Room gesperrt.")

        rm_guard = r'\brm\s+-[a-z]*r[a-z]*f?\s+(/(?!tmp/|' + HOME_FRAG + r'/room-workspace)|~|\$home|\*)'
        if re.search(rm_guard, low) \
                or re.search(r'\bmkfs\b', low) or re.search(r'\bdd\b.*of=/dev/', low) \
                or re.search(r'>\s*/dev/sd', low):
            deny("Destruktiver Befehl (rm -rf am System / mkfs / dd auf Gerät) ist gesperrt.")
        if re.search(r'\bsystemctl\s+(stop|disable|mask|kill)\b.*\b(sshd?|phantom|roomd|zyrkel|systemd)', low):
            deny("Stoppen kritischer Dienste (sshd/phantom/roomd/zyrkel) ist gesperrt.")
        if re.search(r'(^|\s|;|&|\|)sudo\s', " " + low):
            deny("sudo ist im Room gesperrt.")
        if re.search(r'\b(curl|wget|nc|ncat|scp|rsync|ftp)\b', low) and hits(cmd, SECRET_SHAPED):
            deny("Versand potenzieller Geheimnisse nach außen ist gesperrt.")
    elif tool in ("Read", "Edit", "Write", "NotebookEdit"):
        if hits(inp.get("file_path", "") or "", SECRETS):
            deny("Datei mit Geheimnissen ist gesperrt.")
    elif tool in ("WebFetch", "WebSearch"):

        url = (inp.get("url", "") or "") + (inp.get("query", "") or "")
        if hits(url, SECRET_SHAPED):
            deny("URL/Query enthält geheimnis-ähnliche Daten — gesperrt.")
    sys.exit(0)

if __name__ == "__main__":
    main()
