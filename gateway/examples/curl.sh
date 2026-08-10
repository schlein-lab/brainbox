#!/usr/bin/env bash
set -euo pipefail
AUTH=(-H "Authorization: Bearer ${BB_DID}.${BB_TOKEN}" -H "X-Brainarbeit-2FA: ${BB_2FA}")
JSON=(-H "Content-Type: application/json")

echo "== health (unauthenticated) =="
curl -s "$BB/health" | jq .

echo "== machine-readable specs (unauthenticated) =="
curl -s "$BB/openapi.yaml"  | head -3
curl -s "$BB/asyncapi.yaml" | head -3

echo "== submit a typed job with a file attachment + a webhook return path =="
curl -s -X POST "$BB/jobs" "${AUTH[@]}" "${JSON[@]}" -d '{
  "task_type": "fill.form",
  "params": {"form": "customs-CN22"},
  "attachments": [{"filename": "invoice.pdf", "content_b64": "JVBERi0xLjQK..."}],
  "reply_to": "webhook:https://you.example/brainarbeit-hook",
  "needs_confirmation": true
}' | jq .

JOB=42

echo "== status (Canonical View Model) =="
curl -s "$BB/jobs/$JOB/cvm" "${AUTH[@]}" | jq .

echo "== list my jobs / outputs / pending approvals =="
curl -s "$BB/jobs/mine?limit=20"      "${AUTH[@]}" | jq '.jobs | length'
curl -s "$BB/outputs?limit=20"        "${AUTH[@]}" | jq '.outputs | length'
curl -s "$BB/approvals"               "${AUTH[@]}" | jq '.pending'

echo "== approve the staged job (let the side effect fire) =="
NONCE="k3y-..."
curl -s -X POST "$BB/approvals/$NONCE" "${AUTH[@]}" "${JSON[@]}" \
  -d '{"decision": "approve"}' | jq .

echo "== steer a running job / cancel =="
curl -s -X POST "$BB/jobs/$JOB/steer"  "${AUTH[@]}" "${JSON[@]}" -d '{"input":"prefer metric units"}' | jq .
curl -s -X POST "$BB/jobs/$JOB/cancel" "${AUTH[@]}" | jq .

echo "== result =="
curl -s "$BB/jobs/$JOB/result" "${AUTH[@]}" | jq .

echo "== engine/governor status =="
curl -s "$BB/engine/status" "${AUTH[@]}" | jq '.counts'

echo "== register a webhook subscription (box -> you) =="
curl -s -X POST "$BB/webhooks" "${AUTH[@]}" "${JSON[@]}" \
  -d '{"url":"https://you.example/brainarbeit-hook","topics":["user/me"]}' | jq .

echo "== reconnect replay (events since a cursor) =="
curl -s "$BB/stream/replay?topics=user/me&after_id=0" "${AUTH[@]}" | jq '.cursor'

echo "== mint a media ticket, then watch MJPEG (WebSocket) =="
TICKET=$(curl -s -X POST "$BB/media/ticket" "${AUTH[@]}" | jq -r .ticket)
echo "open ws: $BB/media/screen?ticket=$TICKET   (binary JPEG frames)"

echo "== the live event stream is WebSocket — use a WS client, e.g.: =="
echo "  websocat 'wss://brainbox.local:8810/v1/stream?topics=user/me' \\"
echo "    -H 'Authorization: Bearer $BB_DID.<token>' -H 'X-Brainarbeit-2FA: <code>'"
