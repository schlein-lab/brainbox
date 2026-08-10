
import { Reality, subscribe } from './contract.js';

export class Connection {
  constructor({ baseUrl, principal = null, token = null, wsImpl = null, fetchImpl = null } = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.principal = principal;
    this.token = token;
    this.WS = wsImpl || globalThis.WebSocket;
    this.fetch = fetchImpl || globalThis.fetch?.bind(globalThis);
    this.reality = new Reality();
    this.ws = null;
    this._handlers = new Set();
    this._statusHandlers = new Set();
    this._reconnect = true;
    this._backoff = 500;
  }

  onUpdate(fn) { this._handlers.add(fn); return () => this._handlers.delete(fn); }
  onStatus(fn) { this._statusHandlers.add(fn); return () => this._statusHandlers.delete(fn); }
  _emitStatus(s) { for (const h of this._statusHandlers) try { h(s); } catch {} }
  _emit(u) { for (const h of this._handlers) try { h(u); } catch {} }

  _authHeaders() {
    return this.token ? { Authorization: `Bearer ${this.token}` } : {};
  }

  connect() {
    if (!this.principal) throw new Error('no principal; pair/resolve first');
    this._reconnect = true;
    this._open();
  }

  async _mintStreamTicket(topics) {
    if (!this.token) return null;
    const r = await this.fetch(`${this.baseUrl}/api/stream-ticket`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...this._authHeaders() },
      body: JSON.stringify({ topics }),
    });
    const j = await r.json();
    return j.ticket || null;
  }

  _wsUrl(ticket = null) {
    const u = new URL(this.baseUrl);
    u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
    const after = this.reality.lastEventId || '';
    return `${u.origin}/ws/events?topics=${encodeURIComponent('user/' + this.principal)}` +
      (after ? `&after_id=${after}` : '') +
      (ticket ? `&ticket=${encodeURIComponent(ticket)}` : '');
  }

  async _open() {
    this._emitStatus('connecting');
    let ticket = null;
    try {
      ticket = await this._mintStreamTicket(['user/' + this.principal]);
    } catch (e) { return this._scheduleReconnect(); }
    let ws;
    try { ws = new this.WS(this._wsUrl(ticket)); } catch (e) { return this._scheduleReconnect(); }
    this.ws = ws;
    ws.onopen = () => { this._backoff = 500; this._emitStatus('open'); };
    ws.onmessage = (ev) => {
      let frame; try { frame = JSON.parse(ev.data); } catch { return; }
      const u = this.reality.apply(frame);
      if (u && u.revoked) { this._reconnect = false; this._emitStatus('revoked'); }
      if (u) this._emit(u);
    };
    ws.onclose = () => { this._emitStatus('closed'); this._scheduleReconnect(); };
    ws.onerror = () => { try { ws.close(); } catch {} };
  }

  _scheduleReconnect() {
    if (!this._reconnect) return;
    const d = this._backoff;
    this._backoff = Math.min(this._backoff * 2, 15000);
    setTimeout(() => this._reconnect && this._open(), d);
  }

  close() { this._reconnect = false; try { this.ws && this.ws.close(); } catch {} }

  async verb(req) {

    const { principal, uid, _peer_uid, ...clean } = req;
    const r = await this.fetch(`${this.baseUrl}/api/verb`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...this._authHeaders() },
      body: JSON.stringify(clean),
    });
    return r.json();
  }

  async cvm(jobId) {
    const r = await this.fetch(`${this.baseUrl}/api/cvm?id=${jobId}`, { headers: this._authHeaders() });
    return r.json();
  }

  async replayDelta(afterId) {
    const topics = encodeURIComponent('user/' + this.principal);
    const r = await this.fetch(`${this.baseUrl}/api/replay?topics=${topics}&after=${afterId}`,
      { headers: this._authHeaders() });
    return r.json();
  }
}
