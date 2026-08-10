
export const CONTRACT_VERBS = Object.freeze([
  'submit', 'subscribe', 'cvm', 'replay', 'approve', 'deny', 'steer', 'cancel', 'job', 'ping',
]);

export const userTopic = (principal) => `user/${principal}`;

export function submitText(text, { taskType = 'intake.message', replyTopic = null } = {}) {
  const params = { text };
  if (replyTopic) params.reply_to = replyTopic;
  return { verb: 'submit', task_type: taskType, params };
}

export function submitAttach(text, { attachments = null, paths = null,
  taskType = 'intake.attach', replyTopic = null } = {}) {
  const params = { text };
  if (attachments) params.attachments = attachments;
  if (paths) params.paths = paths;
  if (replyTopic) params.reply_to = replyTopic;
  return { verb: 'submit', task_type: taskType, params };
}

export function submitMedia(kind, { replyTopic = null, meta = null } = {}) {

  const params = { media: kind };
  if (meta) params.meta = meta;
  if (replyTopic) params.reply_to = replyTopic;
  return { verb: 'submit', task_type: 'intake.media', params };
}

export function subscribe(principal, { afterId = null, extraTopics = null } = {}) {
  const topics = [userTopic(principal), ...(extraTopics || [])];
  const req = { verb: 'subscribe', topics };
  if (afterId != null) req.after_id = afterId;
  return req;
}

export const approve = (nonce) => ({ verb: 'approve', nonce });
export const reject = (nonce) => ({ verb: 'deny', nonce });
export const revise = (jobId, feedback) => ({ verb: 'steer', id: jobId, input: feedback });
export const cvmRequest = (jobId) => ({ verb: 'cvm', id: jobId });
export const replay = (principal, afterId) =>
  ({ verb: 'replay', topics: [userTopic(principal)], after_id: afterId });

export class Reality {
  constructor() {
    this.jobs = new Map();
    this.lastEventId = 0;
    this.authorizedTopics = [];
    this.health = {};
    this.media = new Map();
  }

  apply(frame) {
    const t = frame.type || frame.kind;
    const eid = frame.event_id ?? frame.seq;
    if (Number.isInteger(eid) && eid > this.lastEventId) this.lastEventId = eid;

    if (t === 'subscribed') { this.authorizedTopics = frame.topics || []; return null; }
    if (t === 'health') { this.health = frame.payload || frame; return null; }
    if (t === 'revoked') { this.health.revoked = true; return { revoked: true }; }

    if (t === 'media-offer' || t === 'media-answer' || t === 'media-ice' || t === 'media-end') {
      return { mediaSignal: frame };
    }

    const jid = frame.job_id ?? frame.id;
    if (jid == null) return null;
    let cvm = this.jobs.get(jid);
    if (!cvm) { cvm = { id: jid }; this.jobs.set(jid, cvm); }

    switch (t) {
      case 'state':
        cvm.state = frame.state;
        if (cvm.approval_state === 'pending' &&
            !['staged', 'awaiting_approval'].includes(frame.state)) {
          cvm.approval_state = 'resolved';
        }
        break;
      case 'progress':
        cvm.progress = frame.progress ||
          { done: frame.done, total: frame.total, msg: frame.msg };
        break;
      case 'partial':
        cvm.partial = frame.partial ?? frame.text;
        break;
      case 'approval-request': {
        const ar = frame.approval_request || frame.payload || {};
        ar.job_id = ar.job_id ?? jid;
        cvm.approval_request = ar;
        cvm.approval_state = 'pending';
        cvm.state = cvm.state || 'awaiting_approval';
        cvm.task_type = ar.task_type || cvm.task_type;
        break;
      }
      case 'notify': cvm.notify = frame.text || frame.msg || frame.payload; break;
      case 'log': (cvm.log = cvm.log || []).push(frame.line || frame.msg); break;
      default: break;
    }
    cvm.last_event_id = this.lastEventId;
    return cvm;
  }

  pendingApprovals() {
    return [...this.jobs.values()].filter(isAwaiting);
  }
}

export function title(cvm) {
  const ar = cvm.approval_request || {};
  return ar.summary || cvm.task_type || `job #${cvm.id}`;
}
export function actionLine(cvm) {
  const ar = cvm.approval_request || {};
  if (ar.action) return { text: ar.action, brick: ar.brick_warning };
  const tt = cvm.task_type;
  if (tt && tt !== '(raw)') return { text: tt, brick: null };
  return null;
}
export function digest(cvm) {
  const ar = cvm.approval_request || {};
  if (ar.digest) return ar.digest;
  if (ar.preview) return ar.preview;
  const p = cvm.partial;
  if (p != null) return typeof p === 'string' ? p : JSON.stringify(p, null, 2);
  return null;
}
export function diff(cvm) { return (cvm.approval_request || {}).diff; }
export function isAwaiting(cvm) {
  return cvm.approval_state === 'pending' &&
    ['staged', 'awaiting_approval'].includes(cvm.state);
}
export function nonceOf(cvm) {
  const ar = cvm.approval_request || {};
  return ar.nonce || cvm.nonce;
}
export function approvalSummary(cvm) {
  const act = actionLine(cvm);
  let suffix = '';
  if (act) suffix = ' — ' + act.text + (act.brick ? ' [BRICK RISK]' : '');
  return `Approval needed: ${title(cvm)}${suffix}`;
}
