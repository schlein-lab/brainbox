

from __future__ import annotations

import abc
import itertools
import time
from dataclasses import dataclass, field
from typing import Optional

@dataclass(frozen=True)
class SurfaceRef:

    surface_id: str
    app: str
    toplevel: str
    title: str = ""

@dataclass
class Node:

    node_id: str
    role: str
    name: str = ""
    value: str = ""
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    focusable: bool = False
    editable: bool = False
    secret: bool = False
    children: list["Node"] = field(default_factory=list)

    def center(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    def walk(self):

        yield self
        for c in self.children:
            yield from c.walk()

    def find(self, *, role: Optional[str] = None, name: Optional[str] = None) -> Optional["Node"]:

        for n in self.walk():
            if role is not None and n.role != role:
                continue
            if name is not None and name.lower() not in n.name.lower():
                continue
            return n
        return None

class SurfaceError(Exception):

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message

class VerbSurface(abc.ABC):

    @abc.abstractmethod
    def list_surfaces(self) -> list[SurfaceRef]:
        pass

    @abc.abstractmethod
    def open(self, *, app: Optional[str] = None, url: Optional[str] = None) -> SurfaceRef:
        pass

    @abc.abstractmethod
    def resolve(self, surface_id: str) -> SurfaceRef:
        pass

    @abc.abstractmethod
    def sense_tree(self, surface_id: str) -> Node:
        pass

    @abc.abstractmethod
    def sense_shot(self, surface_id: str) -> bytes:
        pass

    @abc.abstractmethod
    def seat_objects(self, surface_id: str) -> "SeatObjects":
        pass

    @abc.abstractmethod
    def _raw_pointer(self, toplevel: str, seatobj: str, kind: str, x: int, y: int,
                     button: Optional[int], serial: int, t_ms: int) -> None:
        pass

    @abc.abstractmethod
    def _raw_frame(self, toplevel: str, seatobj: str) -> None:
        pass

    @abc.abstractmethod
    def _raw_key(self, toplevel: str, seatobj: str, keysym: str, serial: int, t_ms: int) -> None:
        pass

    @abc.abstractmethod
    def needs_shm(self, surface_id: str) -> bool:
        pass

@dataclass
class SeatObjects:

    pointers: list[str]
    keyboards: list[str]

    def first_pointer(self) -> str:
        if not self.pointers:
            raise SurfaceError("ERR_CAP_MISSING", "surface has no bound wl_pointer")
        return self.pointers[0]

    def first_keyboard(self) -> str:
        if not self.keyboards:
            raise SurfaceError("ERR_CAP_MISSING", "surface has no bound wl_keyboard")
        return self.keyboards[0]

class _FakeApp:

    def __init__(self, surface: SurfaceRef, tree: Node, *,
                 double_seat: bool = False, shm: bool = False):
        self.surface = surface
        self.tree = tree
        self.shm = shm

        if double_seat:
            self.seat = SeatObjects(pointers=["ptr@7", "ptr@24"],
                                    keyboards=["kbd@15", "kbd@25"])
        else:
            self.seat = SeatObjects(pointers=["ptr@7"], keyboards=["kbd@15"])

        self.delivered: list[tuple[str, str, str]] = []

class FakeSurface(VerbSurface):

    def __init__(self):
        self._apps: dict[str, _FakeApp] = {}
        self._serial = itertools.count(1)

    def _install(self, app: _FakeApp) -> SurfaceRef:
        self._apps[app.surface.surface_id] = app
        return app.surface

    def _app(self, surface_id: str) -> _FakeApp:
        try:
            return self._apps[surface_id]
        except KeyError:
            raise SurfaceError("ERR_AMBIGUOUS", f"no surface {surface_id!r}")

    def list_surfaces(self) -> list[SurfaceRef]:
        return [a.surface for a in self._apps.values()]

    def open(self, *, app: Optional[str] = None, url: Optional[str] = None) -> SurfaceRef:

        if url is not None:
            browser = next((a for a in self._apps.values() if a.surface.app == "firefox"), None)
            if browser is None:
                raise SurfaceError("ERR_CAP_MISSING", "no browser surface to open a url in")
            tab = Node(node_id=f"tab-{next(self._serial)}", role="document-web",
                       name=url, value=url, x=0, y=40, w=960, h=560)
            browser.tree.children.append(tab)
            browser.surface = SurfaceRef(browser.surface.surface_id, "firefox",
                                         browser.surface.toplevel, title=url)
            self._apps[browser.surface.surface_id] = browser
            return browser.surface
        raise SurfaceError("ERR_CAP_MISSING", f"fake backend cannot launch app {app!r}")

    def resolve(self, surface_id: str) -> SurfaceRef:
        return self._app(surface_id).surface

    def sense_tree(self, surface_id: str) -> Node:
        return self._app(surface_id).tree

    def sense_shot(self, surface_id: str) -> bytes:

        self._app(surface_id)
        return b"FAKESHOT:" + surface_id.encode()

    def seat_objects(self, surface_id: str) -> SeatObjects:
        return self._app(surface_id).seat

    def needs_shm(self, surface_id: str) -> bool:
        return self._app(surface_id).shm

    def _find_app_by_toplevel(self, toplevel: str) -> _FakeApp:
        for a in self._apps.values():
            if a.surface.toplevel == toplevel:
                return a
        raise SurfaceError("ERR_AMBIGUOUS", f"no surface with toplevel {toplevel!r}")

    def _raw_pointer(self, toplevel, seatobj, kind, x, y, button, serial, t_ms):
        app = self._find_app_by_toplevel(toplevel)
        detail = f"{kind}({x},{y}" + (f",btn={button}" if button is not None else "") + ")"
        app.delivered.append(("pointer", seatobj, detail))

        if kind == "button_release":
            hit = self._hit_test(app.tree, x, y)
            if hit is not None:
                app._focus = hit.node_id

    def _raw_frame(self, toplevel, seatobj):
        app = self._find_app_by_toplevel(toplevel)
        app.delivered.append(("frame", seatobj, ""))

    def _raw_key(self, toplevel, seatobj, keysym, serial, t_ms):
        app = self._find_app_by_toplevel(toplevel)
        app.delivered.append(("key", seatobj, keysym))

        focus_id = getattr(app, "_focus", None)
        if focus_id is not None:
            for n in app.tree.walk():
                if n.node_id == focus_id and n.editable:
                    if keysym == "BackSpace":
                        n.value = n.value[:-1]
                    elif len(keysym) == 1:
                        n.value += keysym
                    break

    @staticmethod
    def _hit_test(root: Node, x: int, y: int) -> Optional[Node]:

        best: Optional[Node] = None
        for n in root.walk():
            if n.w and n.h and n.x <= x < n.x + n.w and n.y <= y < n.y + n.h:
                best = n
        return best

class PhantomSurface(VerbSurface):

    def __init__(self, *, endpoint: str = "http://127.0.0.1:8092"):
        self.endpoint = endpoint

    def _todo(self, what: str):
        raise NotImplementedError(
            f"PhantomSurface.{what} is a documented Phase-3 stub — wire to phantom "
            f"({self.endpoint}) in the next increment; see class docstring for the mapping.")

    def list_surfaces(self):            self._todo("list_surfaces")
    def open(self, **kw):               self._todo("open")
    def resolve(self, surface_id):      self._todo("resolve")
    def sense_tree(self, surface_id):   self._todo("sense_tree")
    def sense_shot(self, surface_id):   self._todo("sense_shot")
    def seat_objects(self, surface_id): self._todo("seat_objects")
    def needs_shm(self, surface_id):    self._todo("needs_shm")

    def _raw_pointer(self, *a):         self._todo("_raw_pointer")
    def _raw_frame(self, *a):           self._todo("_raw_frame")
    def _raw_key(self, *a):             self._todo("_raw_key")

def now_ms() -> int:
    return int(time.time() * 1000)
