#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'Error: %s\n' "$*" >&2
  if [[ -n "${JOB_ID:-}" ]]; then
    printf 'Job ID: %s\n' "$JOB_ID" >&2
  fi
  exit 1
}

[[ "$(uname -s)" == Darwin ]] || fail "This script only supports macOS."
[[ $# -eq 2 ]] || fail "Usage: $0 INPUT.app OUTPUT.app | INPUT.dmg OUTPUT.dmg"
for command in curl jq mktemp ditto python3; do
  command -v "$command" >/dev/null || fail "Required command is missing: $command"
done
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULT_HELPER="${RESULT_HELPER:-$SCRIPT_DIR/archive-helper.py}"
RESULT_HELPER_URL="${RESULT_HELPER_URL:-}"
[[ -f "$RESULT_HELPER" || -n "$RESULT_HELPER_URL" ]] ||
  fail "Required script is missing: $RESULT_HELPER. Set RESULT_HELPER_URL to download it."

API_BASE_URL="${API_BASE_URL:-http://localhost:8000/openapi/v1}"
API_BASE_URL="${API_BASE_URL%/}"
API_TOKEN="${API_TOKEN:-}"
POLL_INTERVAL="${POLL_INTERVAL:-5}"
REQUEST_TIMEOUT="${REQUEST_TIMEOUT:-0}"
CURL_RETRY_OPTIONS=(--retry 3 --retry-delay 2 --retry-all-errors)
JOB_ID=""
STATUS=""
[[ -n "$API_TOKEN" ]] || fail "API_TOKEN is required."
[[ "$API_TOKEN" != *$'\n'* && "$API_TOKEN" != *$'\r'* ]] || fail "Invalid API_TOKEN."
[[ "$API_BASE_URL" =~ ^https?://.+/openapi/v1$ ]] || fail "API_BASE_URL must end in /openapi/v1."
[[ "$POLL_INTERVAL" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "Invalid POLL_INTERVAL."
[[ ! "$POLL_INTERVAL" =~ ^0+([.]0+)?$ ]] || fail "POLL_INTERVAL must be greater than zero."
[[ "$REQUEST_TIMEOUT" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "Invalid REQUEST_TIMEOUT."
[[ -e "$1" ]] || fail "Input does not exist."
INPUT="$(cd "$(dirname "$1")" && pwd -P)/$(basename "$1")"
INPUT_NAME="$(basename "$INPUT")"
[[ ! -L "$INPUT" ]] || fail "Input must be an app directory or DMG file, not a symlink."
case "$INPUT_NAME" in
*.[aA][pP][pP])
  [[ -d "$INPUT" && -r "$INPUT" && -x "$INPUT" ]] || fail "App input must be a readable directory."
  KIND="app"
  [[ "$2" == *.[aA][pP][pP] ]] || fail "App output path must end in .app."
  ;;
*.[dD][mM][gG])
  [[ -f "$INPUT" && -r "$INPUT" && -s "$INPUT" ]] || fail "DMG input must be a readable, nonempty regular file."
  KIND="dmg"
  [[ "$2" == *.[dD][mM][gG] ]] || fail "DMG output path must end in .dmg."
  ;;
*) fail "Only .app directories or .dmg files are accepted; do not pass a ZIP. Packaging is automatic." ;;
esac
[[ "${INPUT_NAME%.*}" =~ [0-9]+[.][0-9]+[.][0-9]+ ]] ||
  fail "ZIP filename must contain a version number (X.Y.Z); include it in the input name, for example product-v1.2.3.app."
[[ -d "$(dirname "$2")" ]] || fail "Output directory does not exist."
OUTPUT="$(cd "$(dirname "$2")" && pwd -P)/$(basename "$2")"
[[ ! -e "$OUTPUT" && ! -L "$OUTPUT" ]] || fail "Output already exists: $OUTPUT"
if [[ "$KIND" == app && "$OUTPUT" == "$INPUT/"* ]]; then
  fail "Output must be outside the input App directory."
fi

# Keep all staging on the output filesystem so publishing can be an atomic rename.
WORK_DIR="$(mktemp -d "$(dirname "$OUTPUT")/.apple-remote-sign.XXXXXX")"
cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ ! -f "$RESULT_HELPER" ]]; then
  RESULT_HELPER="$WORK_DIR/archive-helper.py"
  curl --fail --location --silent --show-error --connect-timeout 10 \
    "${CURL_RETRY_OPTIONS[@]}" --max-time "$REQUEST_TIMEOUT" \
    --output "$RESULT_HELPER" -- "$RESULT_HELPER_URL" ||
    fail "Could not download helper from RESULT_HELPER_URL."
  [[ -s "$RESULT_HELPER" ]] || fail "Downloaded helper is empty."
fi
mkdir "$WORK_DIR/upload"
UPLOAD="$WORK_DIR/upload/${INPUT_NAME}.zip"
DOWNLOAD="$WORK_DIR/artifact.zip"
RESPONSE="$WORK_DIR/response.json"

perform() {
  local destination="$1" response
  shift
  if ! response="$(curl --silent --show-error --connect-timeout 10 \
    "${CURL_RETRY_OPTIONS[@]}" --max-time "$REQUEST_TIMEOUT" \
    --header "Authorization: Bearer $API_TOKEN" \
    --output "$destination" --write-out '%{http_code}\n%{redirect_url}' "$@")"; then
    fail "Network/transfer error."
  fi
  HTTP_CODE="${response%%$'\n'*}"
  REDIRECT_URL=""
  if [[ "$response" == *$'\n'* ]]; then
    REDIRECT_URL="${response#*$'\n'}"
  fi
}

check_http() {
  local body="$1" reason candidate
  candidate="$(jq -er '.data.id | select(type == "string")' "$body" 2>/dev/null || true)"
  if [[ -z "$JOB_ID" && "$candidate" =~ ^[0-9a-fA-F-]{36}$ ]]; then
    JOB_ID="$candidate"
  fi
  if [[ "$HTTP_CODE" != 200 ]]; then
    reason="$(jq -er '.error_message | select(type == "string" and length > 0)' \
      "$body" 2>/dev/null || true)"
    fail "HTTP $HTTP_CODE: ${reason:-Unexpected response from server.}"
  fi
}

read_job() {
  local identifier reason
  check_http "$RESPONSE"
  reason="$(jq -er '.error_message | select(type == "string" and length > 0)' \
    "$RESPONSE" 2>/dev/null || true)"
  [[ -z "$reason" ]] || fail "Server reported an error: $reason"
  jq -e 'type == "object" and .error_message == "" and (.data | type == "object")' \
    "$RESPONSE" >/dev/null 2>&1 || fail "Invalid JSON response envelope."
  identifier="$(jq -er '.data.id | select(type == "string")' "$RESPONSE")" || fail "Missing job ID."
  [[ "$identifier" =~ ^[0-9a-fA-F-]{36}$ ]] || fail "Invalid job ID."
  [[ -z "$JOB_ID" || "$JOB_ID" == "$identifier" ]] || fail "Response job ID changed."
  JOB_ID="$identifier"
  STATUS="$(jq -er '.data.status | select(type == "string")' "$RESPONSE")" || fail "Missing status."
  case "$STATUS" in
  preparing | processing | completed) ;;
  failed) fail "Task failed." ;;
  *) fail "Unrecognized task status: $STATUS" ;;
  esac
}

printf 'Packaging: %s\n' "$INPUT" >&2
python3 "$RESULT_HELPER" pack "$INPUT" "$UPLOAD" || fail "Could not package input as ZIP."

# Curl's multipart syntax needs quotes around filenames containing commas/semicolons.
FORM_INPUT="${UPLOAD//\\/\\\\}"
FORM_INPUT="${FORM_INPUT//\"/\\\"}"
perform "$RESPONSE" --request POST --form "file=@\"$FORM_INPUT\";type=application/zip" "$API_BASE_URL/jobs"
read_job
printf 'Job ID: %s\n' "$JOB_ID" >&2

while [[ "$STATUS" != completed ]]; do
  perform "$RESPONSE" "$API_BASE_URL/jobs/$JOB_ID"
  read_job
  printf 'Status: %s\n' "$STATUS" >&2
  if [[ "$STATUS" != completed ]]; then
    sleep "$POLL_INTERVAL"
  fi
done

perform "$RESPONSE" "$API_BASE_URL/jobs/$JOB_ID/artifact"
if [[ "$HTTP_CODE" != 302 ]]; then
  check_http "$RESPONSE"
  fail "Expected HTTP 302 with an authenticated CDN URL."
fi
[[ "$REDIRECT_URL" =~ ^https?:// ]] || fail "Invalid CDN redirect URL."
# A separate curl invocation guarantees the CDN never receives the API Bearer token.
if ! HTTP_CODE="$(curl --silent --show-error --connect-timeout 10 \
  "${CURL_RETRY_OPTIONS[@]}" --max-time "$REQUEST_TIMEOUT" --location --max-redirs 5 \
  --proto '=http,https' --proto-redir '=http,https' \
  --output "$DOWNLOAD" --write-out '%{http_code}' -- "$REDIRECT_URL")"; then
  fail "Network/transfer error while downloading from CDN."
fi
check_http "$DOWNLOAD"
python3 "$RESULT_HELPER" unpack "$DOWNLOAD" "$WORK_DIR/extracted" "$INPUT_NAME" "$KIND" "$OUTPUT" ||
  fail "Could not unpack or publish the downloaded ZIP."
printf 'Saved: %s\n' "$OUTPUT"
