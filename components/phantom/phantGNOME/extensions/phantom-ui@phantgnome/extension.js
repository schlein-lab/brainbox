
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import St from 'gi://St';
import Shell from 'gi://Shell';
import Meta from 'gi://Meta';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {MonitorConstraint} from 'resource:///org/gnome/shell/ui/layout.js';
import {ExtensionState} from 'resource:///org/gnome/shell/misc/extensionUtils.js';

import {
    UUID,
    SHELL_SCHEMA,
    DISABLED_EXTENSIONS_KEY,
    VERSION_VALIDATION_KEY,
    VALID_STAGES,
} from './lib/constants.js';
import {ModeController} from './lib/modeController.js';
import {FusionScene} from './lib/fusion.js';
import {PhantomService} from './lib/phantomService.js';
import {WindowTreeWatcher} from './lib/windowTreeWatcher.js';
import {
    PhantomHud,
    PhantomPanelIndicator,
    SummonButton,
    BootChooser,
    Lifeboat,
} from './lib/ui.js';

export default class PhantomExtension extends Extension {
    enable() {

        this._installSpofGuards();

        try {
            this._lifeboat = new Lifeboat(() => this._recover());
            this._lifeboat.add_constraint(new MonitorConstraint({primary: true}));
            this._lifeboat.set({x_align: Clutter.ActorAlign.END, y_align: Clutter.ActorAlign.START});
            Main.layoutManager.addTopChrome(this._lifeboat, {affectsInputRegion: true});
        } catch (e) {
            logError(e, 'phantom-ui: lifeboat mount failed');
        }

        try {
            this._enableBody();
            this._enabledOk = true;
            log('phantom-ui: Stage-2 enabled (HUD+slider, ModeController, org.gnome.Phantom)');
        } catch (e) {
            this._enabledOk = false;
            logError(e, 'phantom-ui: enable() body FAILED — lifeboat is up, desktop recoverable');
        }
    }

    _enableBody() {
        const settings = this.getSettings();
        this._settings = settings;

        this._service = new PhantomService(this);

        this._modeController = new ModeController(this._service);
        this._service.setModeController(this._modeController);
        this._service.export();

        this._modeController.enableWindowTracking();

        this._windowWatcher = new WindowTreeWatcher(this._service);
        this._windowWatcher.enable();

        this._modeChangedCb = this._modeController.connect(
            (stage, profile) => this._onModeChanged(stage, profile));

        this._hud = new PhantomHud(settings, (s) => this._service.SetStage(s));
        this._hud.add_constraint(new MonitorConstraint({primary: true}));
        this._hud.set({x_align: Clutter.ActorAlign.START, y_align: Clutter.ActorAlign.START});
        Main.layoutManager.addTopChrome(this._hud, {affectsInputRegion: true});
        this._modeController.registerHud(this._hud);

        try {
            this._panelIndicator = new PhantomPanelIndicator(
                settings, (s) => this._service.SetStage(s));
            Main.panel.addToStatusArea('phantom-mode', this._panelIndicator, 0, 'right');
        } catch (e) {
            logError(e, 'phantom-ui: panel indicator mount failed (non-fatal)');
        }

        try {
            this._summonButton = new SummonButton(() => {
                if (this._service) this._service.SummonLlm();
            });
            Main.panel.addToStatusArea('phantom-summon', this._summonButton, 0, 'right');
        } catch (e) {
            logError(e, 'phantom-ui: summon button mount failed (non-fatal)');
        }

        try {
            this._summonKeyId = Main.wm.addKeybinding(
                'summon-llm', settings, Meta.KeyBindingFlags.NONE,
                Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW,
                () => { if (this._service) this._service.SummonLlm(); });
        } catch (e) {
            logError(e, 'phantom-ui: summon keybinding failed (non-fatal)');
        }

        this._overlay = new St.Widget({
            style_class: 'phantom-overlay',
            reactive: false,
            opacity: 0,
        });
        this._overlay.add_constraint(new MonitorConstraint({primary: true}));
        Main.layoutManager.addChrome(this._overlay, {affectsInputRegion: false, trackFullscreen: false});
        this._modeController.registerOverlay(this._overlay);

        this._fusionScene = new FusionScene(this._service, this._modeController);
        this._service.setFusionScene(this._fusionScene);

        this._modeController.setLlmMatch(settings.get_string('llm-window-match'));
        this._llmMatchWatchId = settings.connect('changed::llm-window-match', () => {
            if (this._modeController)
                this._modeController.setLlmMatch(this._settings.get_string('llm-window-match'));
        });

        let initial = settings.get_string('default-stage');
        if (!VALID_STAGES.includes(initial))
            initial = 'B';
        this._service.SetStage(initial);

        this._chromeSettleCount = 0;
        this._chromeSettleId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1200, () => {
            this._chromeSettleCount++;
            try {
                if (this._modeController && this._modeController.level)
                    this._modeController.applyProfile(this._modeController.level);
            } catch (e) {}
            if (this._chromeSettleCount >= 6) {
                this._chromeSettleId = 0;
                return GLib.SOURCE_REMOVE;
            }
            return GLib.SOURCE_CONTINUE;
        });

        if (initial !== 'A')
            this._showBootChooser(initial);
    }

    _showBootChooser(defaultStage) {
        try {
            this._chooser = new BootChooser(defaultStage, (s) => {
                this._service.SetStage(s);
                this._dismissChooser();
            });
            this._chooser.add_constraint(new MonitorConstraint({primary: true}));
            this._chooser.set({x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER});
            Main.layoutManager.addTopChrome(this._chooser, {affectsInputRegion: true});

            this._chooserTimer = GLib.timeout_add_seconds(
                GLib.PRIORITY_DEFAULT, 10, () => {
                    this._chooserTimer = 0;
                    this._dismissChooser();
                    return GLib.SOURCE_REMOVE;
                });
        } catch (e) {
            logError(e, 'phantom-ui: boot chooser failed (non-fatal)');
        }
    }

    _dismissChooser() {
        if (this._chooserTimer) {
            GLib.source_remove(this._chooserTimer);
            this._chooserTimer = 0;
        }
        if (this._chooser) {
            Main.layoutManager.removeChrome(this._chooser);
            this._chooser.destroy();
            this._chooser = null;
        }
    }

    _onModeChanged(stage, profile) {
        const active = !!(profile && profile.phantomActive !== false);

        try {
            if (active) {
                if (this._windowWatcher) this._windowWatcher.enable();
                if (this._modeController) this._modeController.enableWindowTracking();
            } else {
                if (this._windowWatcher) this._windowWatcher.disable();
                if (this._modeController) this._modeController.disableWindowTracking();
            }
        } catch (e) { logError(e, 'phantom-ui: sensing toggle failed'); }

        if (this._summonButton)
            this._summonButton.visible = !!(active && profile && profile.llmHidden);

        if (active)
            this._ensureLlmRunning();
    }

    _ensureLlmRunning() {
        let found = false;
        for (const actor of global.get_window_actors()) {
            const w = actor.meta_window;
            if (w && this._modeController && this._modeController._isLlmWindow(w)) {
                found = true; break;
            }
        }
        if (found) return;
        const now = GLib.get_monotonic_time();
        if (this._lastLlmSpawn && (now - this._lastLlmSpawn) < 4000000)
            return;
        this._lastLlmSpawn = now;
        try {
            const launcher = GLib.get_home_dir() + '/phantGNOME/phantom-llm-window.sh';
            GLib.spawn_async(null, ['/usr/bin/env', 'bash', launcher], null,
                GLib.SpawnFlags.SEARCH_PATH, null);
            log('phantom-ui: (re)launched the LLM seat window');
        } catch (e) { logError(e, 'phantom-ui: LLM relaunch failed'); }
    }

    _installSpofGuards() {
        try {
            this._shellSettings = new Gio.Settings({schema_id: SHELL_SCHEMA});

            this._priorVersionValidation =
                this._shellSettings.get_boolean(VERSION_VALIDATION_KEY);
            if (!this._priorVersionValidation)
                this._shellSettings.set_boolean(VERSION_VALIDATION_KEY, true);

            this._disabledWatchId = this._shellSettings.connect(
                `changed::${DISABLED_EXTENSIONS_KEY}`, () => this._guardDisabled());
            this._guardDisabled();

            if (Main.extensionManager) {
                this._stateChangedId = Main.extensionManager.connect(
                    'extension-state-changed', (_m, ext) => this._onExtStateChanged(ext));
            }
        } catch (e) {
            logError(e, 'phantom-ui: SPOF guard install failed (non-fatal)');
        }
    }

    _onExtStateChanged(ext) {

        try {
            if (!ext || ext.uuid !== UUID)
                return;
            const terminal = ext.state === ExtensionState.OUT_OF_DATE ||
                             ext.state === ExtensionState.ERROR;
            if (!terminal)
                return;
            if (this._reenableQueued)
                return;
            this._reenableQueued = true;
            log(`phantom-ui: extension-state-changed watchdog saw terminal state=${ext.state}; scheduling re-enable`);

            this._reenableId = GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
                this._reenableId = 0;
                this._reenableQueued = false;
                try {
                    this._guardDisabled();
                    if (Main.extensionManager)
                        Main.extensionManager.enableExtension(UUID);
                } catch (e) {
                    logError(e, 'phantom-ui: deferred state-changed re-enable failed');
                }
                return GLib.SOURCE_REMOVE;
            });
        } catch (e) {
            logError(e, 'phantom-ui: _onExtStateChanged failed');
        }
    }

    _guardDisabled() {
        try {
            const list = this._shellSettings.get_strv(DISABLED_EXTENSIONS_KEY);
            if (list.includes(UUID)) {
                const next = list.filter(u => u !== UUID);
                this._shellSettings.set_strv(DISABLED_EXTENSIONS_KEY, next);
                log('phantom-ui: watchdog re-enabled phantom-ui (was in disabled-extensions)');
            }
        } catch (e) {
            logError(e, 'phantom-ui: disabled-extensions watchdog failed');
        }
    }

    _recover() {
        try {
            this._guardDisabled();
            if (this._service && this._modeController)
                this._service.SetStage('B');
            else if (this._settings)
                this._settings.set_string('stage', 'B');
            log('phantom-ui: lifeboat recover -> stage B (normal desktop)');
        } catch (e) {
            logError(e, 'phantom-ui: lifeboat recover failed');
        }
    }

    disable() {

        this._dismissChooser();

        if (this._windowWatcher) {
            this._windowWatcher.destroy();
            this._windowWatcher = null;
        }

        if (this._fusionScene) {
            this._fusionScene.destroyAll();
            this._fusionScene = null;
        }

        if (this._service) {
            this._service.unexport();
            this._service = null;
        }

        if (this._modeController) {
            this._modeController.destroy();
            this._modeController = null;
        }

        if (this._overlay) {
            Main.layoutManager.removeChrome(this._overlay);
            this._overlay.destroy();
            this._overlay = null;
        }

        if (this._hud) {
            Main.layoutManager.removeChrome(this._hud);
            this._hud.destroy();
            this._hud = null;
        }

        if (this._panelIndicator) {
            try { this._panelIndicator.destroy(); } catch (e) {}
            this._panelIndicator = null;
        }

        if (this._summonButton) {
            try { this._summonButton.destroy(); } catch (e) {}
            this._summonButton = null;
        }

        try { Main.wm.removeKeybinding('summon-llm'); } catch (e) {}
        this._summonKeyId = null;

        if (this._chromeSettleId) {
            GLib.source_remove(this._chromeSettleId);
            this._chromeSettleId = 0;
        }

        if (this._lifeboat) {
            Main.layoutManager.removeChrome(this._lifeboat);
            this._lifeboat.destroy();
            this._lifeboat = null;
        }

        if (this._reenableId) {
            GLib.source_remove(this._reenableId);
            this._reenableId = 0;
        }
        this._reenableQueued = false;
        if (this._stateChangedId && Main.extensionManager) {
            try { Main.extensionManager.disconnect(this._stateChangedId); } catch (e) {}
            this._stateChangedId = 0;
        }
        if (this._shellSettings) {
            if (this._disabledWatchId) {
                this._shellSettings.disconnect(this._disabledWatchId);
                this._disabledWatchId = 0;
            }
            if (this._priorVersionValidation === false) {

                this._shellSettings.set_boolean(VERSION_VALIDATION_KEY, false);
            }
            this._shellSettings = null;
        }

        if (this._llmMatchWatchId && this._settings) {
            try { this._settings.disconnect(this._llmMatchWatchId); } catch (e) {}
        }
        this._llmMatchWatchId = 0;

        this._settings = null;
        this._enabledOk = false;
        log('phantom-ui: disabled (Stage-2 fully reversed)');
    }
}
