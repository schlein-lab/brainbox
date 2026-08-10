

from __future__ import annotations

import itertools
from typing import Optional

from surface import Node, SeatObjects, SurfaceError, SurfaceRef, VerbSurface, now_ms

BTN_LEFT = 0x110

class Forge:

    def __init__(self, surface: VerbSurface):
        self.s = surface
        self._serial = itertools.count(1)

    def assert_renderable(self, ref: SurfaceRef) -> None:

        if self.s.needs_shm(ref.surface_id):

            pass

    def click(self, ref: SurfaceRef, x: int, y: int, button: int = BTN_LEFT) -> dict:

        self.assert_renderable(ref)
        seat: SeatObjects = self.s.seat_objects(ref.surface_id)
        if not seat.pointers:
            raise SurfaceError("ERR_CAP_MISSING", "surface bound no wl_pointer")
        top = ref.toplevel
        first = seat.first_pointer()

        for p in seat.pointers:
            self._ptr(top, p, "enter", x, y, None)
            self.s._raw_frame(top, p)

        for p in seat.pointers:
            self._ptr(top, p, "motion", x, y, None)
            self.s._raw_frame(top, p)

        self._ptr(top, first, "button_press", x, y, button)
        self.s._raw_frame(top, first)

        self._ptr(top, first, "button_release", x, y, button)
        self.s._raw_frame(top, first)

        return {"action": "click", "surface": ref.surface_id, "toplevel": top,
                "at": [x, y], "button": button, "delivered_to": first,
                "hover_seats": list(seat.pointers)}

    def click_node(self, ref: SurfaceRef, node: Node) -> dict:

        cx, cy = node.center()
        out = self.click(ref, cx, cy)
        out["node"] = node.node_id
        out["node_name"] = node.name
        out["node_role"] = node.role
        return out

    def _ptr(self, top, seatobj, kind, x, y, button):
        self.s._raw_pointer(top, seatobj, kind, x, y, button,
                            serial=next(self._serial), t_ms=now_ms())

    def type_text(self, ref: SurfaceRef, text: str, *, secret: bool = False) -> dict:

        self.assert_renderable(ref)
        seat = self.s.seat_objects(ref.surface_id)
        if not seat.keyboards:
            raise SurfaceError("ERR_CAP_MISSING", "surface bound no wl_keyboard")
        kbd = seat.first_keyboard()
        top = ref.toplevel
        n = 0
        for ch in text:
            keysym = _keysym_for(ch)
            self.s._raw_key(top, kbd, keysym, serial=next(self._serial), t_ms=now_ms())
            n += 1
        desc = {"action": "type", "surface": ref.surface_id, "toplevel": top,
                "delivered_to": kbd, "count": n, "secret": bool(secret)}
        if not secret:
            desc["text"] = text
        return desc

    def key(self, ref: SurfaceRef, keysym: str) -> dict:

        self.assert_renderable(ref)
        seat = self.s.seat_objects(ref.surface_id)
        kbd = seat.first_keyboard()
        self.s._raw_key(ref.toplevel, kbd, keysym, serial=next(self._serial), t_ms=now_ms())
        return {"action": "key", "surface": ref.surface_id, "toplevel": ref.toplevel,
                "key": keysym, "delivered_to": kbd}

    def focus(self, ref: SurfaceRef, node: Node) -> dict:

        self.click_node(ref, node)
        return {"action": "focus", "surface": ref.surface_id, "toplevel": ref.toplevel,
                "node": node.node_id}

_NAMED = {" ": "space", "\n": "Return", "\t": "Tab"}

def _keysym_for(ch: str) -> str:

    return _NAMED.get(ch, ch)
