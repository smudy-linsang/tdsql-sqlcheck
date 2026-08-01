#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${ROOT}/deploy/raw_slowlog_exporter"
OUT="${RAW_SLOWLOG_EXPORTER_OUT_DIR:-${ROOT}/deploy/raw_slowlog_exporter/bin}"
ARCH="${1:-amd64}"
case "${ARCH}" in amd64|arm64) ;; *) echo "arch must be amd64 or arm64" >&2; exit 2;; esac
command -v go >/dev/null || { echo "Go toolchain is required on the controlled build host" >&2; exit 2; }
mkdir -p "${OUT}"
CGO_ENABLED=0 GOOS=linux GOARCH="${ARCH}" go build -trimpath -buildvcs=false \
  -ldflags='-s -w' -o "${OUT}/raw_slowlog_exporter-linux-${ARCH}" "${SRC}"
sha256sum "${OUT}/raw_slowlog_exporter-linux-${ARCH}" > "${OUT}/raw_slowlog_exporter-linux-${ARCH}.sha256"
echo "built ${OUT}/raw_slowlog_exporter-linux-${ARCH}"
