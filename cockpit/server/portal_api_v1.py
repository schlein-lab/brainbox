
import json
import os
import re
import threading
import time

API_VERSION = "1.0"

DATA_DIR = None
DEFAULT_PRINCIPAL = None
job_create = None
job_get = None
job_list = None
job_update = None
_prov_log = None

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

_STATE = {
    "queued": "queued", "starting": "running", "building": "running", "reviewing": "running",
    "done": "succeeded", "error": "failed", "canceled": "canceled",
}
_TERMINAL = {"succeeded", "failed", "canceled"}

_RANK = {"queued": 0, "running": 1, "succeeded": 2, "failed": 2, "canceled": 2}

_KINDS = {
    "commission": {
        "summary": "Hand the box a task in natural language; a governed agent works it and returns "
                   "a result + artifacts.",
        "input": {"prompt": "string (required) — the task, in words"},
    },
}
_DEFAULT_KIND = "commission"
_PRIORITIES = ["interactive", "batch"]

_IDEM_LOCK = threading.Lock()
_IDEM_TTL = 24 * 3600

def _idem_path():
    d = os.path.join(DATA_DIR or "/tmp", "api_v1")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "idem.json")

def _idem_get(principal, key):
    if not key:
        return None
    with _IDEM_LOCK:
        try:
            store = json.load(open(_idem_path()))
        except Exception:
            store = {}
        rec = store.get(principal + "\x1f" + key)
        if rec and (time.time() - rec.get("ts", 0)) < _IDEM_TTL:
            return rec.get("jid")
    return None

def _idem_put(principal, key, jid):
    if not key:
        return
    with _IDEM_LOCK:
        try:
            store = json.load(open(_idem_path()))
        except Exception:
            store = {}
        now = time.time()

        store = {k: v for k, v in store.items() if (now - v.get("ts", 0)) < _IDEM_TTL}
        store[principal + "\x1f" + key] = {"jid": jid, "ts": now}
        tmp = _idem_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(store, f)
        os.replace(tmp, _idem_path())

class ApiV1Routes:

    def _job_create(self, *a, **kw):
        return job_create(*a, **kw)

    def _job_get(self, *a, **kw):
        return job_get(*a, **kw)

    def _job_list(self, *a, **kw):
        return job_list(*a, **kw)

    def _job_update(self, *a, **kw):
        return job_update(*a, **kw)

    def _v1_idem_get(self, principal, key):
        return _idem_get(principal, key)

    def _v1_idem_put(self, principal, key, jid):
        return _idem_put(principal, key, jid)

    def _v1_prov(self, event, principal, target, meta):
        if _prov_log:
            try:
                _prov_log(event, principal, target, meta)
            except Exception:
                pass

    def _v1_send(self, obj, code=200, extra=None):
        headers = [("Content-Type", "application/json")] + list(extra or [])
        return self.send_html(json.dumps(obj), code, headers)

    def _v1_problem(self, code, machine_code, title, detail="", extra=None):

        body = {"type": "https://brainbox.local/api/v1/errors/" + machine_code,
                "title": title, "status": code, "code": machine_code}
        if detail:
            body["detail"] = detail
        headers = [("Content-Type", "application/problem+json")] + list(extra or [])
        return self.send_html(json.dumps(body), code, headers)

    def _v1_principal_for(self, method, path):

        if self._authed_for(method, path):
            return self._principal(), None
        if self._apikey_entry() is not None:
            return None, "insufficient_scope"
        return None, "unauthorized"

    def _authed_for(self, method, path):

        if self.authed() and (self._apikey_entry() is None or self._apikey_scoped_for(path, method)):
            return True
        return self._apikey_scoped_for(path, method)

    def _v1_auth(self, method, path):

        principal, err = self._v1_principal_for(method, path)
        if err is None:
            return principal, None
        if err == "insufficient_scope":
            return None, self._v1_problem(
                403, "insufficient_scope",
                "This API key is not scoped for %s %s." % (method, path),
                "Ask the box owner to widen the key's scopes (e.g. 'POST /api/v1/jobs' to submit).")
        return None, self._v1_problem(
            401, "unauthorized", "Missing or invalid API key.",
            "Send Authorization: Bearer <key>. Create a key in the portal (Einstellungen → API-Keys).")

    def _v1_state(self, row):
        return _STATE.get((row or {}).get("status"), "queued")

    def _v1_seq(self, row):

        st = self._v1_state(row)
        return _RANK.get(st, 0) * 1000000 + len((row or {}).get("log") or "")

    def _v1_job(self, row, full=True):
        st = self._v1_state(row)
        prompt = row.get("prompt") or ""
        job = {
            "id": "job_" + str(row.get("id")),
            "object": "job",
            "created_at": int(row.get("created") or 0),
            "principal": row.get("principal") or DEFAULT_PRINCIPAL,
            "spec": {
                "kind": "commission",
                "input": {"prompt": prompt},
                "priority": row.get("priority") or "batch",
            },
            "status": {
                "state": st,
                "room": row.get("room") or "",
                "seq": self._v1_seq(row),
            },
        }
        if st in _TERMINAL:
            job["status"]["finished"] = True
        if st == "failed":
            job["status"]["error"] = {"code": "job_failed",
                                      "message": "The job ended in an error; see /logs."}
        if full:
            arts = row.get("artifacts") or []
            job["status"]["artifacts"] = [
                {"name": a.get("name") if isinstance(a, dict) else str(a),
                 "url": "/api/jobs/%s/art/%s" % (row.get("id"),
                        a.get("name") if isinstance(a, dict) else str(a))}
                for a in arts]
        return job

    def _api_v1(self, method, u):
        path = u.path.rstrip("/") or "/api/v1"
        q = {}
        try:
            import urllib.parse as _up
            q = {k: v[0] for k, v in _up.parse_qs(u.query).items()}
        except Exception:
            q = {}

        if method == "GET" and path in ("/api/v1", "/api/v1/health"):
            return self._v1_send({"name": "Brainbox Public API", "version": API_VERSION,
                                  "ok": True, "docs": "/api/v1/openapi.json"})
        if method == "GET" and path == "/api/v1/openapi.json":
            return self._v1_send(self._v1_openapi())

        principal, problem = self._v1_auth(method, u.path)
        if problem is not None:
            return problem

        if method == "GET" and path == "/api/v1/capabilities":
            return self._v1_capabilities(principal)
        if method == "POST" and path == "/api/v1/jobs":
            return self._v1_jobs_create(principal)
        if method == "GET" and path == "/api/v1/jobs":
            return self._v1_jobs_list(principal, q)
        if path.startswith("/api/v1/jobs/"):
            rest = path[len("/api/v1/jobs/"):]
            parts = rest.split("/")
            jid_pub = parts[0]
            sub = parts[1] if len(parts) > 1 else ""
            jid = jid_pub[4:] if jid_pub.startswith("job_") else jid_pub
            if method == "POST" and sub == "cancel":
                return self._v1_job_cancel(principal, jid)
            if method == "GET" and sub == "":
                return self._v1_job_show(principal, jid)
            if method == "GET" and sub == "events":
                return self._v1_job_events(principal, jid, q)
            if method == "GET" and sub == "logs":
                return self._v1_job_logs(principal, jid)
            if method == "GET" and sub == "result":
                return self._v1_job_result(principal, jid)
        return self._v1_problem(404, "not_found", "No such API v1 route.",
                                "%s %s" % (method, u.path))

    def _v1_scope_all(self, principal):

        try:
            return bool(self._is_admin())
        except Exception:
            return False

    def _v1_jobs_create(self, principal):
        try:
            body = json.loads(self._body() or b"{}")
        except Exception:
            return self._v1_problem(400, "invalid_json", "Request body is not valid JSON.")
        if not isinstance(body, dict):
            return self._v1_problem(400, "invalid_body", "Request body must be a JSON object.")
        kind = (body.get("kind") or _DEFAULT_KIND)
        if kind not in _KINDS:
            return self._v1_problem(422, "unknown_kind",
                                    "Unknown job kind %r." % kind,
                                    "Supported: %s. See GET /api/v1/capabilities." % ", ".join(_KINDS))

        spec = body.get("spec") or {}
        inp = spec.get("input") or {}
        prompt = (inp.get("prompt") if isinstance(inp, dict) else None) \
            or body.get("prompt") or body.get("input")
        if not isinstance(prompt, str) or not prompt.strip():
            return self._v1_problem(422, "missing_prompt",
                                    "A non-empty 'prompt' is required for a commission.",
                                    "Send {\"prompt\": \"...\"} or {\"spec\":{\"input\":{\"prompt\":\"...\"}}}.")
        if len(prompt) > 20000:
            return self._v1_problem(413, "prompt_too_large", "The prompt exceeds 20000 characters.")

        idem = self.headers.get("Idempotency-Key", "") or body.get("idempotency_key", "")
        idem = re.sub(r"[^A-Za-z0-9_.:-]", "", str(idem))[:128]
        if idem:
            existing = _idem_get(principal, idem)
            if existing:
                row = job_get(existing, principal, self._v1_scope_all(principal))
                if row:
                    return self._v1_send(self._v1_job(row), 200,
                                         [("Location", "/api/v1/jobs/job_" + str(existing)),
                                          ("Idempotency-Replayed", "true")])

        prio = (spec.get("priority") if isinstance(spec, dict) else None) or body.get("priority")
        if prio is not None and prio not in _PRIORITIES:
            return self._v1_problem(422, "unknown_priority",
                                    "Unknown priority %r." % prio,
                                    "Supported: %s. See GET /api/v1/capabilities." % ", ".join(_PRIORITIES))
        jid = job_create(prompt.strip(), None, None, "commission", principal=principal, priority=prio)
        if idem:
            _idem_put(principal, idem, jid)
        if _prov_log:
            try:
                _prov_log("api.v1.job.submit", principal, jid, {"kind": kind, "len": len(prompt)})
            except Exception:
                pass
        row = job_get(jid, principal, self._v1_scope_all(principal)) or {"id": jid, "status": "queued",
                                                                         "prompt": prompt, "principal": principal,
                                                                         "created": time.time()}
        return self._v1_send(self._v1_job(row), 202,
                             [("Location", "/api/v1/jobs/job_" + str(jid)), ("Retry-After", "2")])

    def _v1_jobs_list(self, principal, q):
        rows = job_list(principal, self._v1_scope_all(principal)) or []

        limit = 20
        try:
            limit = max(1, min(100, int(q.get("limit", "20"))))
        except Exception:
            limit = 20
        cursor = q.get("cursor", "")
        if cursor:
            try:
                after = float(cursor)
                rows = [r for r in rows if float(r.get("created") or 0) < after]
            except Exception:
                pass
        page = rows[:limit]
        out = {"object": "list",
               "data": [{"id": "job_" + str(r.get("id")), "object": "job",
                         "created_at": int(r.get("created") or 0),
                         "state": _STATE.get(r.get("status"), "queued"),
                         "principal": r.get("principal") or DEFAULT_PRINCIPAL,
                         "summary": (r.get("prompt") or "")[:90]} for r in page]}
        if len(page) == limit and rows[limit:]:
            out["next_cursor"] = str(page[-1].get("created"))
        return self._v1_send(out)

    def _v1_job_show(self, principal, jid):
        row = job_get(jid, principal, self._v1_scope_all(principal))
        if not row:
            return self._v1_problem(404, "not_found", "No such job.")
        return self._v1_send(self._v1_job(row))

    def _v1_job_cancel(self, principal, jid):
        row = job_get(jid, principal, self._v1_scope_all(principal))
        if not row:
            return self._v1_problem(404, "not_found", "No such job.")
        st = self._v1_state(row)
        if st in _TERMINAL:
            return self._v1_problem(409, "already_finished",
                                    "Job is already %s and cannot be canceled." % st)
        if st == "running":

            return self._v1_problem(409, "already_running",
                                    "Job is already running; live cancel is not yet supported.",
                                    "Cancel is available while a job is still queued.")
        job_update(jid, status="canceled")
        if _prov_log:
            try:
                _prov_log("api.v1.job.cancel", principal, jid, {})
            except Exception:
                pass
        row = job_get(jid, principal, self._v1_scope_all(principal))
        return self._v1_send(self._v1_job(row))

    def _v1_job_events(self, principal, jid, q):

        row = job_get(jid, principal, self._v1_scope_all(principal))
        if not row:
            return self._v1_problem(404, "not_found", "No such job.")
        try:
            since = int(q.get("since", "-1"))
        except Exception:
            since = -1
        deadline = time.time() + 25
        while True:
            seq = self._v1_seq(row)
            if seq > since or self._v1_state(row) in _TERMINAL or time.time() > deadline:
                return self._v1_send({"id": "job_" + str(jid), "seq": seq,
                                      "state": self._v1_state(row),
                                      "finished": self._v1_state(row) in _TERMINAL})
            time.sleep(1)
            row = job_get(jid, principal, self._v1_scope_all(principal)) or row

    def _v1_job_logs(self, principal, jid):
        row = job_get(jid, principal, self._v1_scope_all(principal))
        if not row:
            return self._v1_problem(404, "not_found", "No such job.")
        return self.send_html(row.get("log") or "", 200, [("Content-Type", "text/plain; charset=utf-8")])

    def _v1_job_result(self, principal, jid):
        row = job_get(jid, principal, self._v1_scope_all(principal))
        if not row:
            return self._v1_problem(404, "not_found", "No such job.")
        st = self._v1_state(row)
        out = {"id": "job_" + str(jid), "state": st, "finished": st in _TERMINAL}
        arts = row.get("artifacts") or []
        out["artifacts"] = [{"name": a.get("name") if isinstance(a, dict) else str(a),
                             "url": "/api/jobs/%s/art/%s" % (jid,
                                    a.get("name") if isinstance(a, dict) else str(a))} for a in arts]
        if st == "succeeded":
            out["result"] = {"log_url": "/api/v1/jobs/job_%s/logs" % jid}
        elif st == "failed":
            out["error"] = {"code": "job_failed", "message": "The job ended in an error; see /logs."}
        return self._v1_send(out)

    def _v1_capabilities_obj(self, principal):

        ent = self._apikey_entry()
        scopes = (ent or {}).get("scopes") or []
        admin = False
        try:
            admin = bool(self._is_admin())
        except Exception:
            admin = False
        auth = "api_key" if ent else ("open-lan" if principal == "lan-guest"
                                      else ("session" if self.authed() else "open"))
        return {
            "object": "capabilities",
            "version": API_VERSION,
            "principal": principal,
            "auth": auth,
            "scopes": scopes if scopes else ["(full principal access)"],
            "is_admin": admin,
            "kinds": _KINDS,
            "priorities": _PRIORITIES,
            "limits": {"max_prompt_chars": 20000, "list_page_max": 100,
                       "idempotency_ttl_seconds": _IDEM_TTL},
        }

    def _v1_capabilities(self, principal):

        return self._v1_send(self._v1_capabilities_obj(principal))

    def _v1_openapi(self):
        J = {"type": "object"}
        return {
            "openapi": "3.1.0",
            "info": {"title": "Brainbox Public API", "version": API_VERSION,
                     "description": "Submit governed jobs to a Brainbox appliance and read their "
                                    "results. Every job flows through the box's queue; a scoped API "
                                    "key authenticates the caller as a real principal."},
            "servers": [{"url": "/api/v1"}],
            "components": {
                "securitySchemes": {"ApiKey": {"type": "http", "scheme": "bearer",
                                               "description": "A Brainbox API key (pak_…)."}},
                "schemas": {
                    "Job": J, "Problem": J, "Capabilities": J,
                    "JobCreate": {"type": "object", "required": ["prompt"],
                                  "properties": {"prompt": {"type": "string"},
                                                 "kind": {"type": "string", "enum": list(_KINDS)},
                                                 "idempotency_key": {"type": "string"}}},
                },
            },
            "security": [{"ApiKey": []}],
            "paths": {
                "/health": {"get": {"summary": "Liveness + version (public)", "security": [],
                                    "responses": {"200": {"description": "ok"}}}},
                "/capabilities": {"get": {"summary": "What this key may do",
                                          "responses": {"200": {"description": "Capabilities"}}}},
                "/jobs": {
                    "post": {"summary": "Submit a job", "operationId": "createJob",
                             "requestBody": {"required": True, "content": {"application/json":
                                {"schema": {"$ref": "#/components/schemas/JobCreate"}}}},
                             "parameters": [{"name": "Idempotency-Key", "in": "header",
                                             "schema": {"type": "string"}}],
                             "responses": {"202": {"description": "Accepted — Job created"},
                                           "200": {"description": "Idempotent replay"},
                                           "401": {"description": "Problem"}, "403": {"description": "Problem"},
                                           "422": {"description": "Problem"}}},
                    "get": {"summary": "List jobs (own; admin: all)",
                            "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}},
                                           {"name": "cursor", "in": "query", "schema": {"type": "string"}}],
                            "responses": {"200": {"description": "List of Job summaries"}}},
                },
                "/jobs/{id}": {"get": {"summary": "Get a job",
                                       "parameters": [{"name": "id", "in": "path", "required": True,
                                                       "schema": {"type": "string"}}],
                                       "responses": {"200": {"description": "Job"},
                                                     "404": {"description": "Problem"}}}},
                "/jobs/{id}/cancel": {"post": {"summary": "Cancel a queued job",
                                               "responses": {"200": {"description": "Job"},
                                                             "409": {"description": "Problem"}}}},
                "/jobs/{id}/events": {"get": {"summary": "Long-poll job state (?since=seq)",
                                              "responses": {"200": {"description": "state + seq"}}}},
                "/jobs/{id}/logs": {"get": {"summary": "Job log (text/plain)",
                                            "responses": {"200": {"description": "log"}}}},
                "/jobs/{id}/result": {"get": {"summary": "Job result + artifacts",
                                              "responses": {"200": {"description": "result"}}}},
            },
        }
