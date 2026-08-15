
import GObject from 'gi://GObject';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {MonitorConstraint} from 'resource:///org/gnome/shell/ui/layout.js';
import {ExtensionState} from 'resource:///org/gnome/shell/misc/extensionUtils.js';

const UMBRELLA_UUID = 'phantom-ui@phantgnome';

const SHELL_SCHEMA = 'org.gnome.shell';
const DISABLED_EXTENSIONS_KEY = 'disabled-extensions';

const PHANTOM_BUS_NAME = 'org.gnome.Phantom';
const PHANTOM_OBJECT_PATH = '/org/gnome/Phantom';
const PHANTOM_IFACE = 'org.gnome.Phantom';

const PHANTOM_EXT_SCHEMA = 'org.gnome.shell.extensions.phantom';

const LifeboatAnchor = GObject.registerClass(
class LifeboatAnchor extends St.Button {
    _init(onRecover) {
        super._init({
            style_class: 'phantom-lifeboat',
            reactive: true, can_focus: true, track_hover: true,
            label: '⚓',
            style: 'background-color: rgba(200,40,40,0.85); color:#fff; ' +
                   'border-radius: 0 0 0 8px; padding: 1px 7px; font-size: 12px;',
        });
        this.connect('clicked', () => { if (onRecover) onRecover(); });
    }

    setAlert(on) {

        this.style = on
            ? 'background-color: rgba(230,150,20,0.95); color:#111; ' +
              'border-radius: 0 0 0 8px; padding: 1px 7px; font-size: 12px;'
            : 'background-color: rgba(200,40,40,0.85); color:#fff; ' +
              'border-radius: 0 0 0 8px; padding: 1px 7px; font-size: 12px;';
    }
});

export default class PhantomLifeboatExtension extends Extension {
    enable() {

        try {
            this._anchor = new LifeboatAnchor(() => this._recover());
            this._anchor.add_constraint(new MonitorConstraint({primary: true}));
            this._anchor.set({
                x_align: Clutter.ActorAlign.END,
                y_align: Clutter.ActorAlign.START,
            });
            Main.layoutManager.addTopChrome(this._anchor, {affectsInputRegion: true});
        } catch (e) {
            logError(e, 'phantom-lifeboat: anchor mount failed');
        }

        try {
            this._shellSettings = new Gio.Settings({schema_id: SHELL_SCHEMA});
            this._disabledWatchId = this._shellSettings.connect(
                `changed::${DISABLED_EXTENSIONS_KEY}`, () => this._onDisabledChanged());
        } catch (e) {
            logError(e, 'phantom-lifeboat: disabled-extensions watch failed');
        }

        try {
            if (Main.extensionManager) {
                this._stateChangedId = Main.extensionManager.connect(
                    'extension-state-changed', (_m, ext) => this._onExtState(ext));
            }
        } catch (e) {
            logError(e, 'phantom-lifeboat: extension-state-changed watch failed');
        }

        this._refreshAlert();

        log('phantom-lifeboat: enabled (independent SPOF daemon up; anchor + umbrella watchdog)');
    }

    _umbrellaActive() {
        try {
            const em = Main.extensionManager;
            if (!em)
                return false;
            const ext = em.lookup(UMBRELLA_UUID);
            return !!ext && ext.state === ExtensionState.ACTIVE;
        } catch (e) {
            return false;
        }
    }

    _refreshAlert() {
        if (this._anchor)
            this._anchor.setAlert(!this._umbrellaActive());
    }

    _onExtState(ext) {
        try {
            if (!ext || ext.uuid !== UMBRELLA_UUID)
                return;
            this._refreshAlert();

            const terminal = ext.state === ExtensionState.OUT_OF_DATE ||
                             ext.state === ExtensionState.ERROR;
            if (!terminal)
                return;
            if (this._reenableQueued)
                return;
            this._reenableQueued = true;
            log(`phantom-lifeboat: umbrella in terminal state=${ext.state}; scheduling re-enable`);

            this._reenableId = GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
                this._reenableId = 0;
                this._reenableQueued = false;
                this._tryReenableUmbrella();
                this._refreshAlert();
                return GLib.SOURCE_REMOVE;
            });
        } catch (e) {
            logError(e, 'phantom-lifeboat: _onExtState failed');
        }
    }

    _onDisabledChanged() {
        try {
            const list = this._shellSettings.get_strv(DISABLED_EXTENSIONS_KEY);
            if (list.includes(UMBRELLA_UUID)) {

                const next = list.filter(u => u !== UMBRELLA_UUID);
                this._shellSettings.set_strv(DISABLED_EXTENSIONS_KEY, next);
                log('phantom-lifeboat: stripped phantom-ui from disabled-extensions');
            }
            this._refreshAlert();
        } catch (e) {
            logError(e, 'phantom-lifeboat: _onDisabledChanged failed');
        }
    }

    _tryReenableUmbrella() {

        try {
            const list = this._shellSettings.get_strv(DISABLED_EXTENSIONS_KEY);
            if (list.includes(UMBRELLA_UUID)) {
                this._shellSettings.set_strv(
                    DISABLED_EXTENSIONS_KEY, list.filter(u => u !== UMBRELLA_UUID));
            }
        } catch (e) {
            logError(e, 'phantom-lifeboat: un-disable umbrella failed');
        }

        try {
            if (Main.extensionManager)
                Main.extensionManager.enableExtension(UMBRELLA_UUID);
        } catch (e) {
            logError(e, 'phantom-lifeboat: enableExtension(umbrella) failed');
        }
    }

    _recover() {
        log('phantom-lifeboat: recover invoked');
        this._tryReenableUmbrella();
        this._applySafeStage();
        this._refreshAlert();
    }

    _applySafeStage() {

        try {
            Gio.DBus.session.call(
                PHANTOM_BUS_NAME, PHANTOM_OBJECT_PATH, PHANTOM_IFACE, 'SetStage',
                new GLib.Variant('(s)', ['B']), null,
                Gio.DBusCallFlags.NONE, 500, null,
                (conn, res) => {
                    try {
                        conn.call_finish(res);
                        log('phantom-lifeboat: applied safe stage B via D-Bus');
                    } catch (e) {

                        this._writeStageMirror('B');
                    }
                });
        } catch (e) {
            this._writeStageMirror('B');
        }
    }

    _writeStageMirror(stage) {
        try {
            const src = Gio.SettingsSchemaSource.get_default();
            if (src && src.lookup(PHANTOM_EXT_SCHEMA, true)) {
                const s = new Gio.Settings({schema_id: PHANTOM_EXT_SCHEMA});
                if (s.get_string('stage') !== stage)
                    s.set_string('stage', stage);
                log(`phantom-lifeboat: wrote stage mirror -> ${stage}`);
            }
        } catch (e) {
            logError(e, 'phantom-lifeboat: writeStageMirror failed');
        }
    }

    disable() {
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
                try { this._shellSettings.disconnect(this._disabledWatchId); } catch (e) {}
                this._disabledWatchId = 0;
            }
            this._shellSettings = null;
        }
        if (this._anchor) {
            Main.layoutManager.removeChrome(this._anchor);
            this._anchor.destroy();
            this._anchor = null;
        }
        log('phantom-lifeboat: disabled (fully reversed)');
    }
}
