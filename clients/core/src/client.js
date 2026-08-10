
import * as C from './contract.js';
import { Keystore } from './keystore.js';
import { Connection } from './connection.js';
import { pairViaPortal, ensureDeviceKey } from './pairing.js';
import { MediaSession, recordVoiceMessage, captureCamera, captureScreen, snapPhoto, watchMjpegUrl }
  from './media.js';
import { fileToManifest, utteranceToVerb } from './intake.js';

export class ConnectClient {
  constructor({ boxLabel = 'home', baseUrl, keystore, principal = null, token = null,
    wsImpl = null, fetchImpl = null } = {}) {
    this.boxLabel = boxLabel;
    this.baseUrl = baseUrl;
    this.ks = keystore || new Keystore();
    this.principal = principal;
    this.token = token;
    this._wsImpl = wsImpl; this._fetchImpl = fetchImpl;
    this.conn = null;
    this._media = new Map();
  }

  get reality() { return this.conn?.reality; }

  async pair({ code, totpCode, label = null }) {
    const dk = await ensureDeviceKey();
    const httpPost = async (url, body) => {
      const r = await (this._fetchImpl || fetch)(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      return r.json();
    };
    const res = await pairViaPortal({ baseUrl: this.baseUrl, code, totpCode,
      devicePubkey: dk?.pubkeyHex || null, label, httpPost });
    this.principal = res.principal; this.token = res.token;
    await this.ks.saveAlliance(this.boxLabel,
      { principal: res.principal, token: res.token, did: res.did, caps: res.caps });
    return res;
  }

  async isPaired() { return this.ks.isPaired(this.boxLabel); }

  async connect() {
    if (!this.principal || !this.token) {
      const al = await this.ks.alliance(this.boxLabel);
      if (al) { this.principal = this.principal || al.principal; this.token = this.token || al.token; }
    }
    this.conn = new Connection({ baseUrl: this.baseUrl, principal: this.principal, token: this.token,
      wsImpl: this._wsImpl, fetchImpl: this._fetchImpl });

    this.conn.onUpdate((u) => {
      if (u && u.mediaSignal) {
        const sid = u.mediaSignal.session_id;
        const m = this._media.get(sid);
        if (m) m.handleSignal(u.mediaSignal);
      }
    });
    this.conn.connect();
    return this.conn;
  }

  onUpdate(cb) { return this.conn.onUpdate((u) => { if (u && u.id != null) cb(u); }); }
  onStatus(cb) { return this.conn.onStatus(cb); }
  disconnect() { this.conn && this.conn.close(); for (const m of this._media.values()) m.close(); }

  pendingApprovals() { return this.reality.pendingApprovals(); }

  sendText(text) {
    return this.conn.verb(C.submitText(text, { replyTopic: C.userTopic(this.principal) }));
  }

  async sendVoice(audioBlobOrText) {
    if (typeof audioBlobOrText === 'string') {
      return this.conn.verb(C.submitText(audioBlobOrText, { replyTopic: C.userTopic(this.principal) }));
    }
    const manifest = await fileToManifest(audioBlobOrText);
    return this.conn.verb(C.submitAttach('(voice message)', { attachments: [manifest],
      taskType: 'intake.voice', replyTopic: C.userTopic(this.principal) }));
  }
  async attach(text, { files = [], paths = [] } = {}) {
    const attachments = [];
    for (const f of files) attachments.push(await fileToManifest(f));
    return this.conn.verb(C.submitAttach(text, { attachments, paths,
      replyTopic: C.userTopic(this.principal) }));
  }

  approve(cvm) { return this.conn.verb(C.approve(this._nonce(cvm))); }
  reject(cvm) { return this.conn.verb(C.reject(this._nonce(cvm))); }
  revise(cvm, feedback) { return this.conn.verb(C.revise(cvm.id, feedback)); }
  decideSpoken(cvm, utterance) {
    const d = utteranceToVerb(utterance);
    if (!d) return Promise.resolve({ ok: false, error: `cannot parse: ${utterance}` });
    if (d.verb === 'approve') return this.approve(cvm);
    if (d.verb === 'deny') return this.reject(cvm);
    return this.revise(cvm, d.feedback || '');
  }
  _nonce(cvm) {
    const n = C.nonceOf(cvm);
    if (!n) throw new Error('no approval nonce on that CVM');
    return n;
  }

  async _openMedia(kind, localStream, { iceServers = [] } = {}) {
    const resp = await this.conn.verb(C.submitMedia(kind, { replyTopic: C.userTopic(this.principal) }));
    const sessionId = resp.session_id || resp.id || `s-${Date.now()}`;
    const signaler = {
      send: (frame) => this.conn.verb({ verb: 'steer', id: resp.id || sessionId,
        input: JSON.stringify(frame) }),
      onSignal: () => {},
    };
    const m = new MediaSession({ sessionId, signaler, iceServers: resp.ice_servers || iceServers });
    if (localStream) m.addLocalStream(localStream);
    this._media.set(sessionId, m);
    await m.start();
    return m;
  }
  async startCall({ video = false } = {}) {
    const stream = await captureCamera({ video, audio: true });
    return this._openMedia(video ? 'video-call' : 'voice-call', stream);
  }
  async shareScreen() {
    const stream = await captureScreen({ audio: false });
    return this._openMedia('screen-share', stream);
  }
  async sendVoiceMessage({ maxMs = 60000 } = {}) {
    const rec = await recordVoiceMessage({ maxMs });
    return { stop: rec.stop, finish: async () => this.sendVoice(await rec.blob) };
  }
  async snapAndSend(text = '(photo)') {
    const stream = await captureCamera({ video: true, audio: false });
    const blob = await snapPhoto(stream);
    stream.getTracks().forEach(t => t.stop());
    return this.attach(text, { files: [new File([blob], 'photo.jpg', { type: 'image/jpeg' })] });
  }

  async watchUrl() {
    const ticket = this.conn ? await this.conn._mintStreamTicket(['screen']) : null;
    return watchMjpegUrl(this.baseUrl, { ticket });
  }
}
