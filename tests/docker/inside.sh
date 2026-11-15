#!/bin/bash
# Roda DENTRO do container RHEL-like. /work = raiz do repositório, com dist/*.rpm já construído.
set -euo pipefail
. /work/tests/docker/lib.sh
. /etc/os-release; echo "=== $PRETTY_NAME | $(python3 --version 2>&1 || true) ==="
RPM=$(ls /work/dist/dnf-monitor-*.noarch.rpm)

step "Dependências e repositórios locais de teste"
dnf -y -q install rpm-build createrepo_c python3 python3-dnf python3-requests iproute python3-dnf-plugin-versionlock >/dev/null 2>&1
python3 --version
mkdir /etc/yum.repos.d/disabled; mv /etc/yum.repos.d/*.repo /etc/yum.repos.d/disabled/
for p in demo-a demo-sec demo-broken demo-excl demo-lock kernel-headers; do mkrpm $p 1.0 /srv/base; done
mkrpm kernel-core 1.0 /srv/base 'installonlypkg(kernel)'
mkrepo /srv/base base
dnf -y -q install demo-a demo-sec demo-broken demo-excl demo-lock kernel-headers kernel-core >/dev/null 2>&1
for p in demo-a demo-sec demo-excl demo-lock kernel-headers; do mkrpm $p 2.0 /srv/upd; done
mkrpm kernel-core 2.0 /srv/upd 'installonlypkg(kernel)'
mkrpm demo-broken 2.0 /srv/upd 'R:biblioteca-que-nao-existe >= 9'
cat > /tmp/updateinfo.xml <<'EOX'
<?xml version="1.0" encoding="UTF-8"?>
<updates>
 <update from="seg@example.com" status="final" type="security" version="1">
  <id>DEMO-2026:0001</id><title>demo-sec security fix</title><release>Demo</release>
  <issued date="2026-11-01 00:00:00"/><severity>Important</severity><description>fix</description>
  <pkglist><collection short="demo"><name>demo</name>
   <package name="demo-sec" version="2.0" release="1" epoch="0" arch="noarch"><filename>demo-sec-2.0-1.noarch.rpm</filename></package>
  </collection></pkglist>
 </update>
</updates>
EOX
mkrepo /srv/upd upd /tmp/updateinfo.xml
echo "exclude=demo-excl" >> /etc/dnf/dnf.conf
dnf -y -q versionlock add demo-lock >/dev/null 2>&1 || die "versionlock add falhou"
dnf -q check-update >/dev/null 2>&1 || true
ok "6 pacotes com versão nova: 1 de segurança, 1 kernel, 1 com dependência quebrada, 1 em 'exclude', 1 com versionlock"

step "Migração: instala a v1.0-6 (do histórico do git) e atualiza para a nova"
rpm -ivh --nodeps /work/tests/docker/dnf-monitor-1.0-6.noarch.rpm >/dev/null 2>&1 || die "não instalou a 1.0-6"
[ -f /opt/dnf-monitor/dnf_monitor.py ] || die "1.0-6 não instalou"
sed -i 's/^CHECK_INTERVAL_HOURS=.*/CHECK_INTERVAL_HOURS=1/' /etc/dnf-monitor/dnf-monitor.conf   # conf "personalizado"
echo "  (informativo) a v1.0-6 enxerga as atualizações?"
timeout 40 python3 /opt/dnf-monitor/dnf_monitor.py > /tmp/old.log 2>&1 || true
if grep -q "Erro ao consultar o DNF" /tmp/old.log; then echo "  ⚠ v1.0-6 NÃO detecta nada neste DNF: $(grep -m1 'Erro ao consultar' /tmp/old.log | cut -c1-140)"
elif grep -q "pacotes pendentes" /tmp/old.log; then echo "  ℹ v1.0-6 detectou: $(grep -m1 'pacotes pendentes' /tmp/old.log | cut -c1-120)"; fi
rm -f /var/lib/dnf-monitor/state.json
dnf -y -q install "$RPM" >/tmp/upgrade.log 2>&1 || { cat /tmp/upgrade.log; die "upgrade falhou"; }
[ ! -e /opt/dnf-monitor ] && ok "/opt/dnf-monitor removido (arquivo e diretório)" || die "/opt antigo ficou"
[ ! -e /etc/systemd/system/dnf-monitor.service ] && ok "unit antiga em /etc/systemd/system removida" || die "unit antiga ficou (sobrepõe a nova)"
grep -q '^CHECK_INTERVAL_HOURS=1' /etc/dnf-monitor/dnf-monitor.conf && ok "conf do administrador preservado" || die "conf sobrescrito"
[ -f /etc/dnf-monitor/dnf-monitor.conf.rpmnew ] && ok "novo exemplo salvo em .rpmnew"

step "Arquivos instalados"
[ "$(stat -c %a /etc/dnf-monitor/dnf-monitor.conf)" = 600 ] && ok "conf com modo 600 (mesmo vindo da 1.0, que era 644)" || die "modo do conf: $(stat -c %a /etc/dnf-monitor/dnf-monitor.conf)"
[ "$(stat -c %a /var/lib/dnf-monitor)" = 750 ] && ok "/var/lib/dnf-monitor 750" || die "modo do state dir"
dnf-monitor --version
[ -f /usr/lib/systemd/system/dnf-monitor.service ] && ok "unit em /usr/lib/systemd/system" || die "sem unit"
rpm -q --scripts dnf-monitor | grep -q 'systemctl stop dnf-monitor' && ok "scriptlets presentes"
rpm -V dnf-monitor --nomtime && ok "rpm -V sem divergências" || true

step "Configuração de exemplo (placeholders) é rejeitada com EX_CONFIG"
cat > /tmp/placeholder.conf <<'EOC'
[Telegram]
BOT_TOKEN=seu_bot_token_aqui
CHAT_IDS=12345678, 87654321
[Email]
SMTP_SERVER=smtp.seuservidor.com
RECIPIENT_EMAILS=admin@seuservidor.com
EOC
rc=0; dnf-monitor -c /tmp/placeholder.conf --check-config >/dev/null 2>&1 || rc=$?
[ "$rc" = 78 ] && ok "exit 78" || die "esperava 78, veio $rc"

step "Configuração real apontando para Telegram/SMTP falsos"
# Em container o needs-restarting real sempre acusa "reboot" (boot do host): usa um stub neutro.
printf '#!/bin/sh\nexit 0\n' > /usr/local/bin/needs-restarting; chmod +x /usr/local/bin/needs-restarting
python3 /work/tests/docker/mock_servers.py & MOCK=$!
cat > /etc/dnf-monitor/dnf-monitor.conf <<'EOC'
[Telegram]
BOT_TOKEN=bot123456789:AAEhBP0av28dEXAMPLEexampleTOKEN_xyz12
CHAT_IDS=111, 222   # dois chats
API_BASE=http://127.0.0.1:8081
[Monitor]
CHECK_INTERVAL_HOURS=4
NOTIFY_ON_CLEAR=true
UPDATE_CACHE=true
[Email]
SMTP_SERVER=127.0.0.1
SMTP_PORT=2525 # relay local
SMTP_SECURITY=none
RECIPIENT_EMAILS=ops@example.com
SENDER_EMAIL=monitor@example.com
EOC
sleep 1
dnf-monitor --check-config >/dev/null && ok "--check-config"
dnf-monitor --dry-run 2>&1 | grep -q 'kernel-core' && ok "--dry-run mostra o alerta"
[ ! -e /var/lib/dnf-monitor/state.json ] && ok "--dry-run não grava estado" || die "dry-run gravou estado"

step "Ciclo 1: solver real do DNF (kernel installonly, segurança, quebrado e excluído ignorados)"
dnf-monitor --once; settle
expect "2 mensagens Telegram (2 chats)" "len(d['telegram'])==2"
expect "Telegram em HTML, URL com prefixo bot, CRÍTICO (kernel-core veio de install_set)" "'/bot123456789:' in d['telegram'][0]['path'] and 'CRÍTICO' in d['telegram'][0]['body']['text'] and d['telegram'][0]['body']['parse_mode']=='HTML'"
expect "lista tem demo-a, demo-sec, kernel-core, kernel-headers" "all(x in d['email'][0]['text/plain'] for x in ('demo-a','demo-sec','kernel-core','kernel-headers'))"
expect "demo-broken (dependência quebrada), demo-excl (exclude) e demo-lock (versionlock) NÃO aparecem" "all(x not in d['email'][0]['text/plain'] for x in ('demo-broken','demo-excl','demo-lock'))"
expect "e-mail multipart com Date/Message-ID" "len(d['email'])==1 and 'text/plain' in d['email'][0] and 'text/html' in d['email'][0] and d['email'][0]['date'] and d['email'][0]['message_id']"
expect "advisory de segurança detectada (só demo-sec)" "'demo-sec (1.0-1 -> 2.0-1) [segurança]' in d['email'][0]['text/plain'] and '(1 de segurança)' in d['email'][0]['text/plain'] and 'demo-a (1.0-1 -> 2.0-1)\n' in d['email'][0]['text/plain']"
expect "assunto crítico" "'Kernel' in d['email'][0]['subject']"
[ "$(stat -c %a /var/lib/dnf-monitor/state.json)" = 600 ] && ok "state.json criado (600)"

step "Ciclo 2 e simulação de restart: nada novo => nenhum alerta repetido"
dnf-monitor --once; dnf-monitor --once; settle
expect "continua 2 Telegram + 1 e-mail" "len(d['telegram'])==2 and len(d['email'])==1"

step "Plugins do DNF: versionlock só vale com LOAD_DNF_PLUGINS=true"
sed 's/^\[Monitor\]/[Monitor]\nLOAD_DNF_PLUGINS=false/' /etc/dnf-monitor/dnf-monitor.conf > /tmp/noplug.conf
dnf-monitor -c /tmp/noplug.conf --dry-run 2>&1 | grep -q 'demo-lock' && ok "sem plugins o pacote travado apareceria (confirma a necessidade)" || die "esperava demo-lock sem plugins"

step "Teste de envio (--test-notify)"
dnf-monitor --test-notify >/dev/null; settle
expect "teste chegou nos dois canais" "len(d['telegram'])==4 and len(d['email'])==2 and 'Teste' in d['email'][1]['subject']"

step "Repositório fora do ar"
printf '[quebrado]\nname=quebrado\nbaseurl=file:///nao/existe\nenabled=1\ngpgcheck=0\nmetadata_expire=0\n' > /etc/yum.repos.d/quebrado.repo
dnf-monitor --once >/tmp/skip.log 2>&1 && ok "SKIP_BROKEN_REPOS=true: monitor segue funcionando" || { cat /tmp/skip.log; die "ciclo falhou com repo quebrado"; }
sed -i 's/^UPDATE_CACHE=true/UPDATE_CACHE=true\nSKIP_BROKEN_REPOS=false/' /etc/dnf-monitor/dnf-monitor.conf
rc=0; dnf-monitor --once >/tmp/strict.log 2>&1 || rc=$?
[ "$rc" = 1 ] && grep -q "Falha ao consultar o DNF" /tmp/strict.log && ok "SKIP_BROKEN_REPOS=false: falha do dnf => ciclo ignorado (exit 1), sem 'tudo em dia'" || { cat /tmp/strict.log; die "esperava falha, rc=$rc"; }
expect "e nada foi enviado nem resetado" "len(d['telegram'])==4"
rm -f /etc/yum.repos.d/quebrado.repo; sed -i '/^SKIP_BROKEN_REPOS=false/d' /etc/dnf-monitor/dnf-monitor.conf

step "Instala tudo => aviso de 'servidor em dia'"
dnf -y -q upgrade --nobest --skip-broken >/dev/null 2>&1
dnf-monitor --once; settle
expect "alerta 'em dia' enviado (broken/excluído continuam ignorados)" "len(d['telegram'])==6 and 'em dia' in d['telegram'][5]['body']['text'] and 'Todas' in d['email'][2]['subject']"
dnf-monitor --once; settle
expect "e não repete" "len(d['telegram'])==6"

step "Reinicialização pendente (needs-restarting)"
printf '#!/bin/sh\necho "Core libraries or services have been updated since boot-up:"\necho "  * kernel"\necho "Reboot is required to fully utilize these updates."\nexit 1\n' > /usr/local/bin/needs-restarting; chmod +x /usr/local/bin/needs-restarting
dnf-monitor --once; dnf-monitor --once; settle
expect "um único alerta de reboot" "len(d['telegram'])==8 and 'Reinicialização' in d['telegram'][7]['body']['text'] and 'kernel' in d['telegram'][7]['body']['text']"
rm -f /usr/local/bin/needs-restarting

step "Falha de entrega: alerta é retentado, não perdido"
rm -f /var/lib/dnf-monitor/state.json
sed -i 's#^API_BASE=.*#API_BASE=http://127.0.0.1:9#; /^\[Email\]/,$d' /etc/dnf-monitor/dnf-monitor.conf
mkrpm demo-a 3.0 /srv/upd; createrepo_c -q --update /srv/upd >/dev/null
rc=0; dnf-monitor --once >/tmp/fail.log 2>&1 || rc=$?
[ "$rc" = 1 ] && ok "exit 1 quando nenhum canal entrega" || die "rc=$rc"
if grep -q 'AAEhBP0av28d' /tmp/fail.log; then die "TOKEN VAZOU NO LOG"; else ok "token ausente do log mesmo com erro de conexão"; fi
[ "$(python3 -c "import json;print(json.load(open('/var/lib/dnf-monitor/state.json'))['packages'])")" = "None" ] && ok "estado não marcou como notificado" || die "estado marcado indevidamente"

step "Daemon: SIGHUP recarrega, SIGTERM encerra limpo"
sed -i 's#^API_BASE=.*#API_BASE=http://127.0.0.1:8081#' /etc/dnf-monitor/dnf-monitor.conf
dnf-monitor > /tmp/daemon.log 2>&1 & DPID=$!
sleep 12; kill -HUP $DPID; sleep 10
if grep -q 'Configuração recarregada' /tmp/daemon.log; then ok "SIGHUP recarregou"; else cat /tmp/daemon.log; die "sem reload"; fi
kill -TERM $DPID; rc=0; wait $DPID || rc=$?
[ "$rc" = 0 ] && ok "SIGTERM => saída 0" || die "rc=$rc"

step "Remoção"
kill $MOCK 2>/dev/null || true
dnf -y -q remove dnf-monitor >/dev/null 2>&1
[ ! -e /usr/sbin/dnf-monitor ] && [ ! -e /usr/libexec/dnf-monitor ] && ok "binários removidos" || die "sobrou binário"
[ ! -e /var/lib/dnf-monitor ] && [ ! -e /var/log/dnf-monitor.log ] && ok "estado e log removidos" || die "estado/log restaram"
[ -f /etc/dnf-monitor/dnf-monitor.conf.rpmsave ] && ok "conf do administrador guardado em .rpmsave"
echo; echo "✅ $PRETTY_NAME: tudo OK"
