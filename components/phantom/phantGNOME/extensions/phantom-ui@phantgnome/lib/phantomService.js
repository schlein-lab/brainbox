
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import Shell from 'gi://Shell';
import Meta from 'gi://Meta';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {
    PHANTOM_BUS_NAME,
    PHANTOM_OBJECT_PATH,
    VALID_STAGES,
    BTN_LEFT,
    BTN_STATE_PRESSED,
    BTN_STATE_RELEASED,
} from './constants.js';
import {IFACE_XML} from './dbusIface.js';
import {FusionTile} from './fusion.js';

let _activeExportedService = null;

export class PhantomService {
    constructor(extension) {
        this._extension = extension;
        this._settings = extension.getSettings();
        this._dbusImpl = Gio.DBusExportedObject.wrapJSObject(IFACE_XML, this);
        this._mode = null;
    }

    setModeController(mode) { this._mode = mode; }

    export() {

        if (_activeExportedService && _activeExportedService !== this) {
            try { _activeExportedService.unexport(); } catch (e) {}
        }
        try {
            this._dbusImpl.unexport();
        } catch (e) {

        }
        this._dbusImpl.export(Gio.DBus.session, PHANTOM_OBJECT_PATH);
        _activeExportedService = this;
        this._nameId = Gio.bus_own_name(
            Gio.BusType.SESSION,
            PHANTOM_BUS_NAME,
            Gio.BusNameOwnerFlags.REPLACE,
            null, null, null);
    }

    unexport() {

        this._disposeVirtualDevices();
        if (this._nameId) {
            Gio.bus_unown_name(this._nameId);
            this._nameId = 0;
        }
        if (this._dbusImpl) {
            try { this._dbusImpl.unexport(); } catch (e) {}
            this._dbusImpl = null;
        }
        if (_activeExportedService === this)
            _activeExportedService = null;
    }

    writeStageMirror(stage) {
        if (!VALID_STAGES.includes(stage))
            return;
        if (this._settings.get_string('stage') !== stage)
            this._settings.set_string('stage', stage);
        if (this._dbusImpl)
            this._dbusImpl.emit_property_changed('Stage', new GLib.Variant('s', stage));
    }

    get Stage() {
        return this._settings.get_string('stage');
    }

    SetStage(stage) {
        if (!this._mode)
            return false;
        return this._mode.applyProfile(stage);
    }

    ToggleLlm() {
        if (!this._mode || !this._mode.toggleLlmWindow)
            return false;
        try { this._mode.toggleLlmWindow(); } catch (e) { return false; }
        return true;
    }

    SummonLlm() {
        if (!this._mode || !this._mode.bringLlmToFront)
            return false;
        try { this._mode.bringLlmToFront(); } catch (e) { return false; }
        return true;
    }

    DebugState() {
        try {
            const st = this._mode ? this._mode.debugState() : {stage: this.Stage};

            if (this._fusionScene) {
                const tiles = this._fusionScene.list();
                st.fusionCount = tiles.length;
                st.fusionRendered = tiles.filter(t => t.rendered).length;
                st.fusion = tiles;
            } else {
                st.fusionCount = 0;
                st.fusionRendered = 0;
                st.fusion = [];
            }
            return JSON.stringify(st);
        } catch (e) {
            logError(e, 'phantom: DebugState failed');
            return JSON.stringify({error: String(e)});
        }
    }

    async SnapshotAsync(params, invocation) {
        try {
            const shooter = new Shell.Screenshot();
            const [content] = await shooter.screenshot_stage_to_content();
            const texture = content.get_texture();
            const stream = Gio.MemoryOutputStream.new_resizable();
            await Shell.Screenshot.composite_to_stream(
                texture, 0, 0, -1, -1, 1, null, 0, 0, 1, stream);
            stream.close(null);
            const bytes = stream.steal_as_bytes();
            invocation.return_value(new GLib.Variant('(ay)', [bytes.get_data()]));
        } catch (e) {
            logError(e, 'phantom: Snapshot failed');
            invocation.return_error_literal(
                Gio.DBusError, Gio.DBusError.FAILED, `Snapshot failed: ${e.message}`);
        }
    }

    _windowById(id) {
        for (const actor of global.get_window_actors()) {
            const win = actor.meta_window;
            if (win && win.get_id() === id)
                return win;
        }
        return null;
    }

    ActivateWindowAsync([id, _timestamp], invocation) {
        const out = this._gatedAct('ActivateWindow', false, () => {
            const win = this._windowById(id);
            if (!win) throw new Error(`No window with id ${id}`);
            if (win.minimized) win.unminimize();
            const activeWs = global.workspace_manager.get_active_workspace();
            if (win.get_workspace() !== activeWs) win.change_workspace(activeWs);
            Main.activateWindow(win, 0);
            win.raise();
            return win.has_focus();
        });
        invocation.return_value(new GLib.Variant('(b)', [!!out.ok && out.result === true]));
    }

    ListWindows() {
        try {
            const display = global.display;
            const stacked = display.sort_windows_by_stacking(
                global.get_window_actors().map(a => a.meta_window).filter(w => !!w));
            const focus = display.get_focus_window();
            const out = stacked.map((win, idx) => {
                const r = win.get_frame_rect();
                return {
                    id: win.get_id(),
                    title: win.get_title() ?? '',
                    wm_class: win.get_wm_class() ?? '',
                    window_type: win.get_window_type(),
                    focus: win === focus,
                    minimized: !!win.minimized,
                    x: r.x, y: r.y, width: r.width, height: r.height,
                    stacking: idx,
                };
            });
            return JSON.stringify(out);
        } catch (e) {
            logError(e, 'phantom: ListWindows failed');
            return '[]';
        }
    }

    _gate(humanDemand) {
        const profile = this._mode ? this._mode.profile : null;
        const g = profile ? profile.gate : 'off';

        if (g === 'on')
            return {allow: true, reason: 'phantom on (full access)', queued: false};

        return {allow: false, reason: 'phantom OFF — acts blocked', queued: false};
    }

    _queueForConfirm(label, fn) {
        if (this._mode && typeof this._mode.queueConfirm === 'function')
            this._mode.queueConfirm(label, fn);
        else
            log(`phantom: stage-B queued (no controller queue): ${label}`);
    }

    _gatedAct(label, humanDemand, fn) {
        const dec = this._gate(humanDemand);
        if (dec.allow) {
            try {
                const res = fn();
                return {ok: true, gate: dec.reason, result: res};
            } catch (e) {
                logError(e, `phantom: ${label} failed`);
                return {ok: false, gate: dec.reason, error: String(e)};
            }
        }
        if (dec.queued)
            this._queueForConfirm(label, fn);
        return {ok: false, gate: dec.reason, queued: !!dec.queued, executed: false};
    }

    _helperPath() {
        return GLib.build_filenamev([this._extension.path, 'phantom-atspi-helper.py']);
    }

    _runHelper(argv, invocation, timeoutMs = 4000) {
        const full = ['python3', this._helperPath(), ...argv];
        let proc;
        try {
            proc = Gio.Subprocess.new(
                full,
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
        } catch (e) {
            invocation.return_value(new GLib.Variant('(s)',
                [JSON.stringify({ok: false, error: `spawn: ${e.message}`})]));
            return;
        }

        const cancellable = new Gio.Cancellable();
        const timeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, timeoutMs, () => {
            try { proc.force_exit(); } catch (e) {}
            cancellable.cancel();
            return GLib.SOURCE_REMOVE;
        });

        proc.communicate_utf8_async(null, cancellable, (p, res) => {
            GLib.source_remove(timeoutId);
            let payload;
            try {
                const [, stdout, stderr] = p.communicate_utf8_finish(res);
                const line = (stdout || '').trim().split('\n').filter(Boolean).pop() || '';
                if (line) {
                    JSON.parse(line);
                    payload = line;
                } else {
                    payload = JSON.stringify(
                        {ok: false, error: `helper produced no JSON; stderr: ${(stderr || '').slice(0, 200)}`});
                }
            } catch (e) {
                payload = JSON.stringify({ok: false, error: `helper: ${e.message}`});
            }
            invocation.return_value(new GLib.Variant('(s)', [payload]));
        });
    }

    InvokeActionAsync([appHint, selector, action], invocation) {
        const dec = this._gate(false);
        if (!dec.allow) {
            if (dec.queued)
                this._queueForConfirm(`InvokeAction(${selector},${action})`, () => {});
            invocation.return_value(new GLib.Variant('(s)',
                [JSON.stringify({ok: false, gate: dec.reason, queued: !!dec.queued, executed: false})]));
            return;
        }
        const sel = this._joinSel(appHint, selector);
        this._runHelper(['action', sel, String(action)], invocation);
    }

    WriteWidgetAsync([appHint, selector, text], invocation) {
        const dec = this._gate(false);
        if (!dec.allow) {
            if (dec.queued)
                this._queueForConfirm(`WriteWidget(${selector})`, () => {});
            invocation.return_value(new GLib.Variant('(s)',
                [JSON.stringify({ok: false, gate: dec.reason, queued: !!dec.queued, executed: false})]));
            return;
        }
        const sel = this._joinSel(appHint, selector);
        this._runHelper(['write', sel, String(text)], invocation);
    }

    ReadWidgetAsync([appHint, selector], invocation) {
        const sel = this._joinSel(appHint, selector);
        this._runHelper(['read', sel], invocation);
    }

    ReadTreeAsync([appHint, maxNodes], invocation) {
        const n = (maxNodes && maxNodes > 0) ? maxNodes : 400;
        const sel = appHint && appHint.length ? appHint : '0';
        this._runHelper(['tree', sel, '--max', String(n), '--text'], invocation, 6000);
    }

    ListA11yAppsAsync(_params, invocation) {
        this._runHelper(['list'], invocation);
    }

    _joinSel(appHint, selector) {
        const s = selector || '';
        if (!appHint || appHint.length === 0)
            return s;
        if (s.length === 0)
            return appHint;

        if (s.startsWith(appHint) || /^\d/.test(s) || s.includes('?') || s.startsWith(appHint + '/'))
            return s;
        return `${appHint}/${s.replace(/^\/+/, '')}`;
    }

    _ensureVirtualDevices() {
        if (this._vptr && this._vkbd)
            return true;
        try {
            const seat = Clutter.get_default_backend().get_default_seat();
            this._vptr = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
            this._vkbd = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
            return true;
        } catch (e) {
            logError(e, 'phantom: create_virtual_device failed');
            this._vptr = null;
            this._vkbd = null;
            return false;
        }
    }

    _pointIsOnMappedWindow(x, y) {
        const actors = global.get_window_actors();

        for (let i = actors.length - 1; i >= 0; i--) {
            const a = actors[i];
            const win = a.meta_window;
            if (!win)
                continue;
            if (!a.mapped || !a.visible)
                continue;
            if (win.minimized)
                continue;
            const r = win.get_buffer_rect();
            if (x >= r.x && x < r.x + r.width && y >= r.y && y < r.y + r.height)
                return true;
        }
        return false;
    }

    ClickAsync([x, y, button], invocation) {
        const dec = this._gate(false);
        if (!dec.allow) {
            if (dec.queued)
                this._queueForConfirm(`Click(${x},${y})`, () => {});
            invocation.return_value(new GLib.Variant('(b)', [false]));
            return;
        }
        const ok = this.injectClickAt(x, y, button);
        invocation.return_value(new GLib.Variant('(b)', [ok]));
    }

    injectClickAt(x, y, button, skipGate) {
        if (!skipGate) {
            const dec = this._gate(false);
            if (!dec.allow) {
                log(`phantom: injectClickAt refused by gate (${dec.reason})`);
                return false;
            }
        }

        if (!this._pointIsOnMappedWindow(x, y)) {
            log(`phantom: injectClickAt refused — (${x},${y}) not on a visible+mapped window (use Funktionsbus for hidden targets)`);
            return false;
        }
        if (!this._ensureVirtualDevices())
            return false;
        try {
            const btn = (button && button > 0) ? button : BTN_LEFT;

            this._vptr.notify_absolute_motion(0, x, y);
            this._vptr.notify_button(0, btn, BTN_STATE_PRESSED);
            this._vptr.notify_button(0, btn, BTN_STATE_RELEASED);
            return true;
        } catch (e) {
            logError(e, 'phantom: Click inject failed');
            return false;
        }
    }

    TypeAsync([text], invocation) {
        const dec = this._gate(false);
        if (!dec.allow) {
            if (dec.queued)
                this._queueForConfirm(`Type(${(text || '').slice(0, 24)})`, () => {});
            invocation.return_value(new GLib.Variant('(b)', [false]));
            return;
        }
        const focus = global.display.get_focus_window();
        if (!focus) {
            log('phantom: Type refused — no focused window (use WriteWidget for background/focus-free text)');
            invocation.return_value(new GLib.Variant('(b)', [false]));
            return;
        }
        if (!this._ensureVirtualDevices()) {
            invocation.return_value(new GLib.Variant('(b)', [false]));
            return;
        }
        try {
            for (const ch of (text || '')) {
                const cp = ch.codePointAt(0);
                const keyval = Clutter.unicode_to_keysym ? Clutter.unicode_to_keysym(cp) : cp;
                this._vkbd.notify_keyval(0, keyval, Clutter.KeyState.PRESSED);
                this._vkbd.notify_keyval(0, keyval, Clutter.KeyState.RELEASED);
            }
            invocation.return_value(new GLib.Variant('(b)', [true]));
        } catch (e) {
            logError(e, 'phantom: Type inject failed');
            invocation.return_value(new GLib.Variant('(b)', [false]));
        }
    }

    _disposeVirtualDevices() {
        for (const dev of [this._vptr, this._vkbd]) {
            if (dev) {
                try { dev.run_dispose(); } catch (e) {}
            }
        }
        this._vptr = null;
        this._vkbd = null;
    }

    MinimizeAsync([id], invocation) {
        const out = this._gatedAct('Minimize', false, () => {
            const win = this._windowById(id);
            if (!win) throw new Error(`no window ${id}`);
            win.minimize();
            return {id, minimized: true};
        });
        invocation.return_value(new GLib.Variant('(b)', [!!out.ok]));
    }

    MaximizeAsync([id], invocation) {
        const out = this._gatedAct('Maximize', false, () => {
            const win = this._windowById(id);
            if (!win) throw new Error(`no window ${id}`);
            if (win.minimized)
                win.unminimize();
            win.maximize(Meta.MaximizeFlags.BOTH);
            return {id, maximized: true};
        });
        invocation.return_value(new GLib.Variant('(b)', [!!out.ok]));
    }

    MakeAboveAsync([id, on], invocation) {
        const out = this._gatedAct('MakeAbove', false, () => {
            const win = this._windowById(id);
            if (!win) throw new Error(`no window ${id}`);
            if (on)
                win.make_above();
            else
                win.unmake_above();
            return {id, above: !!on};
        });
        invocation.return_value(new GLib.Variant('(b)', [!!out.ok]));
    }

    MoveToWorkspaceAsync([id, ws], invocation) {
        const out = this._gatedAct('MoveToWorkspace', false, () => {
            const win = this._windowById(id);
            if (!win) throw new Error(`no window ${id}`);
            if (ws < 0) throw new Error(`bad workspace ${ws}`);
            win.change_workspace_by_index(ws, false);
            return {id, ws};
        });
        invocation.return_value(new GLib.Variant('(b)', [!!out.ok]));
    }

    FocusedWindow() {
        try {
            const win = global.display.get_focus_window();
            if (!win)
                return JSON.stringify(null);
            const r = win.get_frame_rect();
            return JSON.stringify({
                id: win.get_id(),
                title: win.get_title() ?? '',
                wm_class: win.get_wm_class() ?? '',
                window_type: win.get_window_type(),
                x: r.x, y: r.y, width: r.width, height: r.height,
            });
        } catch (e) {
            logError(e, 'phantom: FocusedWindow failed');
            return JSON.stringify(null);
        }
    }

    emitWindowsChanged(reason) {
        if (this._dbusImpl) {
            try {
                this._dbusImpl.emit_signal('WindowsChanged',
                    new GLib.Variant('(s)', [reason || '']));
            } catch (e) {
                logError(e, 'phantom: emit WindowsChanged failed');
            }
        }
    }

    setFusionScene(scene) { this._fusionScene = scene; }

    CreateMirrorAsync([sourceId, scale, interactive], invocation) {
        let payload;
        try {
            if (!this._fusionScene)
                throw new Error('fusion scene not available');
            const win = this._windowById(sourceId);
            if (!win)
                throw new Error(`no window with id ${sourceId}`);
            const tile = new FusionTile(this, win, this._fusionScene, {
                scale: (scale && scale > 0) ? scale : 0.4,
                interactive: !!interactive,
            });
            this._fusionScene.addTile(tile);
            payload = JSON.stringify(Object.assign({ok: true}, tile.info()));
        } catch (e) {
            logError(e, 'phantom: CreateMirror failed');
            payload = JSON.stringify({ok: false, error: String(e.message || e)});
        }
        invocation.return_value(new GLib.Variant('(s)', [payload]));
    }

    DestroyMirror(mirrorId) {
        try {
            if (!this._fusionScene)
                return false;
            return this._fusionScene.removeTile(mirrorId,  false);
        } catch (e) {
            logError(e, 'phantom: DestroyMirror failed');
            return false;
        }
    }

    ListMirrors() {
        try {
            const tiles = this._fusionScene ? this._fusionScene.list() : [];
            return JSON.stringify(tiles);
        } catch (e) {
            logError(e, 'phantom: ListMirrors failed');
            return '[]';
        }
    }

    RunRecipeAsync([recipeJson], invocation) {
        let recipe;
        try {
            recipe = JSON.parse(recipeJson || '{}');
        } catch (e) {
            invocation.return_value(new GLib.Variant('(s)',
                [JSON.stringify({ok: false, error: `bad recipe JSON: ${e.message}`})]));
            return;
        }
        const steps = Array.isArray(recipe.steps) ? recipe.steps : [];
        if (steps.length === 0) {
            invocation.return_value(new GLib.Variant('(s)',
                [JSON.stringify({ok: false, error: 'recipe has no steps'})]));
            return;
        }

        const values = {};
        const results = [];
        const subst = (s) => (s || '').replace(/\{\{(\w+)\}\}/g,
            (_m, k) => (k in values ? values[k] : ''));
        const finish = (ok) => {
            invocation.return_value(new GLib.Variant('(s)',
                [JSON.stringify({ok, steps: results, values})]));
        };
        const runStep = (i) => {
            if (i >= steps.length) {
                finish(results.every(r => r && r.ok !== false));
                return;
            }
            const step = steps[i] || {};
            const op = (step.op || '').toLowerCase();
            const sel = this._joinSel(step.app || '', subst(step.selector || ''));
            const next = (res) => { results.push(Object.assign({op}, res)); runStep(i + 1); };

            if (op === 'read') {

                this._runHelperPromise(['read', sel]).then((json) => {
                    let captured = null;
                    try {
                        const d = JSON.parse(json);
                        captured = (d && (d.text ?? d.value ?? d.name)) ?? null;
                    } catch (e) {}
                    if (step.capture && captured != null)
                        values[step.capture] = String(captured);
                    next({ok: true, captured: step.capture || null, raw: json});
                }).catch((e) => next({ok: false, error: String(e)}));
                return;
            }

            if (op === 'write' || op === 'action') {

                const dec = this._gate(false);
                if (!dec.allow) {
                    if (dec.queued)
                        this._queueForConfirm(`Recipe.${op}(${sel})`, () => {});
                    next({ok: false, gate: dec.reason, queued: !!dec.queued, executed: false});
                    return;
                }
                const argv = op === 'write'
                    ? ['write', sel, subst(step.text || '')]
                    : ['action', sel, String(step.action || 'click')];
                this._runHelperPromise(argv).then((json) => {
                    next({ok: true, gate: dec.reason, raw: json});
                }).catch((e) => next({ok: false, error: String(e)}));
                return;
            }

            next({ok: false, error: `unknown op '${op}'`});
        };
        runStep(0);
    }

    _runHelperPromise(argv, timeoutMs = 4000) {
        return new Promise((resolve, reject) => {
            const full = ['python3', this._helperPath(), ...argv];
            let proc;
            try {
                proc = Gio.Subprocess.new(
                    full,
                    Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
            } catch (e) {
                reject(e);
                return;
            }
            const cancellable = new Gio.Cancellable();
            const timeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, timeoutMs, () => {
                try { proc.force_exit(); } catch (e) {}
                cancellable.cancel();
                return GLib.SOURCE_REMOVE;
            });
            proc.communicate_utf8_async(null, cancellable, (p, res) => {
                GLib.source_remove(timeoutId);
                try {
                    const [, stdout, stderr] = p.communicate_utf8_finish(res);
                    const line = (stdout || '').trim().split('\n').filter(Boolean).pop() || '';
                    if (line) {
                        JSON.parse(line);
                        resolve(line);
                    } else {
                        resolve(JSON.stringify(
                            {ok: false, error: `helper produced no JSON; stderr: ${(stderr || '').slice(0, 200)}`}));
                    }
                } catch (e) {
                    resolve(JSON.stringify({ok: false, error: `helper: ${e.message}`}));
                }
            });
        });
    }
}
