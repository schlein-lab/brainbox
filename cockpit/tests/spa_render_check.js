
"use strict";
const path = require("path");
const { CVMRender } = require(path.join(__dirname, "..", "web", "app.js"));

let pass = 0, fail = 0;
function check(cond, label) {
  if (cond) { pass++; console.log("  PASS  " + label); }
  else { fail++; console.log("  FAIL  " + label); }
}

const ar = {
  job_id: 7, nonce: "NONCE-XYZ", task_type: "firmware.flash",
  summary: "flash firmware v2.3 to hp-4500",
  action: "firmware.flash: flash firmware v2.3 to hp-4500",
  brick_warning: "irreversible; a failed flash can brick the device",
  submitter_principal: "admin",
};
const cvm = {
  id: 7, state: "staged", approval_state: "pending", task_type: "firmware.flash",
  approval_request: ar, nonce: ar.nonce, needs_confirmation: true,
};

console.log("=== cockpit SPA render check — shipped web/app.js CVMRender vs a mock bus frame ===");
check(typeof CVMRender === "object", "web/app.js exports CVMRender (the shipped render mapping)");
check(CVMRender.isAwaiting(cvm) === true, "CVMRender.isAwaiting -> true (lands in the inbox)");
check(CVMRender.title(cvm) === "flash firmware v2.3 to hp-4500", "CVMRender.title from the CVM summary");
const act = CVMRender.actionLine(cvm);
check(act && act.text === ar.action, "CVMRender.actionLine -> the exact about-to-happen action");
check(act && act.brick === ar.brick_warning, "CVMRender.actionLine carries the brick warning");
check(/\[BRICK RISK\]/.test(CVMRender.approvalSummary(cvm)),
      "CVMRender.approvalSummary flags BRICK RISK (the one line every channel speaks/prints)");

const cleared = Object.assign({}, cvm, { state: "queued", approval_state: "approved" });
check(CVMRender.isAwaiting(cleared) === false, "an approved CVM is no longer awaiting (card clears)");

console.log(`\n=== ${pass} passed, ${fail} failed ===`);
process.exit(fail ? 1 : 0);
