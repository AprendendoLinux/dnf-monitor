#!/bin/bash
# Roda dentro de um container RHEL-like com systemd como PID 1 (ver systemd-test.sh).
set -euo pipefail
. /work/tests/docker/lib.sh
. /etc/os-release; echo "=== $PRETTY_NAME | systemd $(systemctl --version | head -1 | cut -d' ' -f2) | $(python3 --version) ==="
RPM=$(ls /work/dist/dnf-monitor-*.noarch.rpm)
on_exit(){ local code=$?; if [ "$code" -ne 0 ]; then echo "--- journal:"; journalctl -u dnf-monitor --no-pager | tail -25; fi; }
trap on_exit EXIT

step "Repositórios locais de teste"
mkdir /etc/yum.repos.d/disabled; mv /etc/yum.repos.d/*.repo /etc/yum.repos.d/disabled/
for p in demo-a demo-sec; do mkrpm $p 1.0 /srv/base; done; mkrpm kernel-core 1.0 /srv/base 'installonlypkg(kernel)'
mkrepo /srv/base base
dnf -y -q install demo-a demo-sec kernel-core >/dev/null 2>&1
for p in demo-a demo-sec; do mkrpm $p 2.0 /srv/upd; done; mkrpm kernel-core 2.0 /srv/upd 'installonlypkg(kernel)'
cat > /tmp/updateinfo.xml <<'EOX'
<?xml version="1.0" encoding="UTF-8"?>
<updates>
 <update from="seg@example.com" status="final" type="security" version="1">
  <id>DEMO-2026:0001</id><title>demo-sec</title><release>Demo</release>
  <issued date="2026-11-01 00:00:00"/><severity>Important</severity><description>fix</description>
  <pkglist><collection short="demo"><name>demo</name>
   <package name="demo-sec" version="2.0" release="1" epoch="0" arch="noarch"><filename>demo-sec-2.0-1.noarch.rpm</filename></package>
  </collection></pkglist>
 </update>
</updates>
EOX
mkrepo /srv/upd upd /tmp/updateinfo.xml
printf '#!/bin/sh\nexit 0\n' > /usr/local/bin/needs-restarting; chmod +x /usr/local/bin/needs-restarting
python3 /work/tests/docker/mock_servers.py > /tmp/mock.out 2>&1 &
ok "ok"

step "Upgrade da v1.0-6 -> nova com systemd de verdade"
rpm -ivh --nodeps /work/tests/docker/dnf-monitor-1.0-6.noarch.rpm >/dev/null 2>&1 || true
systemctl is-enabled dnf-monitor >/dev/null 2>&1 && ok "1.0-6 instalada e habilitada"
dnf -y -q install "$RPM" > /tmp/upgrade.log 2>&1 || { cat /tmp/upgrade.log; die "upgrade falhou"; }
systemctl is-enabled dnf-monitor >/dev/null && ok "serviço continua habilitado após o upgrade" || die "não habilitado"
[ "$(stat -c %a /etc/dnf-monitor/dnf-monitor.conf)" = 600 ] && ok "conf passou de 644 para 600"
[ ! -e /etc/systemd/system/dnf-monitor.service ] && ok "unit antiga em /etc/systemd/system removida" || die "unit antiga ficou"
cat /tmp/upgrade.log | grep -q 'NÃO foi iniciado' && ok "aviso do %post: configuração incompleta" || echo "  (sem aviso no log do dnf: $(wc -l < /tmp/upgrade.log) linhas)"

step "Configura e inicia (hardening ativo)"
cat > /etc/dnf-monitor/dnf-monitor.conf <<'EOC'
[Telegram]
BOT_TOKEN=123456789:AAEhBP0av28dEXAMPLEexampleTOKEN_xyz12
CHAT_IDS=111
API_BASE=http://127.0.0.1:8081
[Monitor]
CHECK_INTERVAL_HOURS=4
UPDATE_CACHE=true
[Email]
SMTP_SERVER=127.0.0.1
SMTP_PORT=2525
SMTP_SECURITY=none
RECIPIENT_EMAILS=ops@example.com
SENDER_EMAIL=monitor@example.com
EOC
sleep 1
systemctl restart dnf-monitor
if wait_for "[ \"\$(mock \"len(d['telegram'])>=1\")\" = True ]" 90; then ok "primeiro alerta enviado pelo serviço"; else journalctl -u dnf-monitor --no-pager | tail -30; die "sem alerta"; fi
expect "alerta crítico com pacote de segurança" "'CRÍTICO' in d['telegram'][0]['body']['text'] and '1 de segurança' in d['telegram'][0]['body']['text']"
expect "IPs detectados dentro do sandbox (ip addr / AF_NETLINK)" "'IPs:</b> 172.' in d['telegram'][0]['body']['text']"
systemctl is-active --quiet dnf-monitor && ok "serviço ativo" || die "inativo"
PID1=$(systemctl show -p MainPID --value dnf-monitor)
if journalctl -u dnf-monitor --no-pager | grep -q 'Falha ao consultar o DNF'; then journalctl -u dnf-monitor --no-pager | tail; die "dnf falhou dentro do sandbox"; else ok "DNF (metadados, rpmdb) funciona dentro do sandbox ProtectSystem=strict"; fi
[ "$(stat -c %a /var/log/dnf-monitor.log)" = 640 ] && ok "log com modo 640"

step "reload (SIGHUP) roda novo ciclo e o dnf do serviço enxerga repositório novo"
mkrpm demo-a 3.0 /srv/upd; createrepo_c -q --update /srv/upd >/dev/null   # sem rodar 'dnf makecache' aqui
systemctl reload dnf-monitor
if wait_for "[ \"\$(mock \"len(d['telegram'])>=2\")\" = True ]" 90; then ok "novo alerta após reload"; else journalctl -u dnf-monitor --no-pager | tail -20; die "sem 2º alerta"; fi
expect "mensagem cita demo-a 3.0 (metadados atualizados pelo próprio serviço)" "'demo-a' in d['telegram'][1]['body']['text'] and 'demo-a (1.0-1 -> 3.0-1)' in d['email'][1]['text/plain']"
[ "$(systemctl show -p MainPID --value dnf-monitor)" = "$PID1" ] && ok "mesmo PID (sem restart)" || die "PID mudou"
grep -q 'Configuração recarregada' /var/log/dnf-monitor.log && ok "reload registrado no log"

step "logrotate não reinicia o serviço nem repete alertas"
N=$(mock "len(d['telegram'])")
logrotate -f /etc/logrotate.d/dnf-monitor
[ "$(systemctl show -p MainPID --value dnf-monitor)" = "$PID1" ] && ok "PID inalterado após logrotate" || die "serviço reiniciou"
systemctl reload dnf-monitor; sleep 15
if [ -s /var/log/dnf-monitor.log ] && grep -q 'Configuração recarregada' /var/log/dnf-monitor.log; then ok "daemon continua gravando no arquivo novo (WatchedFileHandler)"; else die "log vazio após rotação"; fi
[ "$(mock "len(d['telegram'])")" = "$N" ] && ok "nenhum alerta duplicado" || die "alerta duplicado"

step "Persistência do estado entre restarts"
systemctl restart dnf-monitor; sleep 15
[ "$(mock "len(d['telegram'])")" = "$N" ] && ok "restart do serviço não reenvia alerta" || die "reenviou após restart"

step "Parada rápida"
S=$(date +%s); systemctl stop dnf-monitor; E=$(date +%s)
[ $((E-S)) -le 5 ] && ok "stop em $((E-S))s" || die "stop demorou"
[ "$(systemctl show -p Result --value dnf-monitor)" = success ] && ok "Result=success" || die "Result != success"

step "Configuração inválida => falha com 78 e SEM loop de restart"
sed -i 's/^BOT_TOKEN=.*/BOT_TOKEN=seu_bot_token_aqui/; /^\[Email\]/,$d' /etc/dnf-monitor/dnf-monitor.conf
systemctl start dnf-monitor || true
sleep 40
[ "$(systemctl show -p ExecMainStatus --value dnf-monitor)" = 78 ] && ok "ExecMainStatus=78" || die "status $(systemctl show -p ExecMainStatus --value dnf-monitor)"
[ "$(systemctl show -p NRestarts --value dnf-monitor)" = 0 ] && ok "NRestarts=0 (RestartPreventExitStatus)" || die "reiniciou em loop"
systemctl is-failed --quiet dnf-monitor && ok "serviço em 'failed' com mensagem clara:" && journalctl -u dnf-monitor --no-pager | tail -2

step "Exposição de segurança do unit (informativo)"
systemd-analyze security dnf-monitor.service --no-pager 2>/dev/null | tail -1 || true

step "Remoção"
dnf -y -q remove dnf-monitor >/dev/null 2>&1
if systemctl list-unit-files dnf-monitor.service 2>/dev/null | grep -q dnf-monitor; then die "unit ficou"; else ok "unit removida"; fi
[ ! -e /var/lib/dnf-monitor ] && [ ! -e /var/log/dnf-monitor.log ] && ok "estado e log removidos" || die "restos"
echo; echo "✅ $PRETTY_NAME (systemd): tudo OK"
