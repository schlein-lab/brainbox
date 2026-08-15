
import * as C from '../src/contract.js';
import { Reality } from '../src/contract.js';
import { utteranceToVerb } from '../src/intake.js';
import { Connection } from '../src/connection.js';
import { watchMjpegUrl } from '../src/media.js';

let pass = 0, fail = 0;
const ok = (name, cond, d = '') => { (cond ? pass++ : fail++);
  console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${name}${cond ? '' : '  (' + d + ')'}`); };

console.log('== contract verbs ==');
ok('submitText shape', JSON.stringify(C.submitText('hi', { replyTopic: 'user/chris' }))
  === JSON.stringify({ verb: 'submit', task_type: 'intake.message', params: { text: 'hi', reply_to: 'user/chris' } }));
ok('subscribe topic is user/<P>', C.subscribe('chris').topics[0] === 'user/chris');
ok('approve verb', C.approve('n1').verb === 'approve' && C.approve('n1').nonce === 'n1');
ok('reject -> deny', C.reject('n1').verb === 'deny');
ok('revise -> steer', C.revise(5, 'do X').verb === 'steer');
ok('submitMedia voice-call', C.submitMedia('voice-call').params.media === 'voice-call');

console.log('\n== Reality: single reality + convergence ==');
const R = new Reality();

R.apply({ event_id: 1, type: 'subscribed', topics: ['user/chris'] });
R.apply({ event_id: 2, type: 'state', job_id: 7, state: 'queued' });
R.apply({ event_id: 3, type: 'state', job_id: 7, state: 'awaiting_approval' });
R.apply({ event_id: 4, type: 'approval-request', job_id: 7,
  approval_request: { job_id: 7, nonce: 'NONCE', task_type: 'send.email', summary: 'Reply to Acme',
    action: 'send the drafted email', digest: 'Dear Acme,' } });
ok('authorized topics captured', R.authorizedTopics[0] === 'user/chris');
ok('one pending approval', R.pendingApprovals().length === 1);
ok('cursor advances', R.lastEventId === 4);
const cvm = R.pendingApprovals()[0];
ok('nonce on the CVM', C.nonceOf(cvm) === 'NONCE');
ok('isAwaiting', C.isAwaiting(cvm));
ok('title from summary', C.title(cvm) === 'Reply to Acme');
ok('actionLine text', C.actionLine(cvm).text === 'send the drafted email');
ok('approvalSummary', C.approvalSummary(cvm) === 'Approval needed: Reply to Acme — send the drafted email');

R.apply({ event_id: 5, type: 'state', job_id: 7, state: 'done' });
ok('approval clears on remote decision (convergence)', R.pendingApprovals().length === 0);

R.apply({ event_id: 3, type: 'state', job_id: 7, state: 'awaiting_approval' });
ok('replay of old event keeps cursor monotonic', R.lastEventId === 5);

console.log('\n== brick-warning render ==');
const flashCvm = R.apply({ event_id: 6, type: 'approval-request', job_id: 9,
  approval_request: { job_id: 9, nonce: 'n9', task_type: 'device.flash', summary: 'Flash router',
    action: 'flash firmware', brick_warning: 'IRREVERSIBLE' } });
ok('brick risk surfaced', C.approvalSummary(flashCvm).includes('[BRICK RISK]'));

console.log('\n== spoken decision grammar (deny-before-approve safety) ==');
ok("'approve' -> approve", utteranceToVerb('approve').verb === 'approve');
ok("'no, do not approve' -> deny", utteranceToVerb('no, do not approve that').verb === 'deny');
const rv = utteranceToVerb('revise: use the other address');
ok("'revise ...' -> steer+feedback", rv.verb === 'steer' && rv.feedback.includes('other address'));

async function authTests() {
  console.log('\n== connection auth: durable token never in a URL ==');
  const TOKEN = 'DURABLE-SECRET-abc123';
  const TICKET = 'short.ttl.ticket.xyz';

  const seen = [];
  const fetchImpl = async (url, opts = {}) => {
    seen.push({ url, opts });
    if (String(url).endsWith('/api/stream-ticket')) {
      const auth = (opts.headers || {})['Authorization'];
      return { json: async () => (auth === `Bearer ${TOKEN}` ? { ticket: TICKET, ttl: 30 } : {}) };
    }
    return { json: async () => ({ ok: true }) };
  };

  const wsOpened = [];
  class MockWS { constructor(u) { wsOpened.push(u); this.onopen = this.onmessage = this.onclose = this.onerror = null; } close() {} }

  const conn = new Connection({ baseUrl: 'https://box.local', principal: 'chris', token: TOKEN,
    wsImpl: MockWS, fetchImpl });
  const ticket = await conn._mintStreamTicket(['user/chris']);
  ok('stream-ticket minted via Bearer HEADER (not URL)', ticket === TICKET);
  ok('ticket request carried the token in the Authorization header',
    (seen.find(s => String(s.url).endsWith('/api/stream-ticket'))?.opts.headers || {})['Authorization']
      === `Bearer ${TOKEN}`);
  ok('ticket request did NOT put the token in the URL',
    !seen.some(s => String(s.url).includes(TOKEN)));
  const wsUrl = conn._wsUrl(ticket);
  ok('WS URL uses ?ticket=, not the durable token',
    wsUrl.includes(`ticket=${encodeURIComponent(TICKET)}`) && !wsUrl.includes(TOKEN), wsUrl);

  conn.connect();
  await new Promise(r => setTimeout(r, 0));
  ok('opened WS URL contains no durable token', wsOpened.length > 0 && !wsOpened.some(u => String(u).includes(TOKEN)),
    wsOpened.join(','));
  ok('opened WS URL carries the short-TTL ticket', wsOpened.some(u => String(u).includes(`ticket=`)));
  conn.close();

  await conn.verb({ verb: 'approve', nonce: 'n1' });
  const verbReq = seen.find(s => String(s.url).endsWith('/api/verb'));
  ok('verb POST sends the token in the Authorization header',
    (verbReq?.opts.headers || {})['Authorization'] === `Bearer ${TOKEN}`);
  ok('verb POST URL contains no token', verbReq && !String(verbReq.url).includes(TOKEN));

  ok('watchMjpegUrl with a ticket carries ?ticket= and no token',
    (() => { const u = watchMjpegUrl('https://box.local', { ticket: TICKET });
      return u.includes(`ticket=${encodeURIComponent(TICKET)}`) && !u.includes(TOKEN); })());

  const lan = new Connection({ baseUrl: 'http://localhost:8088', principal: 'chris', token: null,
    wsImpl: MockWS, fetchImpl });
  const lanTicket = await lan._mintStreamTicket(['user/chris']);
  ok('LAN (peercred) mints no ticket', lanTicket === null);
  ok('LAN WS URL has neither token nor ticket',
    !lan._wsUrl(lanTicket).includes('token=') && !lan._wsUrl(lanTicket).includes('ticket='));
}

await authTests();

const { keystoreTests } = await import('./keystore_test.mjs');
await keystoreTests(ok);

console.log(`\n==== ${pass} passed, ${fail} failed ====`);
process.exit(fail ? 1 : 0);
