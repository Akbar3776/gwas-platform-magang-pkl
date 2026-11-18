#!/bin/sh
# PERINGATAN: Script ini HANYA untuk uji lokal/pengembangan (self-signed). JANGAN digunakan di produksi.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
CERT_DIR="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)/certs"
mkdir -p "$CERT_DIR"
cd "$CERT_DIR"

MSYS_NO_PATHCONV=1 openssl req -x509 -newkey rsa:2048 -nodes -days 30 \
  -subj "/CN=${1:-localhost}" \
  -keyout privkey.pem \
  -out fullchain.pem
