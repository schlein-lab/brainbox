
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import St from 'gi://St';
import Meta from 'gi://Meta';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {VALID_STAGES} from './constants.js';

const PROFILES = {
    A: {
        stage: 'A',
        name: 'autonom',
        gate: 'on',
        phantomActive: true,
        appsHeadless: true,
        desktopChrome: false,
        llmHidden: false,
        chrome: {
            hud:     {opacity: 255, reactive: false},
            overlay: {opacity: 0, reactive: false},
        },
        tileRouting: 'LLM_ONLY',
        confirmAffordance: false,
    },
    B: {
        stage: 'B',
        name: 'desktop + phantom',
        gate: 'on',
        phantomActive: true,
        appsHeadless: false,
        desktopChrome: true,
        llmHidden: true,
        chrome: {
            hud:     {opacity: 255, reactive: false},

            overlay: {opacity: 0, reactive: false},
        },
        tileRouting: 'SHARED',
        confirmAffordance: false,
    },
    C: {
        stage: 'C',
        name: 'OFF',
        gate: 'off',
        phantomActive: false,
        appsHeadless: false,
        desktopChrome: true,
        llmHidden: true,
        chrome: {
            hud:     {opacity: 255, reactive: false},
            overlay: {opacity: 0, reactive: false},
        },
        tileRouting: 'HUMAN_ONLY',
        confirmAffordance: false,
    },
};

export class ModeController {
    constructor(service) {
        this._service = service;
        this._level = null;
        this._listeners = new Set();
        this._registry = {hud: null, overlay: null, tiles: []};

        this._llmMatch = '';

        this._focal = new Map();

        this._llmMinimized = new Set();

        this._headless = new Map();
        this._destroyIds = new Map();

        this._pending = [];

        this._windowCreatedId = 0;
        this._newWinHandlers = new Map();
    }

    enableWindowTracking() {
        if (this._windowCreatedId)
            return;
        this._windowCreatedId = global.display.connect(
            'window-created', (_display, win) => this._onWindowCreated(win));
    }

    disableWindowTracking() {
        if (this._windowCreatedId) {
            try { global.display.disconnect(this._windowCreatedId); } catch (e) {}
            this._windowCreatedId = 0;
        }
        for (const win of Array.from(this._newWinHandlers.keys()))
            this._unhookNewWindow(win);
        this._newWinHandlers.clear();
    }

    _killLlmWindow() {
        this._eachLlmWindow((win) => {
            try { win.kill(); } catch (e) {
                try { win.delete(global.get_current_time()); } catch (e2) {}
            }
        });
        this._llmMinimized.clear();
    }

    _onWindowCreated(win) {
        if (!win)
            return;

        const p = this.profile;
        if (!p || !p.appsHeadless)
            return;
        if (this._newWinHandlers.has(win))
            return;

        const rec = {settleId: 0, shownId: 0};
        const fire = () => {

            this._unhookNewWindow(win);

            const cur = this.profile;
            if (cur && cur.appsHeadless)
                this._classifyOneWindow(win);
        };

        rec.settleId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 150, () => {
            rec.settleId = 0;
            fire();
            return GLib.SOURCE_REMOVE;
        });

        try {
            rec.shownId = win.connect('shown', () => {
                rec.shownId = 0;
                fire();
            });
        } catch (e) {

            rec.shownId = 0;
        }
        this._newWinHandlers.set(win, rec);
    }

    _unhookNewWindow(win) {
        const rec = this._newWinHandlers.get(win);
        if (!rec)
            return;
        if (rec.settleId) {
            GLib.source_remove(rec.settleId);
            rec.settleId = 0;
        }
        if (rec.shownId) {
            try { win.disconnect(rec.shownId); } catch (e) {}
            rec.shownId = 0;
        }
        this._newWinHandlers.delete(win);
    }

    _classifyOneWindow(win) {
        if (!win)
            return;
        if (win.get_window_type() !== Meta.WindowType.NORMAL)
            return;
        const actor = win.get_compositor_private();
        if (!actor)
            return;
        if (this._isLlmWindow(win))
            this._promoteFocal(win, actor);
        else
            this._demoteOne(win, actor);
    }

    registerHud(actor)     { this._registry.hud = actor; }
    registerOverlay(actor) { this._registry.overlay = actor; }
    registerTile(tile)     { this._registry.tiles.push(tile); }

    setLlmMatch(match) {
        const next = (match ?? '').toString().trim().toLowerCase();
        if (next === this._llmMatch)
            return;
        this._llmMatch = next;
        if (this._level === 'A')
            this.applyProfile('A');
    }
    get llmMatch() { return this._llmMatch; }

    _isLlmWindow(win) {
        if (!this._llmMatch || !win)
            return false;
        const hay = [
            win.get_wm_class() ?? '',
            win.get_wm_class_instance ? (win.get_wm_class_instance() ?? '') : '',
            win.get_title() ?? '',
        ].join(' ').toLowerCase();
        return hay.includes(this._llmMatch);
    }

    connect(cb) { this._listeners.add(cb); return cb; }
    disconnect(cb) { this._listeners.delete(cb); }

    get level() { return this._level; }
    get profile() { return PROFILES[this._level] ?? null; }

    queueConfirm(label, fn) {
        this._pending.push({label, fn, ts: GLib.get_monotonic_time()});
        return this._pending.length;
    }
    get pendingCount() { return this._pending.length; }

    drainOnePending() {
        const item = this._pending.shift();
        if (!item)
            return false;
        try { item.fn(); } catch (e) { logError(e, `phantom: pending ${item.label} failed`); }
        return true;
    }
    clearPending() { this._pending = []; }

    applyProfile(stage) {
        if (!VALID_STAGES.includes(stage))
            return false;
        const next = PROFILES[stage];

        this._service.writeStageMirror(stage);

        if (next.appsHeadless)
            this._demoteApps();
        else
            this._restoreApps();

        this._applyChrome(this._registry.hud, next.chrome.hud);
        this._applyChrome(this._registry.overlay, next.chrome.overlay);

        this._applyDesktopChrome(next);

        if (next.phantomActive === false)
            this._killLlmWindow();
        else if (next.llmHidden)
            this._hideLlmWindow();
        else
            this._restoreLlmWindow();

        for (const tile of this._registry.tiles)
            this._applyTileRouting(tile, next.tileRouting);

        this._level = stage;
        for (const cb of this._listeners) {
            try { cb(stage, next); } catch (e) { logError(e, 'phantom: mode-changed listener'); }
        }
        return true;
    }

    _applyChrome(actor, spec) {
        if (!actor || !spec)
            return;

        actor.opacity = spec.opacity;
        actor.reactive = spec.reactive;
    }

    _applyTileRouting(tile, routing) {

        const reactive = routing === 'SHARED' || routing === 'HUMAN_ONLY';
        if (tile && tile.inputProxy)
            tile.inputProxy.reactive = reactive;
    }

    _applyDesktopChrome(profile) {
        const show = profile.desktopChrome !== false;
        try {
            if (show) {
                Main.layoutManager.panelBox.visible = true;
                Main.panel.show();
            } else {
                Main.panel.hide();

                Main.layoutManager.panelBox.visible = false;
            }
        } catch (e) { logError(e, 'phantom: panel toggle'); }
        for (const dock of this._findDocks()) {
            try { if (show) dock.show(); else dock.hide(); } catch (e) {}
        }
    }

    _findDocks() {
        const found = [];
        try {
            for (const tracked of (Main.layoutManager._trackedActors ?? [])) {
                const a = tracked && tracked.actor;
                if (a && a.name === 'dashtodockContainer') found.push(a);
            }
        } catch (e) {}
        if (found.length === 0) {
            const walk = (actor) => {
                if (!actor) return;
                if (actor.name === 'dashtodockContainer') found.push(actor);
                const kids = actor.get_children ? actor.get_children() : [];
                for (const c of kids) walk(c);
            };
            try { walk(Main.layoutManager.uiGroup); } catch (e) {}
        }
        return found;
    }

    _demoteApps() {
        const actors = global.get_window_actors();
        for (const actor of actors) {
            const win = actor.meta_window;
            if (!win)
                continue;

            if (win.get_window_type() !== Meta.WindowType.NORMAL)
                continue;
            const id = win.get_id();

            if (this._isLlmWindow(win)) {
                this._promoteFocal(win, actor);
                continue;
            }

            this._demoteOne(win, actor);
        }
    }

    _demoteOne(win, actor) {
        const id = win.get_id();
        if (this._headless.has(id))
            return;

        const clone = new Clutter.Clone({
            source: actor,
            width: 1, height: 1,
            x: -32, y: -32,
            opacity: 255,
        });
        Main.uiGroup.add_child(clone);

        const saved = {
            actor,
            opacity: actor.opacity,
            x: actor.x,
            y: actor.y,
            clone,
        };
        actor.opacity = 0;

        actor.set_position(-10000, -10000);
        this._headless.set(id, saved);

        const did = actor.connect('destroy', () => this._reapHeadless(id));
        this._destroyIds.set(id, did);
    }

    _promoteFocal(win, actor) {
        const id = win.get_id();

        if (this._headless.has(id)) {
            this._restoreOne(id, this._headless.get(id));
            this._headless.delete(id);
        }
        if (!this._focal.has(id)) {
            const fr0 = win.get_frame_rect();
            this._focal.set(id, {
                actor,
                x: fr0.x,
                y: fr0.y,
                opacity: actor.opacity,
            });
        }

        actor.opacity = 255;
        actor.show();
        try {
            const mon = win.get_monitor();
            const wa = (mon >= 0)
                ? Main.layoutManager.getWorkAreaForMonitor(mon)
                : Main.layoutManager.getWorkAreaForMonitor(Main.layoutManager.primaryIndex);
            if (wa) {
                const fr = win.get_frame_rect();
                const cx = wa.x + Math.max(0, Math.floor((wa.width  - fr.width)  / 2));
                const cy = wa.y + Math.max(0, Math.floor((wa.height - fr.height) / 2));
                win.move_frame(true, cx, cy);
            }
        } catch (e) {

        }
        try {
            win.raise();
            win.activate(global.get_current_time());
        } catch (e) {}
    }

    _restoreFocal() {
        for (const [id, saved] of this._focal.entries()) {
            const {actor} = saved;
            if (actor) {
                actor.opacity = saved.opacity;
                try {
                    const win = actor.meta_window;
                    if (win) win.move_frame(true, saved.x, saved.y);
                } catch (e) {}
            }
        }
        this._focal.clear();
    }

    _restoreApps() {
        for (const [id, saved] of this._headless.entries()) {
            this._restoreOne(id, saved);
        }
        this._headless.clear();

        this._restoreFocal();
    }

    _restoreOne(id, saved) {
        const {actor} = saved;
        const did = this._destroyIds.get(id);
        if (did && actor) {
            try { actor.disconnect(did); } catch (e) {}
        }
        this._destroyIds.delete(id);
        if (actor) {
            actor.opacity = saved.opacity;
            actor.set_position(saved.x, saved.y);
        }
        if (saved.clone) {
            saved.clone.destroy();
        }
    }

    _reapHeadless(id) {

        const saved = this._headless.get(id);
        if (!saved)
            return;
        this._destroyIds.delete(id);
        if (saved.clone)
            saved.clone.destroy();
        this._headless.delete(id);
    }

    _eachLlmWindow(cb) {
        for (const actor of global.get_window_actors()) {
            const win = actor.meta_window;
            if (win && this._isLlmWindow(win))
                cb(win, actor);
        }
    }

    _hideLlmWindow() {
        this._eachLlmWindow((win) => {
            try {
                if (!win.minimized) {
                    this._llmMinimized.add(win.get_id());
                    win.minimize();
                }
            } catch (e) {}
        });
    }

    _restoreLlmWindow() {
        this._eachLlmWindow((win) => {
            try {
                if (this._llmMinimized.has(win.get_id())) {
                    if (win.minimized)
                        win.unminimize();
                    this._llmMinimized.delete(win.get_id());
                }
            } catch (e) {}
        });
    }

    toggleLlmWindow() {
        let found = false, focused = false;
        this._eachLlmWindow((win) => {
            found = true;
            if (win.has_focus && win.has_focus()) focused = true;
        });
        if (!found)
            return;
        if (focused)
            this._hideLlmWindow();
        else
            this.bringLlmToFront();
    }

    bringLlmToFront() {
        this._eachLlmWindow((win) => {
            try {
                const wasHidden = win.minimized;
                if (win.minimized)
                    win.unminimize();
                this._llmMinimized.delete(win.get_id());

                const actor = win.get_compositor_private();
                if (actor && actor.opacity < 255)
                    actor.opacity = 255;
                if (wasHidden && this._level !== 'A') {

                    try { win.unmaximize(Meta.MaximizeFlags.BOTH); } catch (e) {}
                    this._compactResize(win);
                    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 250, () => {
                        try {
                            win.unmaximize(Meta.MaximizeFlags.BOTH);
                            this._compactResize(win);
                        } catch (e) {}
                        return GLib.SOURCE_REMOVE;
                    });
                }
                win.raise();
                win.activate(global.get_current_time());
            } catch (e) {}
        });
    }

    _compactResize(win) {
        const mon = win.get_monitor();
        const wa = (mon >= 0)
            ? Main.layoutManager.getWorkAreaForMonitor(mon)
            : Main.layoutManager.getWorkAreaForMonitor(Main.layoutManager.primaryIndex);
        if (!wa) return;
        const w = Math.min(560, Math.floor(wa.width * 0.42));
        const h = Math.min(640, Math.floor(wa.height * 0.82));
        const x = wa.x + wa.width - w - 18;
        const y = wa.y + 10;
        win.move_resize_frame(true, x, y, w, h);
    }

    debugState() {
        const p = this.profile;
        const hud = this._registry.hud;
        const overlay = this._registry.overlay;
        return {
            stage: this._level,
            gate: p ? p.gate : null,
            confirmAffordance: p ? p.confirmAffordance : null,
            pendingConfirm: this._pending.length,
            appsHeadless: p ? p.appsHeadless : null,
            headlessCount: this._headless.size,
            keepAliveClones: this._headless.size,
            llmMatch: this._llmMatch,
            focalCount: this._focal.size,

            focal: Array.from(this._focal.entries()).map(([id, s]) => {
                const w = s.actor ? s.actor.meta_window : null;
                return {
                    id,
                    wm_class: w ? (w.get_wm_class() ?? '') : '',
                    title: w ? (w.get_title() ?? '') : '',
                    opacity: s.actor ? s.actor.opacity : null,
                };
            }),
            tileRouting: p ? p.tileRouting : null,
            groups: {
                hud: hud ? {opacity: hud.opacity, reactive: !!hud.reactive,
                            mapped: !!hud.mapped} : null,
                overlay: overlay ? {opacity: overlay.opacity, reactive: !!overlay.reactive,
                                    mapped: !!overlay.mapped} : null,
            },
        };
    }

    destroy() {

        if (this._windowCreatedId) {
            try { global.display.disconnect(this._windowCreatedId); } catch (e) {}
            this._windowCreatedId = 0;
        }

        for (const win of Array.from(this._newWinHandlers.keys()))
            this._unhookNewWindow(win);
        this._newWinHandlers.clear();

        this._restoreApps();
        this._restoreLlmWindow();

        try { Main.layoutManager.panelBox.visible = true; Main.panel.show(); } catch (e) {}
        for (const dock of this._findDocks()) { try { dock.show(); } catch (e) {} }
        this._listeners.clear();
        this._pending = [];
        this._registry = {hud: null, overlay: null, tiles: []};
        this._service = null;
    }
}
