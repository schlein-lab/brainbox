

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from forge import Forge
from surface import Node, SurfaceError, SurfaceRef, VerbSurface

@dataclass
class VerbResult:

    ok: bool
    earcon: str
    speech: str
    forged: Optional[dict] = None
    error_code: Optional[str] = None
    extra: dict = field(default_factory=dict)

class LowStakesExecutor:

    def __init__(self, surface: VerbSurface):
        self.s = surface
        self.forge = Forge(surface)

    def _sense(self, ref: SurfaceRef) -> Node:
        return self.s.sense_tree(ref.surface_id)

    @staticmethod
    def _err(code: str, speech: str) -> VerbResult:
        return VerbResult(ok=False, earcon="error", speech=speech, error_code=code)

    def click_label(self, ref: SurfaceRef, label: str) -> VerbResult:

        tree = self._sense(ref)
        node = tree.find(name=label)
        if node is None:

            return self._err("ERR_AMBIGUOUS",
                             f"ich finde kein bedienelement '{label}' in {ref.app}.")
        forged = self.forge.click_node(ref, node)

        role_de = _role_de(node.role)
        speech = f"{role_de} '{node.name or label}' in {_app_de(ref.app)} geklickt."
        return VerbResult(ok=True, earcon="done", speech=speech, forged=forged)

    def open_url(self, ref: SurfaceRef, url: str) -> VerbResult:

        try:
            new_ref = self.s.open(url=url)
        except SurfaceError as e:
            return self._err(e.code, f"seite konnte nicht geöffnet werden: {url}.")

        loaded = new_ref.title or url
        speech = f"{_speak_url(loaded)} geladen."
        return VerbResult(ok=True, earcon="done",
                          speech=speech,
                          forged={"action": "open", "url": url,
                                  "surface": new_ref.surface_id, "title": new_ref.title},
                          extra={"surface": new_ref})

    def new_tab_and_open(self, ref: SurfaceRef, url: str,
                         new_tab_label: str = "neuer tab") -> VerbResult:

        clicked = self.click_label(ref, new_tab_label)
        if not clicked.ok:
            return clicked
        opened = self.open_url(ref, url)
        if not opened.ok:
            return opened

        opened.speech = f"neuer tab, {opened.speech}"
        opened.forged = {"compound": [clicked.forged, opened.forged]}
        return opened

    def type_into(self, ref: SurfaceRef, label: str, text: str,
                  *, secret: bool = False) -> VerbResult:

        tree = self._sense(ref)
        node = tree.find(name=label)
        if node is None:
            node = tree.find(role="entry") if not secret else tree.find(role="password-text")
        if node is None:
            return self._err("ERR_AMBIGUOUS",
                             f"ich finde kein eingabefeld '{label}' in {ref.app}.")
        if not node.editable:
            return self._err("ERR_CAP_MISSING",
                             f"'{node.name or label}' ist kein editierbares feld.")
        self.forge.focus(ref, node)
        forged = self.forge.type_text(ref, text, secret=secret)

        after = self._sense(ref).find(name=node.name) or node
        if secret:
            speech = f"{forged['count']} zeichen in feld '{node.name or label}' eingetragen (versteckt)."
        else:
            speech = f"'{after.value}' in feld '{node.name or label}' eingetragen."
        return VerbResult(ok=True, earcon="done", speech=speech, forged=forged,
                          extra={"value_after": ("<versteckt>" if secret else after.value)})

    def press_key(self, ref: SurfaceRef, keysym: str) -> VerbResult:
        forged = self.forge.key(ref, keysym)
        return VerbResult(ok=True, earcon="done",
                          speech=f"taste {keysym} gedrückt.", forged=forged)

_ROLE_DE = {
    "push-button": "knopf",
    "document-web": "seite",
    "entry": "feld",
    "password-text": "passwortfeld",
    "menu-item": "menüpunkt",
    "link": "link",
    "check-box": "kontrollkästchen",
}

_APP_DE = {
    "firefox": "firefox",
    "gnome-calculator": "dem rechner",
    "libreoffice-calc": "excel",
}

def _role_de(role: str) -> str:
    return _ROLE_DE.get(role, role)

def _app_de(app: str) -> str:
    return _APP_DE.get(app, app)

def _speak_url(url: str) -> str:

    u = url
    for pre in ("https://", "http://"):
        if u.startswith(pre):
            u = u[len(pre):]
    u = u.rstrip("/")
    return u.replace(".", " punkt ")
