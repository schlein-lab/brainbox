
const VC = require("../server/webapp/vaultcrypto.js");
const FAST = { iter: 1000 };

let pass = 0, fail = 0;
async function t(name, fn) {
  try { await fn(); console.log("PASS " + name); pass++; }
  catch (e) { console.log("FAIL " + name + ": " + (e && e.message || e)); fail++; }
}
function assert(c, m) { if (!c) throw new Error(m || "assertion failed"); }
async function throws(fn, m) {
  try { await fn(); } catch (e) { return; }
  throw new Error(m || "expected throw");
}

(async () => {
  const PASSPHRASE = "correct horse battery staple";
  const RECOV = VC.generateRecoveryCode();

  await t("recovery code shape (4x5 groups, 100 bits)", async () => {
    assert(/^[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}$/.test(RECOV), "bad shape: " + RECOV);
  });

  await t("create + unlock(pass) yields empty entries", async () => {
    const blob = await VC.createVault(PASSPHRASE, RECOV, FAST);
    const { entries } = await VC.unlock(blob, PASSPHRASE, "pass");
    assert(JSON.stringify(entries) === "{}", "expected empty entries");
  });

  await t("add entry -> save -> serialize -> deserialize -> unlock round-trips", async () => {
    let blob = await VC.createVault(PASSPHRASE, RECOV, FAST);
    let { vmkBytes, entries } = await VC.unlock(blob, PASSPHRASE, "pass");
    entries["openai_api_key"] = { kind: "api_key", policy: "auto", value: "sk-TOPSECRET-123", updated: 1 };
    blob = await VC.save(blob, vmkBytes, entries);

    const wire = VC.b64e(VC.serialize(blob));
    const back = VC.deserialize(VC.b64d(wire));
    const r = await VC.unlock(back, PASSPHRASE, "pass");
    assert(r.entries.openai_api_key.value === "sk-TOPSECRET-123", "value lost in round-trip");
    assert(r.entries.openai_api_key.policy === "auto", "policy lost");
  });

  await t("recovery code unwraps the SAME VMK / same entries", async () => {
    let blob = await VC.createVault(PASSPHRASE, RECOV, FAST);
    let u = await VC.unlock(blob, PASSPHRASE, "pass");
    u.entries["x"] = { kind: "note", policy: "ask", value: "shared-secret" };
    blob = await VC.save(blob, u.vmkBytes, u.entries);
    const viaRecov = await VC.unlock(blob, RECOV, "recov");
    assert(viaRecov.entries.x.value === "shared-secret", "recovery path saw different entries");

    assert(Buffer.from(viaRecov.vmkBytes).equals(Buffer.from(u.vmkBytes)), "VMK differs across wraps");
  });

  await t("wrong passphrase is refused", async () => {
    const blob = await VC.createVault(PASSPHRASE, RECOV, FAST);
    await throws(() => VC.unlock(blob, "wrong passphrase", "pass"), "wrong secret should fail");
  });

  await t("tampered vault ciphertext is refused (GCM integrity)", async () => {
    let blob = await VC.createVault(PASSPHRASE, RECOV, FAST);
    let u = await VC.unlock(blob, PASSPHRASE, "pass");
    u.entries["y"] = { value: "z" };
    blob = await VC.save(blob, u.vmkBytes, u.entries);
    const ctb = VC.b64d(blob.vault.ct); ctb[0] ^= 0xff; blob.vault.ct = VC.b64e(ctb);
    await throws(() => VC.unlock(blob, PASSPHRASE, "pass"), "tamper should fail auth");
  });

  await t("BOX-BLIND: serialized blob leaks no plaintext / name / secret", async () => {
    let blob = await VC.createVault(PASSPHRASE, RECOV, FAST);
    let u = await VC.unlock(blob, PASSPHRASE, "pass");
    u.entries["bank_login"] = { kind: "login", policy: "touch", value: "hunter2-PLAINTEXT" };
    blob = await VC.save(blob, u.vmkBytes, u.entries);
    const wire = VC.serialize(blob).toString ? Buffer.from(VC.serialize(blob)).toString("utf8")
      : String.fromCharCode.apply(null, VC.serialize(blob));
    for (const leak of ["hunter2-PLAINTEXT", "bank_login", PASSPHRASE, RECOV, "login", "touch"]) {
      assert(wire.indexOf(leak) === -1, "blob leaked: " + leak);
    }
  });

  await t("change passphrase: old fails, new works, entries + recovery intact", async () => {
    let blob = await VC.createVault(PASSPHRASE, RECOV, FAST);
    let u = await VC.unlock(blob, PASSPHRASE, "pass");
    u.entries["k"] = { value: "v" };
    blob = await VC.save(blob, u.vmkBytes, u.entries);
    const NEWPASS = "a brand new passphrase";
    blob = await VC.addWrap(blob, u.vmkBytes, NEWPASS, "pass", FAST);
    await throws(() => VC.unlock(blob, PASSPHRASE, "pass"), "old passphrase must fail after rotation");
    const withNew = await VC.unlock(blob, NEWPASS, "pass");
    assert(withNew.entries.k.value === "v", "entries lost on passphrase change");
    const withRecov = await VC.unlock(blob, RECOV, "recov");
    assert(withRecov.entries.k.value === "v", "recovery broke on passphrase change");
  });

  await t("real-config smoke: createVault at production 600k iters works", async () => {
    const blob = await VC.createVault(PASSPHRASE, RECOV);
    const { entries } = await VC.unlock(blob, PASSPHRASE, "pass");
    assert(JSON.stringify(entries) === "{}");
    assert(blob.wrap.pass.iter === VC.ITER, "iter not recorded");
  });

  console.log("\n" + pass + "/" + (pass + fail) + " passed");
  process.exit(fail ? 1 : 0);
})();
