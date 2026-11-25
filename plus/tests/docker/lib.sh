#!/bin/bash
# Funções comuns dos testes de integração em containers RHEL-like.
step(){ echo; echo "── $*"; }
ok(){ echo "  ✔ $*"; }
die(){ echo "  ✘ $*" >&2; exit 1; }
mock(){ python3 -I -c "import json,sys; d=json.load(open('/tmp/mock.json')); print(bool(eval(sys.argv[1])))" "$1"; }
num(){ python3 -I -c "import json,sys; d=json.load(open('/tmp/mock.json')); print(eval(sys.argv[1]))" "$1"; }   # valor numérico (mock devolve só True/False)
settle(){ sleep 0.6; }
expect(){ # expect "descrição" "expressão python sobre d"
  if [ "$(mock "$2")" = "True" ]; then ok "$1"; else echo "  mock.json:"; cat /tmp/mock.json; die "$1"; fi; }
wait_for(){ local n=0; until eval "$1"; do sleep 1; n=$((n+1)); if [ $n -gt "${2:-30}" ]; then return 1; fi; done; return 0; }

# mkrpm <nome> <versão> <dir> [provides... | R:requer...]  — gera um RPM noarch mínimo
mkrpm(){
  local name=$1 ver=$2 dir=$3; shift 3
  local top; top=$(mktemp -d); mkdir -p "$dir" "$top"/{SPECS,BUILD,RPMS,SRPMS,SOURCES}
  {
    printf 'Name: %s\nVersion: %s\nRelease: 1\nSummary: demo %s\nLicense: MIT\nBuildArch: noarch\n' "$name" "$ver" "$name"
    for p in "$@"; do case "$p" in R:*) printf 'Requires: %s\n' "${p#R:}";; *) printf 'Provides: %s\n' "$p";; esac; done
    printf '%%description\ndemo\n%%files\n'
  } > "$top/SPECS/p.spec"
  rpmbuild -bb --quiet --define "_topdir $top" "$top/SPECS/p.spec" >/dev/null 2>&1 || die "rpmbuild falhou para $name"
  cp "$top"/RPMS/noarch/*.rpm "$dir/"
}
# mkrepo <dir> <id> [updateinfo.xml]
mkrepo(){
  createrepo_c -q "$1" >/dev/null
  if [ -n "${3:-}" ]; then cp "$3" "$1/updateinfo.xml"; modifyrepo_c "$1/updateinfo.xml" "$1/repodata" >/dev/null; fi
  printf '[%s]\nname=%s\nbaseurl=file://%s\nenabled=1\ngpgcheck=0\nmetadata_expire=0\n' "$2" "$2" "$1" > "/etc/yum.repos.d/$2.repo"
}
