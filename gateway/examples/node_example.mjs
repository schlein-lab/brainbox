
import { Brainarbeit } from "../sdk/typescript/client.ts";

const bb = new Brainarbeit({
  baseUrl: process.env.BB_URL,
  deviceDid: process.env.BB_DID,
  durableToken: process.env.BB_TOKEN,
  totpSecret: process.env.BB_TOTP,
});

const resp = await bb.submit("send.email", {
  params: { to: "kunde@example.com", subject: "Angebot" },
  attachments: [{ filename: "angebot.pdf", bytes: new TextEncoder().encode("%PDF-1.4 ...") }],
  needsConfirmation: true,
});
console.log("submitted:", resp);
const { id: jobId, nonce } = resp;

(async () => {
  for await (const frame of bb.stream([`user/${process.env.BB_PRINCIPAL ?? "me"}`])) {
    if (frame.type === "event") {
      console.log(`  [${frame.event.kind}]`, frame.event.data);
      if (frame.event.kind === "approval-result") break;
    }
  }
})();

console.log("pending:", await bb.pendingApprovals());
console.log("approve:", await bb.approve(nonce));

console.log("result:", await bb.result(jobId));
