
import json

ENGINE_BROWSER = "browser"
ENGINE_WASM = "wasm"
ENGINE_CONTAINER = "container"
ENGINE_TERMINAL = "terminal"

class Capabilities:
    def __init__(self, engines=None, gpu=False, codecs=None, arch="", os="", thin=False, source="default"):
        self.engines = set(engines or ())
        self.gpu = bool(gpu)
        self.codecs = set(codecs or ())
        self.arch = arch or ""
        self.os = os or ""
        self.thin = bool(thin)
        self.source = source

    def has(self, engine):
        return engine in self.engines

    def as_dict(self):
        return {"engines": sorted(self.engines), "gpu": self.gpu, "codecs": sorted(self.codecs),
                "arch": self.arch, "os": self.os, "thin": self.thin, "source": self.source}

    def __repr__(self):
        return "Capabilities(%s)" % self.as_dict()

def browser_default(arch="", os="", gpu=True):
    return Capabilities(
        engines={ENGINE_BROWSER, ENGINE_WASM, ENGINE_TERMINAL},
        gpu=gpu, codecs={"h264", "vp8", "vp9", "opus", "aac", "jpeg", "png", "webp"},
        arch=arch, os=os, thin=False, source="ua",
    )

DEFAULT = Capabilities(engines={ENGINE_BROWSER}, source="default")

def _from_hint(hint):
    try:
        d = json.loads(hint) if isinstance(hint, str) else dict(hint)
    except (ValueError, TypeError):
        return None
    caps = browser_default(arch=d.get("arch", ""), os=d.get("os", ""), gpu=bool(d.get("gpu", True)))
    if "engines" in d:
        caps.engines = set(d["engines"])
    if "codecs" in d:
        caps.codecs = set(d["codecs"])
    caps.thin = bool(d.get("thin", False))
    caps.source = "hint"
    return caps

def from_request(headers=None, query_caps=None):

    headers = headers or {}
    hint = query_caps or headers.get("X-Fabric-Caps") or headers.get("x-fabric-caps")
    if hint:
        c = _from_hint(hint)
        if c:
            return c
    ua = (headers.get("User-Agent") or headers.get("user-agent") or "")
    arch = "x86_64" if ("x86_64" in ua or "Win64" in ua) else ("arm64" if "arm" in ua.lower() else "")
    osname = ("linux" if "Linux" in ua else "mac" if "Mac" in ua else "windows" if "Windows" in ua
              else "android" if "Android" in ua else "ios" if ("iPhone" in ua or "iPad" in ua) else "")
    if ua:
        return browser_default(arch=arch, os=osname)
    return DEFAULT
