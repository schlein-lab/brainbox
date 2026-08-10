

GREEN = "GREEN"
RED = "RED"
AMBER = "AMBER"
SKIP = "SKIP"

FAILING = (RED,)

class Check:
    def __init__(self, name, status, detail="", group="", evidence=None):
        self.name = name
        self.status = status
        self.detail = detail
        self.group = group
        self.evidence = evidence or {}

    def as_dict(self):
        return {
            "name": self.name,
            "group": self.group,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
        }

class Report:
    def __init__(self):
        self.checks = []

    def add(self, name, status, detail="", group="", evidence=None):
        c = Check(name, status, detail, group, evidence)
        self.checks.append(c)
        return c

    def green(self, name, detail="", group="", evidence=None):
        return self.add(name, GREEN, detail, group, evidence)

    def red(self, name, detail="", group="", evidence=None):
        return self.add(name, RED, detail, group, evidence)

    def amber(self, name, detail="", group="", evidence=None):
        return self.add(name, AMBER, detail, group, evidence)

    def skip(self, name, detail="", group="", evidence=None):
        return self.add(name, SKIP, detail, group, evidence)

    @property
    def failed(self):
        return [c for c in self.checks if c.status in FAILING]

    def counts(self):
        out = {GREEN: 0, RED: 0, AMBER: 0, SKIP: 0}
        for c in self.checks:
            out[c.status] = out.get(c.status, 0) + 1
        return out

    def render(self):

        mark = {GREEN: "PASS", RED: "FAIL", AMBER: "WARN", SKIP: "SKIP"}
        wname = max([len(c.name) for c in self.checks] + [20])
        wname = min(wname, 46)
        lines = []
        group = None
        for c in self.checks:
            if c.group != group:
                group = c.group
                lines.append("")
                lines.append("  -- %s --" % (group or "general"))
            name = c.name if len(c.name) <= wname else c.name[: wname - 1] + "…"
            detail = c.detail.replace("\n", " ")
            if len(detail) > 96:
                detail = detail[:95] + "…"
            lines.append("  %-4s  %-*s  %s" % (mark.get(c.status, "?"), wname, name, detail))
        n = self.counts()
        lines.append("")
        lines.append("  " + "-" * (wname + 12))
        lines.append(
            "  %d pass, %d FAIL, %d warn, %d skip"
            % (n[GREEN], n[RED], n[AMBER], n[SKIP])
        )
        lines.append("  RESULT: %s" % ("RED" if n[RED] else "GREEN"))
        return "\n".join(lines)

    def as_dict(self):
        return {"counts": self.counts(), "checks": [c.as_dict() for c in self.checks]}
