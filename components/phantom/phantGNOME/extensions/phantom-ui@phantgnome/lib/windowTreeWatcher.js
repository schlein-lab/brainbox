
import GLib from 'gi://GLib';

export class WindowTreeWatcher {
    constructor(service) {
        this._service = service;
        this._ids = [];
        this._perWindowIds = new Map();
        this._debounceId = 0;
        this._pendingReason = '';
    }

    enable() {
        if (this._ids.length)
            return;
        const display = global.display;
        const wm = global.workspace_manager;

        this._connect(display, 'window-created', (_d, win) => {
            this._hookWindow(win);
            this._fire('created');
        });
        this._connect(display, 'window-entered-monitor', () => this._fire('entered-monitor'));
        this._connect(display, 'window-left-monitor', () => this._fire('left-monitor'));
        this._connect(display, 'restacked', () => this._fire('restacked'));
        this._connect(display, 'notify::focus-window', () => this._fire('focus-change'));
        if (wm) {
            this._connect(wm, 'active-workspace-changed', () => this._fire('workspace-change'));
            this._connect(wm, 'workspace-switched', () => this._fire('workspace-switched'));
        }

        for (const actor of global.get_window_actors()) {
            const win = actor.meta_window;
            if (win)
                this._hookWindow(win);
        }
    }

    _connect(obj, signal, cb) {
        const id = obj.connect(signal, cb);
        this._ids.push({obj, id});
    }

    _hookWindow(win) {
        if (!win || this._perWindowIds.has(win))
            return;
        const id = win.connect('unmanaged', () => {
            const hid = this._perWindowIds.get(win);
            if (hid) {
                try { win.disconnect(hid); } catch (e) {}
                this._perWindowIds.delete(win);
            }
            this._fire('destroyed');
        });
        this._perWindowIds.set(win, id);
    }

    _fire(reason) {
        this._pendingReason = reason;
        if (this._debounceId)
            return;
        this._debounceId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 120, () => {
            this._debounceId = 0;
            if (this._service)
                this._service.emitWindowsChanged(this._pendingReason);
            return GLib.SOURCE_REMOVE;
        });
    }

    disable() {
        if (this._debounceId) {
            GLib.source_remove(this._debounceId);
            this._debounceId = 0;
        }
        for (const {obj, id} of this._ids) {
            try { obj.disconnect(id); } catch (e) {}
        }
        this._ids = [];
        for (const [win, id] of this._perWindowIds.entries()) {
            try { win.disconnect(id); } catch (e) {}
        }
        this._perWindowIds.clear();
    }

    destroy() {
        this.disable();
        this._service = null;
    }
}
