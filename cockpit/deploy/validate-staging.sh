#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COCKPIT="$(cd "$HERE/.." && pwd)"
cd "$COCKPIT"

PY="${PYTHON:-python3}"
rc=0

echo "############################################################"
echo "# cockpit staging validation (MOCK engine, headless)        "
echo "############################################################"

echo
echo ">> [1/4] dom_diff — desktop == web by construction (no engine)"
"$PY" tests/dom_diff.py || rc=1

echo
echo ">> [2/4] bridge_selftest — the 4-method native bridge, headless (no display)"
"$PY" tests/bridge_selftest.py || rc=1

echo
echo ">> [3/4] staging_check — SPA serves + live approval round-trip vs the MOCK engine"
"$PY" tests/staging_check.py || rc=1

echo
echo ">> [4/4] SPA render check (playwright-lite)"
if command -v node >/dev/null 2>&1; then
  node tests/spa_render_check.js || rc=1
else
  echo "  SKIP  node not found — the shipped CVMRender mapping is also asserted by"
  echo "        adapters/render_parity_test.py (run it for the cross-channel proof)."
  "$PY" adapters/render_parity_test.py || rc=1
fi

echo
if [ "$rc" -eq 0 ]; then
  echo "=== STAGING VALIDATION PASS — cockpit builds + serves + round-trips on the mock engine ==="
  echo "    Next: deploy/deploy.sh to install the live pn-portal service (see deploy/README.md)."
else
  echo "=== STAGING VALIDATION FAIL — do NOT deploy ==="
fi
exit "$rc"
