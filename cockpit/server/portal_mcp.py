
import json

MCP_PROTOCOL = "2025-06-18"
MCP_SERVER = {"name": "brainbox", "title": "Brainbox", "version": "1.0"}

_TOOL_AUTH = {
    "submit_job": ("POST", "/api/v1/jobs"),
    "get_job": ("GET", "/api/v1/jobs"),
    "list_jobs": ("GET", "/api/v1/jobs"),
    "job_result": ("GET", "/api/v1/jobs"),
    "cancel_job": ("POST", "/api/v1/jobs"),
    "capabilities": ("GET", "/api/v1/capabilities"),
}

_TOOLS = [
    {"name": "submit_job",
     "description": "Submit a job to the Brainbox. A governed agent works the task and returns a "
                    "result + artifacts. Returns the job id and initial state; poll get_job for progress.",
     "inputSchema": {"type": "object", "required": ["prompt"], "properties": {
         "prompt": {"type": "string", "description": "The task, in natural language."},
         "idempotency_key": {"type": "string", "description": "Optional: retrying with the same key "
                             "returns the same job instead of creating a duplicate."}}}},
    {"name": "get_job",
     "description": "Get a job's current state (queued|running|succeeded|failed|canceled) and metadata.",
     "inputSchema": {"type": "object", "required": ["id"],
                     "properties": {"id": {"type": "string", "description": "The job id (job_…)."}}}},
    {"name": "list_jobs",
     "description": "List recent jobs you own (an admin key sees all).",
     "inputSchema": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "Max jobs to return (default 20, max 100)."}}}},
    {"name": "job_result",
     "description": "Get a finished job's result and artifact list.",
     "inputSchema": {"type": "object", "required": ["id"],
                     "properties": {"id": {"type": "string"}}}},
    {"name": "cancel_job",
     "description": "Cancel a job that is still queued (a running or finished job cannot be canceled).",
     "inputSchema": {"type": "object", "required": ["id"],
                     "properties": {"id": {"type": "string"}}}},
    {"name": "capabilities",
     "description": "What this API key may do: principal, scopes, submittable job kinds, limits.",
     "inputSchema": {"type": "object", "properties": {}}},
]

class McpRoutes:
    def _mcp_rpc_result(self, rid, result):
        return self.send_html(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}),
                              200, [("Content-Type", "application/json")])

    def _mcp_rpc_error(self, rid, code, message, http=200):
        return self.send_html(json.dumps({"jsonrpc": "2.0", "id": rid,
                              "error": {"code": code, "message": message}}),
                              http, [("Content-Type", "application/json")])

    def _mcp_tool_ok(self, rid, obj):

        return self._mcp_rpc_result(rid, {"content": [{"type": "text",
                                    "text": json.dumps(obj, ensure_ascii=False)}]})

    def _mcp_tool_err(self, rid, message):
        return self._mcp_rpc_result(rid, {"isError": True,
                                    "content": [{"type": "text", "text": message}]})

    def _api_mcp(self):

        try:
            req = json.loads(self._body() or b"{}")
        except Exception:
            return self._mcp_rpc_error(None, -32700, "Parse error")
        if not isinstance(req, dict):
            return self._mcp_rpc_error(None, -32600, "Invalid Request")
        rid = req.get("id")
        method = req.get("method")

        if method == "initialize":
            return self._mcp_rpc_result(rid, {
                "protocolVersion": MCP_PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": MCP_SERVER,
                "instructions": "Submit governed jobs to this Brainbox and read their results. "
                                "Start with the capabilities tool to see what your key may do."})
        if method in ("notifications/initialized", "notifications/cancelled"):
            return self.send_html("", 202)
        if method == "ping":
            return self._mcp_rpc_result(rid, {})
        if method == "tools/list":
            return self._mcp_rpc_result(rid, {"tools": _TOOLS})
        if method == "tools/call":
            return self._mcp_tools_call(rid, req.get("params") or {})
        return self._mcp_rpc_error(rid, -32601, "Method not found: %s" % method)

    def _mcp_tools_call(self, rid, params):
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in _TOOL_AUTH:
            return self._mcp_rpc_error(rid, -32602, "Unknown tool: %s" % name)

        method, path = _TOOL_AUTH[name]
        principal, err = self._v1_principal_for(method, path)
        if err == "unauthorized":
            return self._mcp_tool_err(rid, "Unauthorized: send Authorization: Bearer <Brainbox API key>.")
        if err == "insufficient_scope":
            return self._mcp_tool_err(rid, "This API key is not scoped to call %s." % name)
        try:
            return self._mcp_dispatch(rid, name, args, principal)
        except Exception as e:
            return self._mcp_tool_err(rid, "Tool error: %s" % e)

    def _mcp_dispatch(self, rid, name, args, principal):
        admin = False
        try:
            admin = bool(self._is_admin())
        except Exception:
            admin = False

        if name == "submit_job":
            prompt = args.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                return self._mcp_tool_err(rid, "A non-empty 'prompt' is required.")
            if len(prompt) > 20000:
                return self._mcp_tool_err(rid, "The prompt exceeds 20000 characters.")
            idem = str(args.get("idempotency_key") or "")[:128]

            if idem:
                existing = self._v1_idem_get(principal, idem)
                if existing:
                    row = self._job_get(existing, principal, admin)
                    if row:
                        return self._mcp_tool_ok(rid, self._v1_job(row))
            jid = self._job_create(prompt.strip(), None, None, "commission", principal=principal)
            if idem:
                self._v1_idem_put(principal, idem, jid)
            self._v1_prov("api.v1.job.submit", principal, jid, {"via": "mcp", "len": len(prompt)})
            row = self._job_get(jid, principal, admin) or {"id": jid, "status": "queued",
                                                           "prompt": prompt, "principal": principal, "created": 0}
            return self._mcp_tool_ok(rid, self._v1_job(row))

        if name in ("get_job", "job_result"):
            jid = self._mcp_jid(args.get("id"))
            row = self._job_get(jid, principal, admin)
            if not row:
                return self._mcp_tool_err(rid, "No such job.")
            return self._mcp_tool_ok(rid, self._v1_job(row))

        if name == "list_jobs":
            try:
                limit = max(1, min(100, int(args.get("limit", 20))))
            except Exception:
                limit = 20
            rows = (self._job_list(principal, admin) or [])[:limit]
            return self._mcp_tool_ok(rid, {"data": [
                {"id": "job_" + str(r.get("id")), "state": self._v1_state(r),
                 "created_at": int(r.get("created") or 0), "summary": (r.get("prompt") or "")[:90]}
                for r in rows]})

        if name == "cancel_job":
            jid = self._mcp_jid(args.get("id"))
            row = self._job_get(jid, principal, admin)
            if not row:
                return self._mcp_tool_err(rid, "No such job.")
            st = self._v1_state(row)
            if st in ("succeeded", "failed", "canceled"):
                return self._mcp_tool_err(rid, "Job is already %s and cannot be canceled." % st)
            if st == "running":
                return self._mcp_tool_err(rid, "Job is already running; live cancel is not supported.")
            self._job_update(jid, status="canceled")
            self._v1_prov("api.v1.job.cancel", principal, jid, {"via": "mcp"})
            return self._mcp_tool_ok(rid, self._v1_job(self._job_get(jid, principal, admin)))

        if name == "capabilities":
            return self._mcp_tool_ok(rid, self._v1_capabilities_obj(principal))
        return self._mcp_tool_err(rid, "Unhandled tool: %s" % name)

    @staticmethod
    def _mcp_jid(raw):
        s = str(raw or "")
        return s[4:] if s.startswith("job_") else s
