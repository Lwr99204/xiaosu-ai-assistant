#!/usr/bin/env bash
# 自动执行 PRD §7 可自动化的 API、文档索引与 Agent 行为验收。
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
BASE_URL="${BASE_URL:-http://127.0.0.1:8010}"
PYTHON="${PYTHON:-$REPO_ROOT/backend/.venv/Scripts/python.exe}"
PASSED=0
FAILED=0

pass() { printf 'PASS %s\n' "$*"; PASSED=$((PASSED + 1)); }
fail() { printf 'FAIL %s\n' "$*"; FAILED=$((FAILED + 1)); }
section() { printf '\n== %s ==\n' "$*"; }

if ! command -v curl >/dev/null 2>&1; then
  printf 'curl is required\n'
  exit 2
fi
if [[ ! -x "$PYTHON" ]]; then
  printf 'Python executable not found: %s (set PYTHON if needed)\n' "$PYTHON"
  exit 2
fi

section 'Health'
HEALTH="$(curl -fsS --max-time 5 "$BASE_URL/api/health" 2>/dev/null)" || HEALTH=''
if [[ -n "$HEALTH" ]] && printf '%s' "$HEALTH" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("status") == "ok" else 1)'; then
  pass 'GET /api/health status=ok'
else
  fail 'GET /api/health unavailable or status is not ok'
  printf '\nSummary: %d passed, %d failed\n' "$PASSED" "$FAILED"
  printf 'Manual: WeCom 10+3 messages, cross-user session isolation, invalid-key fallback, screenshots/video.\n'
  exit 1
fi

section 'Mock API CRUD/read paths'
EMPLOYEE="$(curl -fsS --max-time 5 "$BASE_URL/api/employee/001" 2>/dev/null)" || EMPLOYEE=''
if printf '%s' "$EMPLOYEE" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("id")=="001" and d.get("name") else 1)' 2>/dev/null; then pass 'employee lookup 001'; else fail 'employee lookup 001'; fi
MISSING_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "$BASE_URL/api/employee/acceptance-missing" 2>/dev/null)"
if [[ "$MISSING_CODE" == '404' ]]; then pass 'missing employee returns 404'; else fail "missing employee returned HTTP ${MISSING_CODE:-no-response}"; fi
ATTENDANCE="$(curl -fsS --max-time 5 "$BASE_URL/api/attendance?emp_id=001&start_date=2026-11-16&end_date=2026-11-20" 2>/dev/null)" || ATTENDANCE=''
if printf '%s' "$ATTENDANCE" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("count")==5 else 1)' 2>/dev/null; then pass 'attendance date range'; else fail 'attendance date range'; fi
ORDERS="$(curl -fsS --max-time 5 "$BASE_URL/api/orders?start_date=2026-11-16&end_date=2026-11-22" 2>/dev/null)" || ORDERS=''
if printf '%s' "$ORDERS" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); s=d.get("summary",{}); sys.exit(0 if s.get("count")==7 and float(s.get("total_amount",0))==121400 else 1)' 2>/dev/null; then pass 'orders count and paid total'; else fail 'orders count and paid total'; fi

section 'Documents: upload state and indexed lifecycle regression'
# Keep the temporary file inside the repository.  On Windows Git Bash, a
# POSIX /tmp path is not always understood by the native curl executable.
TMP_DOC=".xiaosu-acceptance-$$.md"
trap 'rm -f "$TMP_DOC"' EXIT
printf '# 自动验收临时文档\n\n验收标记 AC-%s。\n' "$(date +%s)" > "$TMP_DOC"
UPLOAD_RAW="$(curl -sS -w $'\nHTTP_STATUS:%{http_code}' --max-time 15 -F "files=@${TMP_DOC};filename=acceptance-note.md" "$BASE_URL/api/documents" 2>&1)"
UPLOAD_HTTP="${UPLOAD_RAW##*$'\nHTTP_STATUS:'}"
UPLOAD="${UPLOAD_RAW%$'\nHTTP_STATUS:'*}"
if [[ "$UPLOAD_HTTP" == "$UPLOAD_RAW" ]]; then UPLOAD_HTTP='000'; fi
UPLOAD_FIELDS="$(printf '%s' "$UPLOAD" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); r=(d.get("results") or [{}])[0]; print(str(r.get("doc_id") or "")+"\t"+str(r.get("status") or ""))' 2>/dev/null)"
DOC_ID="${UPLOAD_FIELDS%%$'\t'*}"
UPLOAD_STATUS="${UPLOAD_FIELDS#*$'\t'}"
if [[ -n "$DOC_ID" && "$UPLOAD_STATUS" == 'pending' ]]; then pass 'document upload accepted with status=pending'; else fail "document upload HTTP=${UPLOAD_HTTP} doc_id=${DOC_ID:-missing} status=${UPLOAD_STATUS:-missing}; response=${UPLOAD:0:240}"; fi

section 'Agent behavior (offline MockLLM regression tests)'
if (cd "$REPO_ROOT/backend" && "$PYTHON" -m pytest -q tests/integration/test_agent_with_mock_llm.py tests/integration/test_tool_registry.py tests/integration/test_document_api.py); then
  pass 'knowledge citations/refusal, tool questions and pending->indexed lifecycle (MockLLM/tests)'
else
  fail 'Agent MockLLM regression suite'
fi

if [[ -n "$DOC_ID" ]]; then
  DOC="$(curl -fsS --max-time 5 "$BASE_URL/api/documents/$DOC_ID" 2>/dev/null)" || DOC=''
  DOC_STATUS="$(printf '%s' "$DOC" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin).get("status", ""))' 2>/dev/null)"
  if [[ "$UPLOAD_STATUS" == 'pending' && "$DOC_STATUS" == 'indexed' ]]; then pass 'document indexed after pending upload'; else fail "document lifecycle expected pending->indexed, observed ${UPLOAD_STATUS:-missing}->${DOC_STATUS:-unavailable}"; fi
  DELETE_CODE="$(curl -sS -o /dev/null -w '%{http_code}' -X DELETE --max-time 10 "$BASE_URL/api/documents/$DOC_ID" 2>/dev/null)"
  if [[ "$DELETE_CODE" == '200' ]]; then pass 'temporary document deleted'; else fail "temporary document delete HTTP ${DELETE_CODE:-no-response}"; fi
else
  fail 'document indexing skipped: upload did not return doc_id'
  fail 'temporary document delete skipped'
fi

printf '\nManual-only acceptance items:\n'
printf '  - WeCom real-device 10+3 questions and message timing/tool/citation capture\n'
printf '  - Cross-user session isolation / whitelist using a second real account\n'
printf '  - Invalid LLM credential friendly fallback (requires controlled config change)\n'
printf '  - Real UI screenshots and <=30s demo recording\n'
printf '\nSummary: %d passed, %d failed\n' "$PASSED" "$FAILED"
if [[ "$FAILED" -gt 0 ]]; then exit 1; fi
exit 0
