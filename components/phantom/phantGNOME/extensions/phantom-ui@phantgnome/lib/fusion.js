
import GObject from 'gi://GObject';
import Clutter from 'gi://Clutter';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {MonitorConstraint} from 'resource:///org/gnome/shell/ui/layout.js';

import {BTN_LEFT} from './constants.js';

export class FusionTile {

    constructor(service, win, scene, opts) {
        this._service = service;
        this._scene = scene;
        this._win = win;
        this._sourceId = win.get_id();
        this._scale = (opts && opts.scale > 0) ? opts.scale : 0.4;
        this._interactive = !!(opts && opts.interactive);
        this._mirrorId = `mirror-${this._sourceId}-${FusionTile._seq++}`;
        this._destroyId = 0;
        this.clone = null;
        this.inputProxy = null;
        this.container = null;

        const actor = win.get_compositor_private();
        if (!actor)
            throw new Error(`source window ${this._sourceId} has no compositor actor (unrealized)`);
        this._sourceActor = actor;

        this.clone = new Clutter.Clone({
            source: actor,
            reactive: false,
            x: 0, y: 0,
            scale_x: this._scale,
            scale_y: this._scale,
        });

        const fr = win.get_buffer_rect();
        this.container = new St.Widget({
            reactive: false,
            x: (opts && Number.isFinite(opts.x)) ? opts.x : 0,
            y: (opts && Number.isFinite(opts.y)) ? opts.y : 0,
            width:  Math.max(1, Math.round(fr.width  * this._scale)),
            height: Math.max(1, Math.round(fr.height * this._scale)),
        });
        this.container.add_child(this.clone);

        if (this._interactive) {
            this.inputProxy = new St.Widget({
                reactive: true,
                x: 0, y: 0,
                width:  this.container.width,
                height: this.container.height,
                opacity: 0,
            });
            this.container.add_child(this.inputProxy);
            this._capturedId = this.inputProxy.connect(
                'captured-event', (_a, event) => this._onProxyEvent(event));
        }

        this._destroyId = actor.connect('destroy', () => {
            this._destroyId = 0;
            if (this._scene)
                this._scene.removeTile(this._mirrorId,  true);
        });
    }

    get mirrorId()  { return this._mirrorId; }
    get sourceId()  { return this._sourceId; }

    _onProxyEvent(event) {
        try {
            const type = event.type();
            if (type !== Clutter.EventType.BUTTON_PRESS &&
                type !== Clutter.EventType.BUTTON_RELEASE)
                return Clutter.EVENT_PROPAGATE;
            if (!this._service)
                return Clutter.EVENT_PROPAGATE;

            const [px, py] = event.get_coords();
            const [ax, ay] = this.inputProxy.get_transformed_position();
            const lx = px - ax, ly = py - ay;
            const fr = this._win.get_buffer_rect();
            const wx = fr.x + lx / this._scale;
            const wy = fr.y + ly / this._scale;

            if (type === Clutter.EventType.BUTTON_PRESS) {
                const btn = event.get_button() || BTN_LEFT;
                this._service.injectClickAt(wx, wy, btn);
            }
            return Clutter.EVENT_STOP;
        } catch (e) {
            logError(e, 'phantom: fusion InputProxy route failed');
            return Clutter.EVENT_PROPAGATE;
        }
    }

    rendered() {
        try {
            if (!this.clone || !this._sourceActor)
                return false;

            let realized = false, mapped = false;
            try { realized = !!this._sourceActor.realized; } catch (e) {}
            try { mapped = !!this._sourceActor.mapped; } catch (e) {}
            const w = this._sourceActor.get_width  ? this._sourceActor.get_width()  : 0;
            const h = this._sourceActor.get_height ? this._sourceActor.get_height() : 0;
            return (realized || mapped) && w > 0 && h > 0;
        } catch (e) {
            return false;
        }
    }

    info() {
        let sw = 0, sh = 0;
        try { sw = this._sourceActor ? this._sourceActor.get_width()  : 0; } catch (e) {}
        try { sh = this._sourceActor ? this._sourceActor.get_height() : 0; } catch (e) {}
        return {
            mirrorId: this._mirrorId,
            sourceId: this._sourceId,
            title: (() => { try { return this._win.get_title() ?? ''; } catch (e) { return ''; } })(),
            wm_class: (() => { try { return this._win.get_wm_class() ?? ''; } catch (e) { return ''; } })(),
            scale: this._scale,
            interactive: this._interactive,
            rendered: this.rendered(),
            sourceWidth: sw,
            sourceHeight: sh,
            cloneWidth:  this.container ? this.container.width  : 0,
            cloneHeight: this.container ? this.container.height : 0,
            x: this.container ? this.container.x : 0,
            y: this.container ? this.container.y : 0,
        };
    }

    destroy(sourceGone) {
        if (this._destroyId && !sourceGone && this._sourceActor) {
            try { this._sourceActor.disconnect(this._destroyId); } catch (e) {}
        }
        this._destroyId = 0;
        if (this._capturedId && this.inputProxy) {
            try { this.inputProxy.disconnect(this._capturedId); } catch (e) {}
            this._capturedId = 0;
        }

        if (this.container) {
            try { this.container.destroy(); } catch (e) {}
            this.container = null;
        }
        this.clone = null;
        this.inputProxy = null;
        this._sourceActor = null;
        this._win = null;
        this._service = null;
        this._scene = null;
    }
}
FusionTile._seq = 1;

export class FusionScene {
    constructor(service, modeController) {
        this._service = service;
        this._mode = modeController;
        this._tiles = new Map();
        this._group = new St.Widget({
            style_class: 'phantom-fusion-group',
            reactive: false,
        });
        this._group.add_constraint(new MonitorConstraint({primary: true}));

        Main.layoutManager.addChrome(this._group,
            {affectsInputRegion: false, trackFullscreen: false});
    }

    has(mirrorId) { return this._tiles.has(mirrorId); }
    get count()   { return this._tiles.size; }

    addTile(tile) {
        this._group.add_child(tile.container);
        this._tiles.set(tile.mirrorId, tile);

        if (this._mode && typeof this._mode.registerTile === 'function') {
            this._mode.registerTile(tile);

            const p = this._mode.profile;
            if (p && tile.inputProxy)
                tile.inputProxy.reactive =
                    (p.tileRouting === 'SHARED' || p.tileRouting === 'HUMAN_ONLY');
        }
        this._reflow();
        return tile.mirrorId;
    }

    removeTile(mirrorId, sourceGone) {
        const tile = this._tiles.get(mirrorId);
        if (!tile)
            return false;
        this._tiles.delete(mirrorId);

        if (this._mode && Array.isArray(this._mode._registry?.tiles)) {
            this._mode._registry.tiles =
                this._mode._registry.tiles.filter(t => t !== tile);
        }
        tile.destroy(sourceGone);
        this._reflow();
        return true;
    }

    list() {
        return Array.from(this._tiles.values()).map(t => t.info());
    }

    _reflow() {
        const mon = Main.layoutManager.primaryMonitor;
        if (!mon)
            return;
        const margin = 12, gap = 10;
        let x = margin;
        const tiles = Array.from(this._tiles.values());

        let maxH = 0;
        for (const t of tiles)
            maxH = Math.max(maxH, t.container ? t.container.height : 0);
        const baseY = mon.height - margin - maxH;
        for (const t of tiles) {
            if (!t.container)
                continue;
            t.container.x = x;
            t.container.y = baseY + (maxH - t.container.height);
            x += t.container.width + gap;
        }
    }

    destroyAll() {
        for (const id of Array.from(this._tiles.keys())) {
            const tile = this._tiles.get(id);
            this._tiles.delete(id);
            if (this._mode && Array.isArray(this._mode._registry?.tiles)) {
                this._mode._registry.tiles =
                    this._mode._registry.tiles.filter(t => t !== tile);
            }
            tile.destroy( false);
        }
        if (this._group) {
            try { Main.layoutManager.removeChrome(this._group); } catch (e) {}
            try { this._group.destroy(); } catch (e) {}
            this._group = null;
        }
        this._service = null;
        this._mode = null;
    }
}
