#!/usr/bin/env python3

import os, sys, json, time, tempfile, shutil
from importlib.machinery import SourceFileLoader
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

PASS, FAIL = 0, 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")

def section(name):
    print(f"\n== {name} ==")

TMP = tempfile.mkdtemp(prefix="intake-test-")
os.environ["PN_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "xdg")
os.environ.pop("PN_REPLICA_DIR", None)
os.makedirs(os.environ["PN_DATA_DIR"], exist_ok=True)
os.makedirs(os.environ["XDG_DATA_HOME"], exist_ok=True)

import pnlib
from pnlib import db, record
import intake
from intake import (
    Modality, classify, Understander, MockSTT, MockVision, MockFileParser,
    IntakePipeline, stage_artifact, build_intake_submit, submit_intake,
)

def _db():
    cx = db.connect(os.path.join(TMP, "queue.db"))
    return cx

def _mkfile(name, data=b"x"):
    p = os.path.join(TMP, name)
    with open(p, "wb") as f:
        f.write(data)
    return p

def main():

    section("[a] modality classification")
    check(classify(hint="voice") is Modality.VOICE, "explicit hint voice")
    check(classify(hint="screen") is Modality.SCREEN, "explicit hint screen (bytes can't reveal)")
    check(classify(mime="audio/ogg") is Modality.VOICE, "MIME audio/* -> voice")
    check(classify(mime="image/png") is Modality.IMAGE, "MIME image/* -> image")
    check(classify(mime="video/mp4") is Modality.VIDEO, "MIME video/* -> video")
    check(classify(filename="note.ogg") is Modality.VOICE, "ext .ogg -> voice")
    check(classify(filename="photo.JPG") is Modality.IMAGE, "ext .JPG (case) -> image")
    check(classify(filename="clip.mp4") is Modality.VIDEO, "ext .mp4 -> video")
    check(classify(filename="report.pdf") is Modality.FILE, "ext .pdf -> file")
    check(classify(head=b"\xff\xd8\xff\xe0junk") is Modality.IMAGE, "magic JPEG -> image")
    check(classify(head=b"OggS....") is Modality.VOICE, "magic Ogg -> voice")
    check(classify(head=b"RIFF\x00\x00\x00\x00WAVEfmt ") is Modality.VOICE, "magic RIFF/WAVE -> voice")
    check(classify() is Modality.FILE, "no signal -> file (never reject)")
    check(classify(hint="bogus", filename="x.png") is Modality.IMAGE, "bad hint falls through to ext")

    section("[b] understanding per modality (MOCK backends)")
    u = Understander()
    voice = _mkfile("vn.ogg", b"OggS" + os.urandom(64))
    img = _mkfile("p.jpg", b"\xff\xd8\xff" + os.urandom(64))
    scr = _mkfile("s.png", b"\x89PNG\r\n\x1a\n" + os.urandom(64))
    vid = _mkfile("c.mp4", os.urandom(128))
    txt = _mkfile("notes.txt", b"the brake job is overdue\nremember the oil filter")

    uv = u.understand(Modality.VOICE, voice)
    check(uv.modality == "voice" and "transcript" in uv.text and uv.mock,
          "voice -> mock transcription (mock=True)")
    check(uv.model == "mock-whisper", "voice model name = mock-whisper")

    ui = u.understand(Modality.IMAGE, img)
    check(ui.modality == "image" and "vision" in uv_or(ui.summary, ui.text) and ui.mock,
          "image -> mock vision (mock=True)")
    check("FORM" in ui.text, "image OCR text surfaced into .text (fill-this-form)")

    us = u.understand(Modality.SCREEN, scr, {"modality": "screen"})
    check(us.modality == "screen" and "screen" in us.summary.lower(),
          "screen -> mock vision describes a shared screen")
    check("Traceback" in us.text or "NameError" in us.text,
          "screen OCR surfaces the on-screen error (fix-my-error)")

    uvid = u.understand(Modality.VIDEO, vid, {"modality": "video"})
    check(uvid.modality == "video" and uvid.model == "mock-vision", "video -> mock vision frames")

    uf = u.understand(Modality.FILE, txt)
    check(uf.modality == "file" and "brake job" in uf.text, "file -> real text extract (bounded)")
    check(uf.detail.get("kind") == "txt", "file parser reports kind=txt")

    section("[c] backend injection (real-marked)")

    class RealVision:
        name = "prod-vision-v9"
        mock = False
        def describe(self, path, meta=None):
            return {"caption": "real model output", "objects": [], "ocr_text": ""}

    ru = Understander(vision=RealVision()).understand(Modality.IMAGE, img)
    check(ru.mock is False and ru.model == "prod-vision-v9",
          "injected real vision -> mock=False, model=prod name")

    section("[d] artifact staging (Record inputs/ + intake.json + CAS)")
    big = os.urandom(300 * 1024)
    und = u.understand(Modality.IMAGE, img)
    art = stage_artifact(4242, modality=Modality.IMAGE, understanding=und,
                         intent_text="fill this form from the photo",
                         filename="form.jpg", data=big, reply_to="telegram:555",
                         submitter_principal="lan-guest", via_method="telegram",
                         via_device="phone-1")
    ws = record.workspace_path(4242)
    check(os.path.isfile(os.path.join(ws, "inputs", "form.jpg")), "media staged under inputs/")
    check(os.path.isfile(os.path.join(ws, "inputs", "intake.json")), "intake.json sidecar present")
    sc = json.load(open(art.sidecar_path))
    check(sc["intent"] == "show", "sidecar intent == show (ACTIVE intake)")
    check(sc["instruction"] == "fill this form from the photo", "sidecar carries user instruction")
    check(sc["understanding"]["modality"] == "image", "sidecar embeds the understanding")
    check(sc["media"]["rel_path"] == "inputs/form.jpg", "sidecar media rel_path -> inputs/")
    check(art.cas_locator and art.cas_locator.startswith("cas://"), "large media offloaded to CAS")
    check(sc["media"]["cas"] == art.cas_locator, "sidecar carries the cas:// locator")

    art2 = stage_artifact(4243, modality=Modality.FILE, understanding=uf,
                          filename="../../etc/passwd", data=b"hi")
    check(os.path.dirname(art2.media_path) == os.path.join(record.workspace_path(4243), "inputs"),
          "traversal filename sanitised into inputs/ (no escape)")

    section("[e/f/i] end-to-end ingest -> standard multimodal submit")
    cx = _db()
    pipe = IntakePipeline()
    res = pipe.ingest(cx, src_path=voice, kind="voice", filename="voicenote.ogg",
                      instruction="add this to my todo list",
                      reply_to="telegram:555", submitter_principal="lan-guest",
                      via_method="telegram", via_device="phone-1", lang="en")
    check(res.job_id > 0, "ingest enqueued a real jobs row (id > 0)")
    row = db.get(cx, res.job_id, principal="lan-guest")
    check(row is not None, "row readable, ownership-scoped to submitter (db.get, no schema change)")
    check(row["task_type"] == "show.act", "row task_type == show.act")
    check(row["source"] == "intake", "row source == intake")
    check(row["reply_to"] == "telegram:555", "row reply_to flows through (stream-back ref)")
    check(row["submitter_principal"] == "lan-guest", "row submitter_principal set (tenancy owner)")
    check(row["via_method"] == "telegram", "row via_method recorded (provenance)")
    cmd = json.loads(row["cmd"])
    check(cmd[0] == "pn-show-runner" and "inputs/intake.json" in cmd,
          "row cmd targets the show-runner + sidecar")
    check("show" in cmd, "row cmd carries the show intent")

    check("--instruction" in cmd, "FIX1: persisted row cmd carries --instruction flag")
    check("add this to my todo list" in cmd,
          "FIX1: persisted row cmd carries the user instruction text")

    resolved = os.path.join(row["cwd"], json.loads(row["cmd"])[1])
    check(os.path.isfile(resolved),
          "FIX1: join(row['cwd'], sidecar_rel) resolves to the real staged sidecar (exists)")
    check(os.path.isabs(row["cwd"]), "FIX1: persisted cwd is the absolute workspace (not '.')")

    check(row["workspace_path"] == res.artifact.workspace,
          "FIX2: persisted row workspace_path == the staged workspace dir")
    check(row["workspace_path"] and os.path.isdir(row["workspace_path"]),
          "FIX2: persisted workspace_path is a real on-disk directory")

    ws2 = res.artifact.workspace
    sc2 = json.load(open(os.path.join(ws2, "inputs", "intake.json")))
    check(sc2["reply_to"] == "telegram:555" and sc2["submitter_principal"] == "lan-guest",
          "enqueued job's sidecar carries reply_to + principal (bidirectional)")
    check(sc2["understanding"]["mock"] is True, "honesty: sidecar marks the understanding as mock")

    check(db.get(cx, res.job_id, principal="someone-else") is None,
          "non-owner cannot read the intake job (integer enumeration leaks nothing)")

    section("[g] ingest from raw bytes")
    res2 = pipe.ingest(cx, data=b"\x89PNG\r\n\x1a\n" + os.urandom(200),
                       kind="screen", filename="screenshare.png",
                       instruction="look at my screen and fix the error",
                       reply_to="native:admin", submitter_principal="admin")
    check(res2.job_id > 0 and res2.modality is Modality.SCREEN, "bytes ingest -> screen job")
    sc3 = json.load(open(res2.artifact.sidecar_path))
    check("error" in sc3["instruction"], "screen instruction preserved (fix-my-error)")
    check(os.path.isfile(os.path.join(res2.artifact.workspace, "inputs", "screenshare.png")),
          "raw-bytes media staged from memory")

    section("[j] redaction: no secret-shaped token reaches the git-committed sidecar")
    SECRET = "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGG1234"
    GHP = "ghp_" + "Z" * 30
    res3 = pipe.ingest(cx, data=b"OggS" + os.urandom(64),
                       kind="voice", filename="vn.ogg",
                       instruction="use api key " + SECRET + " then summarise",
                       reply_to="telegram:555", submitter_principal="lan-guest",
                       envelope={"auth": "Authorization: Bearer " + SECRET, "pat": GHP})
    sc4_path = res3.artifact.sidecar_path
    sc4_text = open(sc4_path).read()
    check(SECRET not in sc4_text,
          "FIX3: sk-ant-* secret from instruction+envelope absent from committed sidecar")
    check(GHP not in sc4_text, "FIX3: github PAT from envelope absent from committed sidecar")
    check("redacted" in sc4_text, "FIX3: redaction mask present (secret was actually masked)")
    sc4 = json.loads(sc4_text)
    check(SECRET not in json.dumps(sc4["envelope"]),
          "FIX3: envelope subtree carries no clear-text secret")
    check(SECRET not in sc4["instruction"],
          "FIX3: instruction carries no clear-text secret")

    check("summarise" in sc4["instruction"], "FIX3: non-secret instruction prose preserved")

    row3 = dict(db.get(cx, res3.job_id, principal="lan-guest"))
    check(SECRET not in row3["cmd"], "FIX3: secret absent from the persisted cmd argv")

    section("[h] Record contract: provenance hashes the staged inputs/ for free")
    job = db.get(cx, res.job_id, principal="lan-guest")
    job = dict(job)
    job["workspace_path"] = res.artifact.workspace
    prov = record.build_provenance(job, argv=cmd, exit_code=0,
                                   started_at=time.time(), finished_at=time.time())
    inputs_h = prov["hashes"]["inputs"]
    check(any(k.endswith("intake.json") for k in inputs_h),
          "provenance hashes intake.json under inputs/ (free, no record.py change)")
    check(any("voicenote" in k for k in inputs_h),
          "provenance hashes the staged media under inputs/")

    for d in record.REQUIRED_DIRS:
        check(os.path.isdir(os.path.join(res.artifact.workspace, d)),
              f"required Record dir present: {d}")

    cx.close()

    print(f"\n{'='*52}\n  intake: {PASS} passed, {FAIL} failed\n{'='*52}")
    return 1 if FAIL else 0

def uv_or(*vals):
    return " ".join(v for v in vals if v)

if __name__ == "__main__":
    code = 0
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
