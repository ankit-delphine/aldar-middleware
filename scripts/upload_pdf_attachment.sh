#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: upload_pdf_attachment.sh <pdf_path> [entity_type] [entity_id]

Environment variables:
  API_URL   Base URL for the backend (default: http://localhost:8000)
  TOKEN     Bearer token for authentication (default: hard-coded demo token in script)

Examples:
  upload_pdf_attachment.sh ./docs/sample.pdf
  API_URL=http://localhost:9000 TOKEN=abc123 upload_pdf_attachment.sh ./docs/sample.pdf chat 123
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -lt 1 ]]; then
  echo "Error: missing <pdf_path> argument" >&2
  usage
  exit 1
fi

PDF_PATH=$1
ENTITY_TYPE=${2:-}
ENTITY_ID=${3:-}

if [[ ! -f "${PDF_PATH}" ]]; then
  echo "Error: file '${PDF_PATH}' does not exist" >&2
  exit 1
fi

API_URL=${API_URL:-http://localhost:8080}
TOKEN=${TOKEN:-"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIyMzUwZGNiNy0xNTAyLTRiOWEtOTcwMy0zZGE0YzJkMDllNTMiLCJlbWFpbCI6InJpc2hhYmhfZGRhaXNAZGVscGhpbWV1YXQuY29tIiwiZXhwIjoxNzYzMDc3MzY4LCJpYXQiOjE3NjMwMTczNjgsImlzcyI6ImFpcS1iYWNrZW5kIn0.hwJukYFg3hQqtCqySCBVl18Qx52Lf22I05HQMecASHs"}

curl_args=(
  -X POST
  "${API_URL%/}/attachments/upload"
  -H "Authorization: Bearer ${TOKEN}"
  -F "file=@${PDF_PATH};type=application/pdf"
)

if [[ -n "${ENTITY_TYPE}" ]]; then
  curl_args+=(-F "entity_type=${ENTITY_TYPE}")
fi

if [[ -n "${ENTITY_ID}" ]]; then
  curl_args+=(-F "entity_id=${ENTITY_ID}")
fi

echo "Uploading '${PDF_PATH}' to ${curl_args[2]}..."
curl "${curl_args[@]}" || {
  echo "Upload failed" >&2
  exit 1
}

