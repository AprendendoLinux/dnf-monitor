#!/bin/bash
# Roda dentro de um container RHEL-like com systemd como PID 1 (ver systemd-test.sh).
set -euo pipefail
. /work/plus/tests/docker/lib.sh
. /etc/os-release; echo "=== $PRETTY_NAME | systemd $(systemctl --version | head -1 | cut -d' ' -f2) | $(python3 --version) ==="
PLUS=$(ls /work/plus/dist/dnf-monitor-plus-*.noarch.rpm)
CLASSIC=$(ls /work/dist/dnf-monitor-*.noarch.rpm)
CONF=/etc/dnf-monitor-plus/dnf-monitor.conf
STATE=/var/lib/dnf-monitor-plus
SVC=dnf-monitor-plus
on_exit(){ local code=$?; if [ "$code" -ne 0 ]; then echo "--- journal:"; journalctl -u dnf-monitor -u $SVC --no-pager | tail -30; fi; }
trap on_exit EXIT
count(){ num "len(d['telegram'])"; }

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
python3 /work/plus/tests/docker/mock_servers.py > /tmp/mock.out 2>&1 &
ok "ok"

step "DNF Monitor CLÁSSICO em produção, com systemd"
dnf -y -q install "$CLASSIC" > /tmp/classic.log 2>&1 || { cat /tmp/classic.log; die "clássico falhou"; }
systemctl is-enabled dnf-monitor >/dev/null && ok "clássico instalado e habilitado"
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
if wait_for "[ \"\$(num \"len(d['telegram'])\")\" -ge 1 ]" 120; then ok "o clássico enviou o alerta"; else journalctl -u dnf-monitor --no-pager | tail -20; die "clássico sem alerta"; fi
expect "alerta crítico com pacote de segurança" "'CRÍTICO' in d['telegram'][0]['body']['text'] and '1 de segurança' in d['telegram'][0]['body']['text']"
N=$(count); [ "$N" = 1 ] && ok "1 alerta enviado pelo clássico"

step "Troca pelo Plus: serviço, configuração e estado migrados (systemd real)"
dnf -y install "$PLUS" > /tmp/migrate.log 2>&1 || { cat /tmp/migrate.log; die "troca falhou"; }
rpm -q dnf-monitor >/dev/null 2>&1 && die "clássico continua instalado" || ok "o dnf removeu o clássico"
systemctl is-active --quiet dnf-monitor && die "serviço clássico continua ativo" || ok "serviço clássico parado"
systemctl list-unit-files dnf-monitor.service 2>/dev/null | grep -q '^dnf-monitor.service' && die "unit do clássico ficou" || ok "unit do clássico removida"
systemctl is-enabled --quiet $SVC && ok "serviço do Plus habilitado" || die "Plus não habilitado"
systemctl is-active --quiet $SVC && ok "serviço do Plus ATIVO logo após a troca (configuração migrada era válida)" || { cat /tmp/migrate.log; die "Plus inativo"; }
grep -q 'configuração do dnf-monitor migrada' /tmp/migrate.log && ok "migração da configuração anunciada" || die "sem aviso"
grep -q '^BOT_TOKEN=123456789:AAEhBP0av28d' $CONF && grep -q '^\[ClientTelegram\]' $CONF && ok "conf migrado + seções do Plus" || die "conf"
PID1=$(systemctl show -p MainPID --value $SVC)
sleep 20
[ "$(count)" = "$N" ] && ok "estado migrado: o Plus NÃO repetiu o alerta já enviado pelo clássico" || die "alerta duplicado após a troca"
journalctl -u $SVC --no-pager | grep -q 'Iniciando DNF Monitor Plus' && ok "banner do Plus no journal"
if journalctl -u $SVC --no-pager | grep -q 'Falha ao consultar o DNF'; then journalctl -u $SVC --no-pager | tail; die "dnf falhou dentro do sandbox"; else ok "DNF (metadados, rpmdb) funciona dentro do sandbox ProtectSystem=strict"; fi
[ "$(stat -c %a /var/log/dnf-monitor-plus.log)" = 640 ] && ok "log com modo 640"
[ -f $STATE/history.db ] && [ "$(stat -c %a $STATE/history.db)" = 600 ] && ok "history.db criado dentro do sandbox (600)" || die "history.db"
if [ "$(systemctl show -p ReadWritePaths --value $SVC | grep -c /var/lib/dnf)" -ge 1 ]; then ok "unit permite ler o history.sqlite do DNF"; fi

step "reload (SIGHUP) roda novo ciclo e o dnf do serviço enxerga repositório novo"
mkrpm demo-a 3.0 /srv/upd; createrepo_c -q --update /srv/upd >/dev/null   # sem rodar 'dnf makecache' aqui
systemctl reload $SVC
if wait_for "[ \"\$(num \"len(d['telegram'])\")\" -ge $((N+1)) ]" 120; then ok "novo alerta após reload"; else journalctl -u $SVC --no-pager | tail -20; die "sem novo alerta"; fi
expect "mensagem cita demo-a" "'demo-a' in d['telegram'][$N]['body']['text']"
[ "$(systemctl show -p MainPID --value $SVC)" = "$PID1" ] && ok "mesmo PID (sem restart)" || die "PID mudou"
grep -q 'Configuração recarregada' /var/log/dnf-monitor-plus.log && ok "reload registrado no log"
N=$(count)

step "ALERTA IMEDIATO: o DNF baixa metadados novos e o Plus avisa sozinho, sem reload e sem esperar o intervalo"
sleep 30        # deixa o ciclo do reload terminar e a linha de base assentar
mkrpm demo-a 4.0 /srv/upd; createrepo_c -q --update /srv/upd >/dev/null
D0=$(grep -c 'O DNF mudou os metadados' /var/log/dnf-monitor-plus.log || true)
T0=$(date +%s)
dnf -q makecache >/dev/null 2>&1       # (o dnf-makecache.timer, ou o administrador, atualizando os metadados)
if wait_for "[ \"\$(num \"len(d['telegram'])\")\" -gt $N ]" 180; then ok "alerta chegou em $(( $(date +%s) - T0 ))s (CHECK_INTERVAL_HOURS=4, sem reload)"; else journalctl -u $SVC --no-pager | tail -20; die "o alerta imediato não veio"; fi
expect "o alerta é da versão nova" "'demo-a' in d['telegram'][-1]['body']['text']"
[ "$(grep -c 'O DNF mudou os metadados' /var/log/dnf-monitor-plus.log)" -gt "$D0" ] && ok "log registra a detecção imediata" || die "sem log da detecção"
[ "$(systemctl show -p MainPID --value $SVC)" = "$PID1" ] && ok "mesmo PID" || die "PID mudou"
sleep 10; N=$(count); D1=$(grep -c 'O DNF mudou os metadados' /var/log/dnf-monitor-plus.log)
sleep 130
[ "$(count)" = "$N" ] && [ "$(grep -c 'O DNF mudou os metadados' /var/log/dnf-monitor-plus.log)" = "$D1" ] && ok "a própria checagem do Plus não gera laço (nenhuma detecção nem alerta novos em 2 min)" || die "laço de checagens"

step "logrotate não reinicia o serviço nem repete alertas"
logrotate -f /etc/logrotate.d/dnf-monitor-plus
[ "$(systemctl show -p MainPID --value $SVC)" = "$PID1" ] && ok "PID inalterado após logrotate" || die "serviço reiniciou"
systemctl reload $SVC; sleep 15
[ -s /var/log/dnf-monitor-plus.log ] && grep -q 'Configuração recarregada' /var/log/dnf-monitor-plus.log && ok "daemon continua gravando no arquivo novo (WatchedFileHandler)" || die "log vazio após rotação"
[ "$(count)" = "$N" ] && ok "nenhum alerta duplicado" || die "alerta duplicado"

step "RELATÓRIO AO CLIENTE: o administrador atualiza e o próprio daemon envia no horário"
RT=$(date -u -d '+2 minutes' +%H:%M)
cat >> $CONF <<EOC

[Report]
ADMIN_NAME=Henrique Fagundes
REPORT_TIME=$RT
REPORT_TIMEZONE=UTC

[ClientTelegram]
BOT_TOKEN=987654321:AAFclientEXAMPLEexampleTOKEN_abcd1234
CHAT_IDS=999
API_BASE=http://127.0.0.1:8081

[ClientEmail]
SMTP_SERVER=127.0.0.1
SMTP_PORT=2525
SMTP_SECURITY=none
SENDER_NAME=Suporte Técnico
SENDER_EMAIL=suporte@example.com
RECIPIENT_EMAILS=cliente@example.com
EOC
systemctl reload $SVC; sleep 5
grep -q "às $RT (UTC)" /var/log/dnf-monitor-plus.log && ok "relatório agendado para $RT UTC" || die "agendamento não registrado"
dnf -y upgrade demo-sec kernel-core >/tmp/work.log 2>&1 || { cat /tmp/work.log; die "upgrade do administrador falhou"; }       # o administrador trabalha
ok "demo-sec e kernel-core atualizados (2 minutos para o horário do relatório)"
if wait_for "[ \"\$(num \"sum(1 for m in d['telegram'] if m['body']['chat_id']=='999')\")\" -ge 1 ]" 300; then ok "o cliente recebeu o relatório no Telegram, enviado pelo daemon"; else journalctl -u $SVC --no-pager | tail -25; die "relatório não saiu"; fi
expect "texto: administrador, 1 vez, 2 pacotes, sucesso" "any(m['body']['chat_id']=='999' and 'O administrador Henrique Fagundes atualizou o servidor 1 vez' in m['body']['text'] and 'Pacotes atualizados: <b>2</b>' in m['body']['text'] and 'Com falha: <b>0</b>' in m['body']['text'] for m in d['telegram'])"
if wait_for "[ \"\$(num \"sum(1 for e in d['email'] if e['to']=='cliente@example.com')\")\" -ge 1 ]" 30; then ok "e o cliente também recebeu por e-mail"; else die "sem e-mail do cliente"; fi
expect "e-mail do cliente: remetente próprio, demo-sec e kernel-core (kernel novo)" "any(e['to']=='cliente@example.com' and 'Suporte Técnico' in e['from'] and 'demo-sec' in e['text/html'] and 'kernel-core' in e['text/html'] and 'Relatório diário' in e['subject'] for e in d['email'])"
expect "o administrador NÃO recebeu relatório" "all('Relatório diário' not in m['body']['text'] for m in d['telegram'] if m['body']['chat_id']=='111') and all(e['to']!='ops@example.com' or 'Relatório diário' not in e['subject'] for e in d['email'])"
NR=$(num "len(d['telegram'])"); ER=$(num "len(d['email'])")
sleep 75
[ "$(num "len(d['telegram'])")" = "$NR" ] && [ "$(num "len(d['email'])")" = "$ER" ] && ok "continua um relatório só no dia (nada repetido nos ciclos seguintes)" || die "relatório repetido"

step "Persistência entre restarts (alertas e relatório)"
N=$(count); E=$(num "len(d['email'])")
systemctl restart $SVC; sleep 20
[ "$(count)" = "$N" ] && [ "$(num "len(d['email'])")" = "$E" ] && ok "restart do serviço não reenvia alerta nem relatório" || die "reenviou após restart"

step "Parada rápida"
S=$(date +%s); systemctl stop $SVC; E=$(date +%s)
[ $((E-S)) -le 5 ] && ok "stop em $((E-S))s" || die "stop demorou"
[ "$(systemctl show -p Result --value $SVC)" = success ] && ok "Result=success" || die "Result != success"

step "Configuração inválida => falha com 78 e SEM loop de restart"
sed -i 's/^BOT_TOKEN=.*/BOT_TOKEN=seu_bot_token_aqui/; /^\[Email\]/,$d' $CONF
systemctl start $SVC || true
sleep 40
[ "$(systemctl show -p ExecMainStatus --value $SVC)" = 78 ] && ok "ExecMainStatus=78" || die "status $(systemctl show -p ExecMainStatus --value $SVC)"
[ "$(systemctl show -p NRestarts --value $SVC)" = 0 ] && ok "NRestarts=0 (RestartPreventExitStatus)" || die "reiniciou em loop"
systemctl is-failed --quiet $SVC && ok "serviço em 'failed' com mensagem clara:" && journalctl -u $SVC --no-pager | tail -2

step "Exposição de segurança do unit (informativo)"
systemd-analyze security $SVC.service --no-pager 2>/dev/null | tail -1 || true

step "Limpeza do clássico residual não toca no Plus; remoção do Plus"
[ -f /etc/dnf-monitor/dnf-monitor.conf.rpmsave ] && ok "conf do clássico ficou em .rpmsave" || die "sem .rpmsave"
rm -rf /etc/dnf-monitor
[ -f $CONF ] && [ -f $STATE/history.db ] && [ -f $STATE/state.json ] && ok "conf, histórico e estado do Plus intactos" || die "a limpeza do clássico atingiu o Plus"
dnf -y -q remove dnf-monitor-plus >/dev/null 2>&1
if systemctl list-unit-files $SVC.service 2>/dev/null | grep -q $SVC; then die "unit ficou"; else ok "unit removida"; fi
[ ! -e $STATE ] && [ ! -e /var/log/dnf-monitor-plus.log ] && ok "estado, histórico e log removidos" || die "restos"
echo; echo "✅ $PRETTY_NAME (systemd): tudo OK"
