
export interface BrainarbeitOptions {
  baseUrl: string;
  deviceDid: string;
  durableToken: string;
  totpSecret?: string;
  totpCode?: string;
}

export interface Attachment { filename: string; bytes: Uint8Array; }

export interface SubmitOptions {
  params?: Record<string, unknown>;
  attachments?: Attachment[];
  pathRefs?: string[];
  replyTo?: string;
  needsConfirmation?: boolean;
  tag?: string;
  [extra: string]: unknown;
}

type Decision = "approve" | "reject" | "revise" | "deny";

export class Brainarbeit {
  constructor(private readonly o: BrainarbeitOptions) {}

  private async totp(): Promise<string | null> {
    if (this.o.totpCode) return this.o.totpCode;
    if (!this.o.totpSecret) return null;
    const key = base32Decode(this.o.totpSecret);
    const counter = Math.floor(Date.now() / 1000 / 30);
    const buf = new ArrayBuffer(8);
    new DataView(buf).setBigUint64(0, BigInt(counter));
    const ck = await crypto.subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-1" }, false, ["sign"]);
    const mac = new Uint8Array(await crypto.subtle.sign("HMAC", ck, buf));
    const off = mac[mac.length - 1] & 0x0f;
    const code = ((mac[off] & 0x7f) << 24 | mac[off + 1] << 16 | mac[off + 2] << 8 | mac[off + 3]) % 1_000_000;
    return String(code).padStart(6, "0");
  }

  private async headers(): Promise<Record<string, string>> {
    const h: Record<string, string> = {
      "Content-Type": "application/json",
      Authorization: `Bearer ${this.o.deviceDid}.${this.o.durableToken}`,
    };
    const code = await this.totp();
    if (code) h["X-Brainarbeit-2FA"] = code;
    return h;
  }

  private async call(method: string, path: string, body?: unknown): Promise<any> {
    const res = await fetch(this.o.baseUrl.replace(/\/$/, "") + path, {
      method,
      headers: await this.headers(),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return res.json();
  }

  async submit(taskType: string, opts: SubmitOptions = {}): Promise<any> {
    const { params, attachments, pathRefs, replyTo, needsConfirmation, tag, ...extra } = opts;
    const body: Record<string, unknown> = { task_type: taskType, params: params ?? {}, ...extra };
    if (attachments?.length) {
      body.attachments = attachments.map((a) => ({ filename: a.filename, content_b64: toBase64(a.bytes) }));
    }
    if (pathRefs) body.path_refs = pathRefs;
    if (replyTo) body.reply_to = replyTo;
    if (needsConfirmation !== undefined) body.needs_confirmation = needsConfirmation;
    if (tag) body.tag = tag;
    return this.call("POST", "/jobs", body);
  }

  approve = (nonce: string) => this.resolve(nonce, "approve");
  reject = (nonce: string) => this.resolve(nonce, "reject");
  deny = (nonce: string) => this.resolve(nonce, "deny");
  revise = (nonce: string, feedback: string) => this.resolve(nonce, "revise", feedback);
  private resolve(nonce: string, decision: Decision, feedback?: string) {
    return this.call("POST", `/approvals/${nonce}`, { decision, feedback });
  }

  steer = (id: number, input: unknown) => this.call("POST", `/jobs/${id}/steer`, { input });
  cancel = (id: number) => this.call("POST", `/jobs/${id}/cancel`);

  status = (id: number) => this.call("GET", `/jobs/${id}/cvm`);
  job = (id: number) => this.call("GET", `/jobs/${id}`);
  result = (id: number) => this.call("GET", `/jobs/${id}/result`);
  history = (id: number) => this.call("GET", `/jobs/${id}/history`);
  mine = (state?: string, limit = 50) =>
    this.call("GET", `/jobs/mine?limit=${limit}${state ? `&state=${state}` : ""}`);
  outputs = (limit = 200) => this.call("GET", `/outputs?limit=${limit}`);
  pendingApprovals = () => this.call("GET", "/approvals");
  engineStatus = () => this.call("GET", "/engine/status");
  registerWebhook = (url: string, topics?: string[]) => this.call("POST", "/webhooks", { url, topics });

  async *stream(topics?: string[], afterId?: number): AsyncGenerator<any> {
    const u = new URL(this.o.baseUrl.replace(/\/$/, "") + "/stream");
    u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
    if (topics?.length) u.searchParams.set("topics", topics.join(","));
    if (afterId !== undefined) u.searchParams.set("after_id", String(afterId));

    const ws = new WebSocket(u.toString());
    const queue: any[] = [];
    let resolveNext: ((v: void) => void) | null = null;
    let done = false;
    ws.onmessage = (e) => {
      try { queue.push(JSON.parse(typeof e.data === "string" ? e.data : "")); } catch {   }
      resolveNext?.(); resolveNext = null;
    };
    ws.onclose = () => { done = true; resolveNext?.(); resolveNext = null; };
    ws.onerror = () => { done = true; resolveNext?.(); resolveNext = null; };
    while (!done || queue.length) {
      if (queue.length) { yield queue.shift(); continue; }
      if (done) break;
      await new Promise<void>((r) => (resolveNext = r));
    }
  }
}

function toBase64(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return typeof btoa !== "undefined" ? btoa(bin) : Buffer.from(bytes).toString("base64");
}

function base32Decode(s: string): Uint8Array {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  const clean = s.replace(/=+$/, "").replace(/\s/g, "").toUpperCase();
  let bits = 0, value = 0;
  const out: number[] = [];
  for (const ch of clean) {
    const idx = alphabet.indexOf(ch);
    if (idx < 0) continue;
    value = (value << 5) | idx;
    bits += 5;
    if (bits >= 8) { out.push((value >>> (bits - 8)) & 0xff); bits -= 8; }
  }
  return new Uint8Array(out);
}
