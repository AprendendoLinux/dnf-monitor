#!/bin/sh
# Renderiza os e-mails num Chromium dentro de um contêiner e salva capturas (desktop e celular) em plus/tests/render/out/.
# Uso: tests/render/run.sh      (requer Docker; usa a rede externa "echelon" do servidor)
set -eu
DIR=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$DIR/out"
rm -f "$DIR/out"/*.html "$DIR/out"/*.png
docker image inspect email-render:local >/dev/null 2>&1 || docker build -q -t email-render:local "$DIR"
python3 "$DIR/gen.py" "$DIR/out"
docker run --rm --network echelon --ipc=host -v "$DIR":/work -w /work email-render:local python3 /work/shot.py
set -- "$DIR"/out/*.png
echo "$# capturas em plus/tests/render/out/"
