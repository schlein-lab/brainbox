
import GObject from 'gi://GObject';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import St from 'gi://St';

import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {VALID_STAGES} from './constants.js';

export const PhantomHud = GObject.registerClass(
class PhantomHud extends St.BoxLayout {
    _init(settings, onPick) {
        super._init({
            style_class: 'phantom-hud',
            reactive: false,
            x_expand: false,
            y_expand: false,
            vertical: false,
            style: 'background-color: rgba(10,10,18,0.80); ' +
                   'color: #c8c8e0; padding: 3px 12px; ' +
                   'margin-top: 34px; margin-left: 8px; ' +
                   'border-radius: 9px; spacing: 10px; ' +
                   'font-family: monospace; font-size: 11px;',
        });

        this._settings = settings;
        this._onPick = onPick;

        this._stageLabel = new St.Label({y_align: Clutter.ActorAlign.START});
        this.add_child(this._stageLabel);

        this._buttons = {};
        const slider = new St.BoxLayout({style: 'spacing: 4px;',
            y_align: Clutter.ActorAlign.START});
        for (const s of VALID_STAGES) {
            const btn = new St.Button({
                label: s,
                reactive: true,
                can_focus: true,
                track_hover: true,
                style_class: 'phantom-stage-btn',
                style: 'border: 1px solid #888; border-radius: 4px; ' +
                       'padding: 0 7px; color: #ddd;',
            });
            btn.connect('clicked', () => { if (this._onPick) this._onPick(s); });
            this._buttons[s] = btn;
            slider.add_child(btn);
        }
        this.add_child(slider);

        this._clock = new St.Label({y_align: Clutter.ActorAlign.START});
        this.add_child(this._clock);

        this._stageChangedId = this._settings.connect(
            'changed::stage', () => this._syncStage());
        this._syncStage();

        this._clockTimer = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT, 1, () => {
                this._syncClock();
                return GLib.SOURCE_CONTINUE;
            });
        this._syncClock();
    }

    _syncClock() {
        this._clock.set_text(GLib.DateTime.new_now_local().format('%H:%M:%S'));
    }

    _syncStage() {
        const stage = this._settings.get_string('stage');
        this._stageLabel.set_text('phantom');
        for (const s of VALID_STAGES) {
            const active = s === stage;
            this._buttons[s].style =
                'border: 1px solid ' + (active ? '#7fd' : '#888') + '; ' +
                'border-radius: 4px; padding: 0 7px; ' +
                'background-color: ' + (active ? 'rgba(80,220,170,0.25)' : 'transparent') + '; ' +
                'color: ' + (active ? '#aff' : '#ddd') + ';';
        }
    }

    destroy() {
        if (this._clockTimer) {
            GLib.source_remove(this._clockTimer);
            this._clockTimer = 0;
        }
        if (this._stageChangedId) {
            this._settings.disconnect(this._stageChangedId);
            this._stageChangedId = 0;
        }
        super.destroy();
    }
});

const STAGE_MENU_LABELS = {
    A: 'A · autonom',
    B: 'B · semiautonom',
    C: 'C · on-demand',
};
export const PhantomPanelIndicator = GObject.registerClass(
class PhantomPanelIndicator extends PanelMenu.Button {
    _init(settings, onPick) {
        super._init(0.5, 'phantom', false);
        this._settings = settings;
        this._onPick = onPick;

        const box = new St.BoxLayout({style_class: 'panel-status-indicators-box'});
        this._glyph = new St.Label({
            y_align: Clutter.ActorAlign.CENTER,
            text: '◉',
            style: 'font-weight: bold; padding-right: 5px;',
        });
        this._letter = new St.Label({
            y_align: Clutter.ActorAlign.CENTER,
            style: 'font-weight: bold;',
            text: '—',
        });
        box.add_child(this._glyph);
        box.add_child(this._letter);
        this.add_child(box);

        this._items = {};
        for (const s of VALID_STAGES) {
            const item = new PopupMenu.PopupMenuItem(STAGE_MENU_LABELS[s] ?? s);
            item.connect('activate', () => { if (this._onPick) this._onPick(s); });
            this.menu.addMenuItem(item);
            this._items[s] = item;
        }

        this._stageChangedId = this._settings.connect(
            'changed::stage', () => this._sync());
        this._sync();
    }

    _sync() {
        const stage = this._settings.get_string('stage');
        this._letter.set_text(VALID_STAGES.includes(stage) ? stage : '—');
        for (const s of VALID_STAGES) {
            if (this._items[s]) {
                this._items[s].setOrnament(s === stage
                    ? PopupMenu.Ornament.DOT : PopupMenu.Ornament.NONE);
            }
        }
    }

    destroy() {
        if (this._stageChangedId) {
            try { this._settings.disconnect(this._stageChangedId); } catch (e) {}
            this._stageChangedId = 0;
        }
        super.destroy();
    }
});

export const SummonButton = GObject.registerClass(
class SummonButton extends PanelMenu.Button {
    _init(onToggle) {
        super._init(0.5, 'phantom-summon', true);
        this._onToggle = onToggle;

        const box = new St.BoxLayout({style_class: 'panel-status-indicators-box'});
        this._glyph = new St.Label({
            y_align: Clutter.ActorAlign.CENTER,
            text: '✦',
            style: 'font-weight: bold; padding-right: 5px;',
        });
        this._label = new St.Label({
            y_align: Clutter.ActorAlign.CENTER,
            text: 'phantom',
            style: 'font-weight: bold;',
        });
        box.add_child(this._glyph);
        box.add_child(this._label);
        this.add_child(box);

        this.set({style: 'background-color: rgba(124,58,237,0.55); ' +
                          'border-radius: 7px; color: #f3eaff; margin: 3px 4px;'});
    }

    vfunc_event(event) {
        const t = event.type();
        if (t === Clutter.EventType.BUTTON_PRESS || t === Clutter.EventType.TOUCH_BEGIN) {
            if (this._onToggle) this._onToggle();
            return Clutter.EVENT_STOP;
        }
        return Clutter.EVENT_PROPAGATE;
    }
});

export const BootChooser = GObject.registerClass(
class BootChooser extends St.BoxLayout {
    _init(defaultStage, onPick) {
        super._init({
            style_class: 'phantom-boot-chooser',
            reactive: true,
            vertical: true,
            style: 'background-color: rgba(10,10,16,0.82); color:#eee; ' +
                   'border: 1px solid #5af; border-radius: 12px; ' +
                   'padding: 18px 26px; spacing: 10px; font-family: monospace;',
        });
        this._onPick = onPick;

        const title = new St.Label({
            text: 'phantom — choose today’s stage',
            style: 'font-size: 14px; font-weight: bold;',
            x_align: Clutter.ActorAlign.CENTER,
        });
        this.add_child(title);

        const row = new St.BoxLayout({style: 'spacing: 12px;',
            x_align: Clutter.ActorAlign.CENTER});
        const labels = {A: 'A · full-auto', B: 'B · semi-auto', C: 'C · manual'};
        for (const s of VALID_STAGES) {
            const btn = new St.Button({
                label: labels[s],
                reactive: true, can_focus: true, track_hover: true,
                style: 'border:1px solid ' + (s === defaultStage ? '#7fd' : '#779') +
                       '; border-radius:8px; padding:6px 14px; color:#dff;',
            });
            btn.connect('clicked', () => { if (this._onPick) this._onPick(s); });
            row.add_child(btn);
        }
        this.add_child(row);
    }
});

export const Lifeboat = GObject.registerClass(
class Lifeboat extends St.Button {
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
});
