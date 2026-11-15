#!/bin/bash
# Uso: tests/docker/run-matrix.sh [imagem ...]   (padrão: matriz RHEL-like)
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
./build.sh >/tmp/build.log 2>&1 || { tail -30 /tmp/build.log; echo "build falhou"; exit 1; }
IMAGES=("$@")
[ ${#IMAGES[@]} -gt 0 ] || IMAGES=(rockylinux:8 rockylinux:9 almalinux:10 oraclelinux:9)
fail=0
for img in "${IMAGES[@]}"; do
    echo "################ $img"
    log="/tmp/matrix-${img//[:\/]/_}.log"
    if docker run --rm -v "$PWD":/work:ro "$img" bash /work/tests/docker/inside.sh > "$log" 2>&1; then
        echo "OK   $img"
    else
        echo "FAIL $img  (veja $log)"; tail -20 "$log"; fail=1
    fi
done
exit $fail
