#!/usr/bin/env bash
# fetch_vectors.sh — Download NIST PQC KAT vectors for ML-KEM, ML-DSA, and SLH-DSA.
#
# These are the official test vectors from the NIST PQC standards:
#   - FIPS 203 (ML-KEM): https://csrc.nist.gov/projects/post-quantum-cryptography
#   - FIPS 204 (ML-DSA): https://csrc.nist.gov/projects/post-quantum-cryptography
#   - FIPS 205 (SLH-DSA): https://csrc.nist.gov/projects/post-quantum-cryptography
#
# Usage:
#   ./scripts/fetch_vectors.sh [output_dir]
#
# Requires: curl, unzip

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="${1:-$PROJECT_ROOT/vectors}"

echo "=== PQC Test Kit — KAT Vector Downloader ==="
echo "Output: $OUTPUT_DIR"
echo ""

mkdir -p "$OUTPUT_DIR"/{ml-kem,ml-dsa,slh-dsa}

# ML-KEM (FIPS 203) KAT vectors
echo "[1/3] Fetching ML-KEM (FIPS 203) vectors..."
MLKEM_URL="https://csrc.nist.gov/csrc/media/Projects/post-quantum-cryptography/documents/round-3/submissions/Kyber-Round3.zip"
if [ ! -f "$OUTPUT_DIR/ml-kem/.downloaded" ]; then
    echo "  Downloading from NIST..."
    echo "  NOTE: NIST vector URLs change between rounds. If this fails,"
    echo "  download manually from https://csrc.nist.gov/projects/post-quantum-cryptography"
    echo "  and place .rsp files in $OUTPUT_DIR/ml-kem/"
    # curl -fsSL -o /tmp/mlkem-kat.zip "$MLKEM_URL" 2>/dev/null && \
    #     unzip -qo /tmp/mlkem-kat.zip -d /tmp/mlkem-kat && \
    #     cp /tmp/mlkem-kat/*/KAT/*.rsp "$OUTPUT_DIR/ml-kem/" && \
    #     touch "$OUTPUT_DIR/ml-kem/.downloaded" && \
    #     echo "  Done." || \
    echo "  [SKIP] Auto-download not available. Place KAT .rsp files manually."
    echo "  Expected files: ML-KEM-512.rsp, ML-KEM-768.rsp, ML-KEM-1024.rsp"
else
    echo "  Already downloaded."
fi

# ML-DSA (FIPS 204) KAT vectors
echo "[2/3] Fetching ML-DSA (FIPS 204) vectors..."
if [ ! -f "$OUTPUT_DIR/ml-dsa/.downloaded" ]; then
    echo "  [SKIP] Auto-download not available. Place KAT .rsp files manually."
    echo "  Expected files: ML-DSA-44.rsp, ML-DSA-65.rsp, ML-DSA-87.rsp"
else
    echo "  Already downloaded."
fi

# SLH-DSA (FIPS 205) KAT vectors
echo "[3/3] Fetching SLH-DSA (FIPS 205) vectors..."
if [ ! -f "$OUTPUT_DIR/slh-dsa/.downloaded" ]; then
    echo "  [SKIP] Auto-download not available. Place KAT .rsp files manually."
    echo "  Expected files: SLH-DSA-SHA2-128s.rsp, SLH-DSA-SHA2-128f.rsp, etc."
else
    echo "  Already downloaded."
fi

echo ""
echo "=== Summary ==="
echo "Vector directory: $OUTPUT_DIR"
echo ""
echo "To generate test vectors from reference implementations, run:"
echo "  go run ./cmd/pqc-testkit gen-vectors --output $OUTPUT_DIR"
echo ""
echo "To validate an implementation against vectors:"
echo "  go run ./cmd/pqc-testkit kat --vectors $OUTPUT_DIR --algorithm ml-kem"
