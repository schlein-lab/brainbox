
from __future__ import annotations
import ipaddress, socket, subprocess

def own_cidrs():

    cidrs = []
    try:
        out = subprocess.run(["ip", "-o", "-4", "addr", "show", "scope", "global"],
                             capture_output=True, text=True, timeout=3).stdout
        for line in out.splitlines():
            parts = line.split()
            for i, tok in enumerate(parts):
                if tok == "inet" and i + 1 < len(parts):
                    try:
                        c = str(ipaddress.ip_network(parts[i + 1], strict=False))
                    except ValueError:
                        continue
                    if c not in cidrs:
                        cidrs.append(c)
    except Exception:
        pass
    cidrs = [c for c in cidrs if not (c.startswith("127.") or c.startswith("169.254."))]
    if cidrs:
        return cidrs

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        return [str(ipaddress.ip_network(ip + "/24", strict=False))]
    except Exception:
        return []

if __name__ == "__main__":
    print(own_cidrs())
