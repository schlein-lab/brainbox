
export type CVM = {
  id: number;
  state?: string;
  task_type?: string;
  approval_state?: 'pending' | 'resolved';
  approval_request?: {
    job_id: number; nonce: string; task_type?: string; summary?: string;
    action?: string; brick_warning?: string; digest?: string; diff?: string;
  };
  progress?: { done?: number; total?: number; msg?: string };
  partial?: unknown;
  notify?: string;
  last_event_id?: number;
};

export type PairResult = { principal: string; token: string; did?: string; caps?: string[] };
export type ConnStatus = 'connecting' | 'open' | 'closed' | 'revoked';

export interface KeystoreBackend {
  get(label: string): Promise<any | null>;
  set(label: string, rec: any): Promise<void>;
  del(label: string): Promise<void>;
  list(): Promise<any[]>;
}

export class Keystore {
  constructor(backend?: KeystoreBackend);
  setBackend(b: KeystoreBackend): void;
  alliance(label: string): Promise<any | null>;
  isPaired(label: string): Promise<boolean>;
  forget(label: string): Promise<void>;
  list(): Promise<any[]>;
}

export class Reality {
  jobs: Map<number, CVM>;
  lastEventId: number;
  pendingApprovals(): CVM[];
  apply(frame: any): any;
}

export class MediaSession {
  sessionId: string;
  remoteStream: MediaStream;
  onRemoteStream(fn: (s: MediaStream) => void): () => void;
  addLocalStream(stream: MediaStream): void;
  start(): Promise<void>;
  handleSignal(frame: any): Promise<void>;
  close(): void;
}

export class ConnectClient {
  constructor(opts: {
    boxLabel?: string; baseUrl: string; keystore?: Keystore;
    principal?: string | null; token?: string | null;
    wsImpl?: any; fetchImpl?: any;
  });
  readonly reality: Reality | undefined;

  pair(opts: { code: string; totpCode: string; label?: string | null }): Promise<PairResult>;
  isPaired(): Promise<boolean>;

  connect(): Promise<unknown>;
  onUpdate(cb: (cvm: CVM) => void): () => void;
  onStatus(cb: (s: ConnStatus) => void): () => void;
  disconnect(): void;
  pendingApprovals(): CVM[];

  sendText(text: string): Promise<any>;
  sendVoice(audioBlobOrText: Blob | string): Promise<any>;
  attach(text: string, opts?: { files?: File[]; paths?: string[] }): Promise<any>;

  approve(cvm: CVM): Promise<any>;
  reject(cvm: CVM): Promise<any>;
  revise(cvm: CVM, feedback: string): Promise<any>;
  decideSpoken(cvm: CVM, utterance: string): Promise<any>;

  startCall(opts?: { video?: boolean }): Promise<MediaSession>;
  shareScreen(): Promise<MediaSession>;
  sendVoiceMessage(opts?: { maxMs?: number }): Promise<{ stop: () => void; finish: () => Promise<any> }>;
  snapAndSend(text?: string): Promise<any>;

  watchUrl(): Promise<string>;
}

export class PairingError extends Error { need2fa: boolean; }
export const VERSION: string;

export namespace contract {
  export function userTopic(principal: string): string;
  export function isAwaiting(cvm: CVM): boolean;
  export function nonceOf(cvm: CVM): string | undefined;
  export function title(cvm: CVM): string;
  export function approvalSummary(cvm: CVM): string;
}
