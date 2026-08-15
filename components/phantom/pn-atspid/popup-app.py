#!/usr/bin/env python3

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk

class App(Gtk.Window):
    def __init__(self):
        super().__init__(title="popup-app")
        css = Gtk.CssProvider()
        css.load_from_data(b"window { background: #20C040; }")
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        fixed = Gtk.Fixed()
        self.add(fixed)
        self.btn = Gtk.MenuButton(label="MENU")
        self.btn.set_size_request(120, 40)
        menu = Gtk.Menu()
        for lbl in ("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta"):
            menu.append(Gtk.MenuItem(label=lbl))
        menu.show_all()
        self.btn.set_popup(menu)
        fixed.put(self.btn, 20, 20)
        self.connect("destroy", Gtk.main_quit)

def main():
    a = App()
    a.show_all()
    a.fullscreen()
    Gtk.main()

if __name__ == "__main__":
    main()
