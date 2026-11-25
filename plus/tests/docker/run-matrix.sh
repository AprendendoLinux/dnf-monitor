#!/bin/bash
# Uso: plus/tests/docker/run-matrix.sh [imagem ...]   (padrão: matriz RHEL-like)
# Constrói o clássico e o Plus e roda plus/tests/docker/inside.sh em cada imagem (inclui a migração clássico -> Plus).
set -uo pipefail
cd "$(dirname "$0")/../../.." || exit 1
rm -f dist/*.rpm plus/dist/*.rpm      # nada de .rpm antigo atrapalhando a escolha dos pacotes
./build.sh >/tmp/build-classic.log 2>&1 || { tail -30 /tmp/build-classic.log; echo "build do clássico falhou"; exit 1; }
plus/build.sh >/tmp/build-plus.log 2>&1 || { tail -30 /tmp/build-plus.log; echo "build do Plus falhou"; exit 1; }
IMAGES=("$@")
[ ${#IMAGES[@]} -gt 0 ] || IMAGES=(rockylinux:8 rockylinux:9 almalinux:10 oraclelinux:9)
NETWORK=echelon; docker network inspect "$NETWORK" >/dev/null 2>&1 || NETWORK=bridge   # padrão do servidor; no CI usa a bridge
fail=0
for img in "${IMAGES[@]}"; do
    echo "################ $img"
    log="/tmp/plus-matrix-${img//[:\/]/_}.log"
    if docker run --rm --network "$NETWORK" -v "$PWD":/work:ro "$img" bash /work/plus/tests/docker/inside.sh > "$log" 2>&1; then
        echo "OK   $img"
    else
        echo "FAIL $img  (veja $log)"; tail -20 "$log"; fail=1
    fi
done
exit $fail
