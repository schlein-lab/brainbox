
from . import capability as cap

A_ENGINE = "A_ENGINE"
B_PORTABLE = "B_PORTABLE"
C_CONTAINER = "C_CONTAINER"
D_PIXEL = "D_PIXEL"

_ARCH_OK = {
    "x86_64": {"x86_64", "amd64"},
    "amd64": {"x86_64", "amd64"},
    "arm64": {"arm64", "aarch64"},
    "aarch64": {"arm64", "aarch64"},
}

class Placement:
    def __init__(self, tier, driver, reason, app=None):
        self.tier = tier
        self.driver = driver
        self.reason = reason
        self.app = app

    def as_dict(self):
        return {"tier": self.tier, "driver": self.driver, "reason": self.reason,
                "app": self.app.id if self.app else None,
                "latency_free": self.tier in (A_ENGINE, B_PORTABLE, C_CONTAINER)}

    def __repr__(self):
        return "Placement(%s via %s: %s)" % (self.tier, self.driver, self.reason)

def _arch_matches(host_arch, image_arch):
    if not image_arch:
        return True
    return image_arch in _ARCH_OK.get(host_arch, set())

def decide(app, caps, image_arch=None):

    if app is None:
        return Placement(D_PIXEL, "none", "unknown app", app)

    if app.engine and caps.has(app.engine):
        return Placement(A_ENGINE, "engine:" + app.engine,
                         "client has the '%s' engine → data-only proxy, native render" % app.engine,
                         app)

    if app.web and caps.has(cap.ENGINE_BROWSER):
        return Placement(B_PORTABLE, "web",
                         "web app shipped to the client browser; state → NAS", app)
    if app.wasm and caps.has(cap.ENGINE_WASM):
        return Placement(B_PORTABLE, "wasm",
                         "wasm module run in the client wasm engine; syscalls → NAS", app)

    if app.container and caps.has(cap.ENGINE_CONTAINER) and _arch_matches(caps.arch, image_arch):
        return Placement(C_CONTAINER, "container",
                         "OCI image on the client kernel (arch %s); $HOME → NAS mount"
                         % (caps.arch or "?"), app)

    if app.pixel:
        why = []
        if app.engine and not caps.has(app.engine):
            why.append("client lacks '%s' engine" % app.engine)
        if app.container and not caps.has(cap.ENGINE_CONTAINER):
            why.append("no client container runtime")
        elif app.container and not _arch_matches(caps.arch, image_arch):
            why.append("client arch %s can't run the image" % (caps.arch or "?"))
        return Placement(D_PIXEL, "pixel",
                         "run on NAS + pixel-stream (" + ("; ".join(why) or "no client tier fits") + ")",
                         app)

    return Placement(D_PIXEL, "none",
                     "no runnable tier and no pixel fallback declared", app)
