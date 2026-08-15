
export const MEDIA_KINDS = Object.freeze([
  'voice-message', 'voice-call', 'video-call', 'screen-share', 'photo', 'video',
]);

export async function captureMic() {
  return navigator.mediaDevices.getUserMedia({ audio: true });
}
export async function captureCamera({ video = true, audio = true } = {}) {
  return navigator.mediaDevices.getUserMedia({ video, audio });
}
export async function captureScreen({ audio = false } = {}) {
  return navigator.mediaDevices.getDisplayMedia({ video: true, audio });
}

export async function recordVoiceMessage({ maxMs = 60000 } = {}) {
  const stream = await captureMic();
  const rec = new MediaRecorder(stream, { mimeType: 'audio/webm' });
  const chunks = [];
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  const done = new Promise((res) => { rec.onstop = () => res(new Blob(chunks, { type: 'audio/webm' })); });
  rec.start();
  const stop = () => { try { rec.stop(); } catch {} stream.getTracks().forEach(t => t.stop()); };
  const timer = setTimeout(stop, maxMs);
  return { stop: () => { clearTimeout(timer); stop(); }, blob: done };
}

export async function snapPhoto(stream) {
  const track = stream.getVideoTracks()[0];
  if ('ImageCapture' in globalThis) {
    const cap = new ImageCapture(track);
    return cap.takePhoto();
  }

  const v = document.createElement('video');
  v.srcObject = stream; await v.play();
  const c = document.createElement('canvas');
  c.width = v.videoWidth; c.height = v.videoHeight;
  c.getContext('2d').drawImage(v, 0, 0);
  return new Promise((res) => c.toBlob(res, 'image/jpeg', 0.9));
}

export class MediaSession {
  constructor({ sessionId, signaler, iceServers = [] }) {
    this.sessionId = sessionId;
    this.signaler = signaler;
    this.pc = new RTCPeerConnection({ iceServers });
    this.remoteStream = new MediaStream();
    this._remoteHandlers = new Set();
    this.pc.ontrack = (e) => {
      e.streams[0]?.getTracks().forEach(t => this.remoteStream.addTrack(t));
      for (const h of this._remoteHandlers) try { h(this.remoteStream); } catch {}
    };
    this.pc.onicecandidate = (e) => {
      if (e.candidate) this.signaler.send({ type: 'media-ice', session_id: sessionId,
        candidate: e.candidate.toJSON() });
    };
  }

  onRemoteStream(fn) { this._remoteHandlers.add(fn); return () => this._remoteHandlers.delete(fn); }

  addLocalStream(stream) { stream.getTracks().forEach(t => this.pc.addTrack(t, stream)); }

  async start() {
    const offer = await this.pc.createOffer({ offerToReceiveAudio: true, offerToReceiveVideo: true });
    await this.pc.setLocalDescription(offer);
    this.signaler.send({ type: 'media-offer', session_id: this.sessionId, sdp: offer.sdp });
  }

  async handleSignal(frame) {
    if (frame.session_id && frame.session_id !== this.sessionId) return;
    if (frame.type === 'media-answer') {
      await this.pc.setRemoteDescription({ type: 'answer', sdp: frame.sdp });
    } else if (frame.type === 'media-offer') {
      await this.pc.setRemoteDescription({ type: 'offer', sdp: frame.sdp });
      const ans = await this.pc.createAnswer();
      await this.pc.setLocalDescription(ans);
      this.signaler.send({ type: 'media-answer', session_id: this.sessionId, sdp: ans.sdp });
    } else if (frame.type === 'media-ice' && frame.candidate) {
      try { await this.pc.addIceCandidate(frame.candidate); } catch {}
    } else if (frame.type === 'media-end') {
      this.close();
    }
  }

  close() {
    try { this.signaler.send({ type: 'media-end', session_id: this.sessionId }); } catch {}
    try { this.pc.getSenders().forEach(s => s.track && s.track.stop()); } catch {}
    try { this.pc.close(); } catch {}
  }
}

export function watchMjpegUrl(baseUrl, { ticket = null } = {}) {
  const u = new URL(baseUrl);
  u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${u.origin}/ws/screen` + (ticket ? `?ticket=${encodeURIComponent(ticket)}` : '');
}
