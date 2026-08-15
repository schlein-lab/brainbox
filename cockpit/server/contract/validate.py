#!/usr/bin/env python3

import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_DIR = os.path.join(HERE, "schemas")
EX_DIR = os.path.join(HERE, "examples")

class SchemaError(Exception):
    pass

class Validator:
    def __init__(self, registry):

        self.registry = registry

    def validate(self, schema, instance, path="$"):

        errs = []
        if schema is True or schema == {}:
            return errs
        if schema is False:
            return [f"{path}: schema=false rejects everything"]

        if "$ref" in schema:
            ref = schema["$ref"]
            if ref not in self.registry:
                raise SchemaError(f"unresolved $ref {ref!r}")
            errs += self.validate(self.registry[ref], instance, path)

            return errs

        t = schema.get("type")
        if t is not None:
            if not self._type_ok(t, instance):
                return [f"{path}: expected type {t}, got {self._jtype(instance)}"]

        if "const" in schema and instance != schema["const"]:
            errs.append(f"{path}: const mismatch, expected {schema['const']!r}")

        if "enum" in schema and instance not in schema["enum"]:
            errs.append(f"{path}: {instance!r} not in enum {schema['enum']}")

        if isinstance(instance, str) and "pattern" in schema:
            if re.search(schema["pattern"], instance) is None:
                errs.append(f"{path}: {instance!r} fails pattern {schema['pattern']!r}")

        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if "minimum" in schema and instance < schema["minimum"]:
                errs.append(f"{path}: {instance} < minimum {schema['minimum']}")
            if "maximum" in schema and instance > schema["maximum"]:
                errs.append(f"{path}: {instance} > maximum {schema['maximum']}")

        if isinstance(instance, dict):
            errs += self._validate_object(schema, instance, path)

        if isinstance(instance, list):
            errs += self._validate_array(schema, instance, path)

        for kw in ("allOf",):
            for i, sub in enumerate(schema.get(kw, [])):
                errs += self.validate(sub, instance, f"{path}/{kw}[{i}]")

        if "if" in schema:
            cond_errs = self.validate(schema["if"], instance, path + "/if")
            branch = "then" if not cond_errs else "else"
            if branch in schema:
                errs += self.validate(schema[branch], instance, f"{path}/{branch}")

        return errs

    def _validate_object(self, schema, instance, path):
        errs = []
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in instance:
                errs.append(f"{path}: missing required property {req!r}")
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            errs.append(f"{path}: fewer than minProperties {schema['minProperties']}")
        for k, v in instance.items():
            if k in props:
                errs += self.validate(props[k], v, f"{path}.{k}")
            else:
                ap = schema.get("additionalProperties", True)
                if ap is False:
                    errs.append(f"{path}: additional property {k!r} not allowed")
                elif isinstance(ap, dict):
                    errs += self.validate(ap, v, f"{path}.{k}")
        return errs

    def _validate_array(self, schema, instance, path):
        errs = []
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errs.append(f"{path}: fewer than minItems {schema['minItems']}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, it in enumerate(instance):
                errs += self.validate(item_schema, it, f"{path}[{i}]")
        if "contains" in schema:
            if not any(not self.validate(schema["contains"], it, path) for it in instance):
                errs.append(f"{path}: no item matches 'contains'")
        return errs

    @staticmethod
    def _type_ok(t, v):
        types = t if isinstance(t, list) else [t]
        for tt in types:
            if tt == "object" and isinstance(v, dict):
                return True
            if tt == "array" and isinstance(v, list):
                return True
            if tt == "string" and isinstance(v, str):
                return True
            if tt == "boolean" and isinstance(v, bool):
                return True
            if tt == "integer" and isinstance(v, int) and not isinstance(v, bool):
                return True
            if tt == "number" and isinstance(v, (int, float)) and not isinstance(v, bool):
                return True
            if tt == "null" and v is None:
                return True
        return False

    @staticmethod
    def _jtype(v):
        if isinstance(v, bool):
            return "boolean"
        if isinstance(v, dict):
            return "object"
        if isinstance(v, list):
            return "array"
        if isinstance(v, str):
            return "string"
        if isinstance(v, int):
            return "integer"
        if isinstance(v, float):
            return "number"
        if v is None:
            return "null"
        return type(v).__name__

def load_registry():
    reg = {}
    for fn in os.listdir(SCHEMA_DIR):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(SCHEMA_DIR, fn)) as f:
            schema = json.load(f)
        sid = schema.get("$id", fn)
        reg[sid] = schema
    return reg

def check_scene_frame(path):

    data = open(path, "rb").read()
    errs = []
    if data[:4] != b"PSN1":
        return [f"{path}: bad magic {data[:4]!r}, want b'PSN1'"]
    if len(data) < 28:
        return [f"{path}: header shorter than 28 bytes"]
    (version, flags, seat_gen, frame_seq, sw, sh, scount, _resv,
     total_px) = struct.unpack_from("<HHIIHHHHI", data, 4)
    if version != 1:
        errs.append(f"{path}: version {version} != 1")
    if not (flags & 1):
        errs.append(f"{path}: keyframe flag (bit0) not set (shipped slice requires it)")
    off = 28
    seen_px = 0
    for s in range(scount):
        if off + 20 > len(data):
            errs.append(f"{path}: surface {s} truncated header")
            break
        (sid, x, y, w, h, stride, fmt, sflags, dcount) = struct.unpack_from(
            "<IhhHHHBBH", data, off)
        off += 18
        off += dcount * 8
        if off + 4 > len(data):
            errs.append(f"{path}: surface {s} truncated pixel length")
            break
        (pxlen,) = struct.unpack_from("<I", data, off)
        off += 4
        if off + pxlen > len(data):
            errs.append(f"{path}: surface {s} truncated pixels (need {pxlen})")
            break
        if fmt != 0:
            errs.append(f"{path}: surface {s} format {fmt} != 0 (shm BGRA/BGRX LE only)")
        seen_px += pxlen
        off += pxlen
    if off != len(data):
        errs.append(f"{path}: {len(data) - off} trailing bytes after {scount} surfaces")
    if seen_px != total_px:
        errs.append(f"{path}: total_pixel_bytes {total_px} != summed {seen_px}")
    return errs

def check_taxonomy(registry):

    errs = []
    with open(os.path.join(HERE, "verb_taxonomy.json")) as f:
        tax = json.load(f)
    v = Validator(registry)
    errs += v.validate(registry["portal-contract/1/verb_taxonomy"], tax, "$(taxonomy)")

    verbs = tax.get("verbs", {})

    must_irrev = {
        "verb.mail_send", "verb.credential_enter", "verb.delete", "verb.pay",
        "verb.commit", "verb.kill",
        "enroll.approve", "enroll.delegate", "enroll.revoke", "enroll.rotate",
    }
    for name in must_irrev:
        if name not in verbs:
            errs.append(f"taxonomy: {name} missing (invariant 4 requires it)")
        elif verbs[name]["ceremony"] != "irreversible":
            errs.append(f"taxonomy: {name} ceremony={verbs[name]['ceremony']!r}, "
                        f"invariant 4 requires 'irreversible'")

    if verbs.get("verb.kill", {}).get("interlock") != "name-pid":
        errs.append("taxonomy: verb.kill must declare interlock 'name-pid' (invariant 12)")

    if not verbs.get("enroll.delegate", {}).get("gated"):
        errs.append("taxonomy: enroll.delegate must be gated=true (fail-closed until Q4)")
    return errs

CASES = {
    "valid.control.req.json":                 ("portal-contract/1/envelope.control", True),
    "valid.control.res_ok.json":              ("portal-contract/1/envelope.control", True),
    "valid.control.res_err.json":             ("portal-contract/1/envelope.control", True),
    "valid.render.placement.json":            ("portal-contract/1/envelope.render", True),
    "valid.capability_profile.json":          ("portal-contract/1/capability_profile", True),
    "invalid.control.bad_contract.json":      ("portal-contract/1/envelope.control", False),
    "invalid.control.req_missing_verb.json":  ("portal-contract/1/envelope.control", False),
    "invalid.control.bad_funding.json":       ("portal-contract/1/envelope.control", False),
    "invalid.control.short_sig.json":         ("portal-contract/1/envelope.control", False),
    "invalid.render.bad_mode.json":           ("portal-contract/1/envelope.render", False),
    "invalid.capability_profile.no_ed25519.json": ("portal-contract/1/capability_profile", False),
}

def main():
    as_json = "--json" in sys.argv
    registry = load_registry()
    v = Validator(registry)
    results = []
    ok = True

    for fn, (sid, expect_valid) in CASES.items():
        with open(os.path.join(EX_DIR, fn)) as f:
            inst = json.load(f)
        errs = v.validate(registry[sid], inst, "$")
        got_valid = not errs
        passed = got_valid == expect_valid
        ok &= passed
        results.append({
            "case": fn, "schema": sid, "expect_valid": expect_valid,
            "got_valid": got_valid, "passed": passed,
            "errors": errs if not got_valid else [],
        })

    sf_errs = check_scene_frame(os.path.join(EX_DIR, "valid.scene_frame_v1.bin"))
    sf_pass = not sf_errs
    ok &= sf_pass
    results.append({"case": "valid.scene_frame_v1.bin", "schema": "scene_frame_v1.layout",
                    "expect_valid": True, "got_valid": sf_pass, "passed": sf_pass,
                    "errors": sf_errs})

    good = open(os.path.join(EX_DIR, "valid.scene_frame_v1.bin"), "rb").read()
    bad = b"XXXX" + good[4:]
    tmp = os.path.join(EX_DIR, ".corrupt.scene.bin")
    open(tmp, "wb").write(bad)
    try:
        corrupt_errs = check_scene_frame(tmp)
    finally:
        os.remove(tmp)
    cpass = bool(corrupt_errs)
    ok &= cpass
    results.append({"case": "corrupt-scene(magic)", "schema": "scene_frame_v1.layout",
                    "expect_valid": False, "got_valid": not corrupt_errs, "passed": cpass,
                    "errors": []})

    tax_errs = check_taxonomy(registry)
    tax_pass = not tax_errs
    ok &= tax_pass
    results.append({"case": "verb_taxonomy.json", "schema": "portal-contract/1/verb_taxonomy",
                    "expect_valid": True, "got_valid": tax_pass, "passed": tax_pass,
                    "errors": tax_errs})

    if as_json:
        print(json.dumps({"ok": ok, "results": results}, indent=2))
    else:
        for r in results:
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"[{mark}] {r['case']}  ({r['schema']})")
            for e in r["errors"]:
                print(f"         - {e}")
        total = len(results)
        npass = sum(1 for r in results if r["passed"])
        print(f"\n{npass}/{total} checks passed.")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
