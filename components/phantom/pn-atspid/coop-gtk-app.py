#!/usr/bin/env python3

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

class App(Gtk.Window):
    def __init__(self):
        super().__init__(title="coop-gtk")
        self.set_default_size(360, 220)
        self.count = 0
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        self.add(box)

        self.counter = Gtk.Label(label="count: 0")
        self.counter.set_name("counter")
        box.pack_start(self.counter, False, False, 0)

        inc = Gtk.Button(label="Increment")
        inc.connect("clicked", self.on_inc)
        box.pack_start(inc, False, False, 0)

        self.mute = Gtk.ToggleButton(label="Mute")
        self.mute.connect("toggled", self.on_mute)
        box.pack_start(self.mute, False, False, 0)

        self.status = Gtk.Label(label="mute: off")
        box.pack_start(self.status, False, False, 0)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Message")

        self.entry.get_accessible().set_name("Message")
        box.pack_start(self.entry, False, False, 0)

        self.pw = Gtk.Entry()
        self.pw.set_visibility(False)
        self.pw.set_text("s3cret-fixture")
        self.pw.get_accessible().set_name("Secret")
        box.pack_start(self.pw, False, False, 0)

        self.connect("destroy", Gtk.main_quit)

    def on_inc(self, _btn):
        self.count += 1
        self.counter.set_text(f"count: {self.count}")

    def on_mute(self, btn):
        self.status.set_text("mute: on" if btn.get_active() else "mute: off")

def main():
    w = App()
    w.show_all()
    Gtk.main()

if __name__ == "__main__":
    main()
