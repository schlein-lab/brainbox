
import os
from urllib.parse import quote
from . import dataplane, placement as P, registry

def _proj(kind, **kw):
    d = {"kind": kind}
    d.update(kw)
    return d

def browser_store(principal):

    return dataplane.open_store(principal, "browser")

def container_manifest(app, store, arch, host, user=None):

    user = user or os.environ.get("BBX_NAS_USER") or os.environ.get("USER", "brainarbeit")
    host = host or "BOX"
    arch = arch or "amd64"
    remote = store.files_dir
    local = "~/.brainarbeit/mnt/%s" % app.id
    mount = {"protocol": "sshfs", "host": host, "user": user, "remote": remote, "local": local}
    run = ["podman", "run", "--rm", "-it", "--arch", arch,
           "-v", "%s:/root" % local, "-e", "HOME=/root", app.container]
    recipe = (
        "#!/bin/sh\n# fabric tier-C launcher for %s — runs on the CLIENT.\nset -e\n"
        "mkdir -p %s\n"
        "# 1) mount the NAS data-plane store (files live on the NAS):\n"
        "sshfs %s@%s:%s %s\n"
        "# 2) run the app on THIS machine's kernel (RAM/CPU are the client's):\n"
        "%s\n"
    ) % (app.id, local, user, host, remote, local, " ".join(run))
    return {"image": app.container, "arch": arch, "mount": mount, "run": run, "recipe": recipe}

def project(pl, principal, params=None, caps=None):
    params = params or {}
    app = pl.app
    store = dataplane.open_store(principal, app.id) if app else None

    if pl.tier == P.A_ENGINE:
        if app.engine == "browser":
            start = params.get("url") or "https://start.duckduckgo.com/"
            return _proj("browser", embed="/go?url=" + quote(start, safe=""),
                         store=store.rel, live=True)
        if app.engine == "terminal":

            return _proj("terminal", embed="/term", ws="/api/term", store=store.rel,
                         live=True)
        return _proj("engine", engine=app.engine, status="scaffold", live=False)

    if pl.tier == P.B_PORTABLE:
        if pl.driver == "web":
            return _proj("web", embed=app.web, store=store.rel, live=bool(app.web))
        if pl.driver == "wasm":
            return _proj("wasm", module=app.wasm, store=store.rel,
                         plan="client fetches the wasm module, runs it in its wasm engine; WASI "
                              "file/socket syscalls are proxied to the NAS data plane store",
                         status="scaffold", live=False)

    if pl.tier == P.C_CONTAINER:
        arch = (caps.arch if caps else "") or params.get("arch") or "amd64"
        man = container_manifest(app, store, arch, params.get("host"))
        return _proj("container", live=True, status="manifest",
                     manifest=man,
                     note="OCI on the CLIENT kernel; RAM/CPU are the client's, $HOME is the NAS "
                          "mount. The client agent executes manifest.recipe; the NAS side is real.")

    if pl.tier == P.D_PIXEL:
        return _proj("pixel", exec=app.pixel,
                     note="run on the NAS phantom seat, stream MJPEG + client reflexes",
                     status="fallback", live=False)

    return _proj("none", status="unsupported", live=False)

def launch(app_id, principal, caps, params=None):

    app = registry.get(app_id)
    pl = P.decide(app, caps)
    return {"app": app_id, "placement": pl.as_dict(),
            "projection": project(pl, principal, params, caps)}
