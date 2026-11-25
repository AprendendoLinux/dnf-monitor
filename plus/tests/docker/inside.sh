#!/bin/bash
# Roda DENTRO do container RHEL-like. /work = raiz do repositório, com os .rpm já construídos:
#   /work/dist/dnf-monitor-*.noarch.rpm            (clássico)
#   /work/plus/dist/dnf-monitor-plus-*.noarch.rpm  (Plus)
set -euo pipefail
. /work/plus/tests/docker/lib.sh
. /etc/os-release; echo "=== $PRETTY_NAME | $(python3 --version 2>&1 || true) ==="
PLUS=$(ls /work/plus/dist/dnf-monitor-plus-*.noarch.rpm)
CLASSIC=$(ls /work/dist/dnf-monitor-*.noarch.rpm)
OLD10=/work/plus/tests/docker/dnf-monitor-1.0-6.noarch.rpm
CONF=/etc/dnf-monitor-plus/dnf-monitor.conf
STATE=/var/lib/dnf-monitor-plus
TOKEN='123456789:AAEhBP0av28dEXAMPLEexampleTOKEN_xyz12'      # sem o prefixo "bot" (o v1.0 exigia; o Plus aceita)

step "Dependências e repositórios locais de teste"
dnf -y -q install rpm-build createrepo_c python3 python3-dnf python3-requests iproute python3-dnf-plugin-versionlock tzdata dnf-automatic >/dev/null 2>&1
python3 --version
mkdir /etc/yum.repos.d/disabled; mv /etc/yum.repos.d/*.repo /etc/yum.repos.d/disabled/
for p in demo-a demo-sec demo-broken demo-excl demo-lock demo-auto kernel-headers; do mkrpm $p 1.0 /srv/base; done
mkrpm kernel-core 1.0 /srv/base 'installonlypkg(kernel)'
mkrepo /srv/base base
dnf -y -q install demo-a demo-sec demo-broken demo-excl demo-lock demo-auto kernel-headers kernel-core >/dev/null 2>&1
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

step "Migração A: do v1.0-6 (a antiga, em /opt) direto para o Plus"
rpm -ivh --nodeps "$OLD10" >/dev/null 2>&1 || die "não instalou a 1.0-6"
[ -f /opt/dnf-monitor/dnf_monitor.py ] || die "1.0-6 não instalou"
printf '[Telegram]\nBOT_TOKEN=%s\nCHAT_IDS=111, 222\n[Monitor]\nCHECK_INTERVAL_HOURS=1\n' "$TOKEN" > /etc/dnf-monitor/dnf-monitor.conf     # conf "personalizado"
chmod 644 /etc/dnf-monitor/dnf-monitor.conf
dnf -y install "$PLUS" > /tmp/migrateA.log 2>&1 || { cat /tmp/migrateA.log; die "a troca 1.0-6 -> Plus falhou"; }
rpm -q dnf-monitor >/dev/null 2>&1 && die "dnf-monitor continua instalado" || ok "o dnf removeu o dnf-monitor 1.0-6 (Obsoletes)"
[ ! -e /opt/dnf-monitor ] && ok "/opt/dnf-monitor removido" || die "/opt ficou"
grep -q 'configuração do dnf-monitor migrada' /tmp/migrateA.log && ok "mensagem de migração da configuração" || { cat /tmp/migrateA.log; die "sem aviso de migração"; }
grep -q '^CHECK_INTERVAL_HOURS=1' $CONF && grep -q "^BOT_TOKEN=$TOKEN" $CONF && ok "configuração da 1.0-6 foi copiada (token sem 'bot' preservado)" || die "conf não migrado"
[ "$(stat -c %a $CONF)" = 600 ] && ok "conf do Plus com modo 600 (a 1.0 deixava 644)" || die "modo do conf: $(stat -c %a $CONF)"
[ -f /etc/dnf-monitor/dnf-monitor.conf.rpmsave ] && ok "conf antigo guardado pelo rpm em .rpmsave" || die "sem .rpmsave"
dnf-monitor-plus --check-config >/dev/null 2>&1 && ok "o conf migrado é válido no Plus" || die "check-config do conf migrado"
dnf -y -q remove dnf-monitor-plus >/dev/null 2>&1; rm -rf /etc/dnf-monitor

step "Migração B: do DNF Monitor 1.1.1 (clássico atual) para o Plus"
dnf -y -q install "$CLASSIC" >/dev/null 2>&1 || die "clássico não instalou"
[ -f /usr/libexec/dnf-monitor/dnf_monitor.py ] || die "clássico não instalou"
printf '[Telegram]\nBOT_TOKEN=%s\nCHAT_IDS=111\n[Monitor]\nCHECK_INTERVAL_HOURS=2\n' "$TOKEN" > /etc/dnf-monitor/dnf-monitor.conf
echo '{"packages": ["demo-a=2.0-1"], "last_notified": 1700000000.0, "reboot_notified": false}' > /var/lib/dnf-monitor/state.json
dnf -y install "$PLUS" > /tmp/migrateB.log 2>&1 || { cat /tmp/migrateB.log; die "a troca clássico -> Plus falhou"; }
rpm -q dnf-monitor >/dev/null 2>&1 && die "clássico continua instalado" || ok "o dnf removeu o clássico (Obsoletes)"
[ ! -e /usr/libexec/dnf-monitor ] && [ ! -e /usr/lib/systemd/system/dnf-monitor.service ] && ok "arquivos do clássico removidos (binário e unit)" || die "sobrou arquivo do clássico"
grep -q '^CHECK_INTERVAL_HOURS=2' $CONF && grep -q "^BOT_TOKEN=$TOKEN" $CONF && ok "configuração do clássico copiada para o Plus" || die "conf não migrado"
grep -q '^# >>> DNF Monitor Plus >>>' $CONF && grep -q '^\[ClientTelegram\]' $CONF && grep -q '^#PENDING_ALERT_DAYS=3' $CONF && ok "seções novas do Plus acrescentadas (comentadas)" || die "faltam as seções do Plus"
[ "$(grep -c '^\[Telegram\]' $CONF)" = 1 ] && [ "$(grep -c '^# >>> DNF Monitor Plus' $CONF)" = 1 ] && ok "sem seção nem marcador duplicados" || die "duplicados"
python3 -c "import json;assert json.load(open('$STATE/state.json'))['packages']==['demo-a=2.0-1']" && [ "$(stat -c %a $STATE/state.json)" = 600 ] && ok "state.json migrado (alertas já enviados não se repetem)" || die "estado não migrado"
[ -f /etc/dnf-monitor/dnf-monitor.conf.rpmsave ] && ok "conf do clássico guardado em .rpmsave (para voltar atrás)" || die "sem .rpmsave"
dnf-monitor-plus --version | grep -q '^dnf-monitor-plus 1\.' && dnf-monitor --version | grep -q '^dnf-monitor-plus ' && ok "comando dnf-monitor-plus e o atalho dnf-monitor" || die "comandos"
rm -rf /etc/dnf-monitor
[ -f $CONF ] && [ -f $STATE/state.json ] && ok "apagar o que sobrou do clássico não toca nos arquivos do Plus" || die "o Plus perdeu arquivos"

step "O Plus obsoleta o clássico: não dá para ter os dois"
rc=0; dnf -y install "$CLASSIC" >/tmp/both.log 2>&1 || rc=$?
rpm -q dnf-monitor >/dev/null 2>&1 && die "o clássico foi instalado junto com o Plus" || ok "com o Plus instalado, o dnf não instala o clássico (rc=$rc)"

step "Rollback: remove o Plus, volta ao clássico e instala o Plus de novo"
cp $CONF /tmp/plus-conf.bak
dnf -y -q remove dnf-monitor-plus >/dev/null 2>&1
[ ! -e $STATE ] && [ ! -e /usr/sbin/dnf-monitor-plus ] && ok "remover o Plus limpa estado, histórico e binários" || die "o Plus deixou resíduos"
[ -f $CONF.rpmsave ] && ok "e guarda o conf dele em .rpmsave" || die "sem .rpmsave do Plus"
dnf -y -q install "$CLASSIC" >/dev/null 2>&1 && rpm -q dnf-monitor >/dev/null && ok "o clássico volta a instalar" || die "clássico não voltou"
cp /tmp/plus-conf.bak /etc/dnf-monitor/dnf-monitor.conf          # o conf do Plus (com as seções novas) no clássico
dnf-monitor --check-config >/dev/null 2>&1 && ok "o clássico ignora as seções que não conhece ([Report], [Client…])" || die "o clássico recusou o conf do Plus"
dnf -y install "$PLUS" > /tmp/migrateC.log 2>&1 || { cat /tmp/migrateC.log; die "reinstalar o Plus falhou"; }
[ "$(grep -c '^# >>> DNF Monitor Plus' $CONF)" = 1 ] && [ "$(grep -c '^\[Report\]' $CONF)" = 1 ] && ok "reinstalar o Plus não duplica o bloco do Plus no conf" || die "bloco duplicado: $(grep -c '^# >>> DNF Monitor Plus' $CONF)"
rm -rf /etc/dnf-monitor

step "Arquivos instalados"
[ "$(stat -c %a $CONF)" = 600 ] && ok "conf com modo 600" || die "modo do conf: $(stat -c %a $CONF)"
[ "$(stat -c %a $STATE)" = 750 ] && ok "$STATE 750" || die "modo do state dir"
[ -f /usr/lib/systemd/system/dnf-monitor-plus.service ] && ok "unit em /usr/lib/systemd/system" || die "sem unit"
LIST=$(rpm -ql dnf-monitor-plus)          # (em variável: grep -q em pipeline + pipefail daria SIGPIPE)
grep -q '/usr/share/doc/dnf-monitor-plus/examples/dnf-monitor.conf' <<<"$LIST" && grep -q '/usr/share/doc/dnf-monitor-plus/docs/configuracao.md' <<<"$LIST" && grep -q '/usr/share/man/man8/dnf-monitor-plus.8' <<<"$LIST" && ok "o pacote contém o conf de exemplo, a documentação e o manual" || die "faltam arquivos no rpm"
# Imagens de contêiner RHEL usam tsflags=nodocs: a documentação não vai para o disco. Em servidores normais ela vai.
if grep -qs '^tsflags=.*nodocs' /etc/dnf/dnf.conf; then
  ok "(imagem com tsflags=nodocs: verificação dos arquivos de documentação em disco ignorada)"
else
  [ -f /usr/share/doc/dnf-monitor-plus/examples/dnf-monitor.conf ] && [ -f /usr/share/doc/dnf-monitor-plus/docs/configuracao.md ] && ok "exemplo de conf e documentação em /usr/share/doc" || die "sem doc"
fi
rpm -q --scripts dnf-monitor-plus | grep -q 'systemctl stop dnf-monitor-plus' && ok "scriptlets presentes"
rpm -q --obsoletes dnf-monitor-plus | grep -q '^dnf-monitor' && ok "rpm declara Obsoletes: dnf-monitor" || die "sem Obsoletes"

step "Configuração de exemplo (placeholders) é rejeitada com EX_CONFIG"
cat > /tmp/placeholder.conf <<'EOC'
[Telegram]
BOT_TOKEN=seu_bot_token_aqui
CHAT_IDS=12345678, 87654321
[Email]
SMTP_SERVER=smtp.seuservidor.com
RECIPIENT_EMAILS=admin@seuservidor.com
EOC
rc=0; dnf-monitor-plus -c /tmp/placeholder.conf --check-config >/dev/null 2>&1 || rc=$?
[ "$rc" = 78 ] && ok "exit 78" || die "esperava 78, veio $rc"
cp /work/plus/packaging/dnf-monitor.conf /tmp/example.conf      # o mesmo arquivo que o pacote instala como modelo
rc=0; dnf-monitor-plus -c /tmp/example.conf --check-config >/dev/null 2>&1 || rc=$?
[ "$rc" = 78 ] && ok "o conf de exemplo instalado também é rejeitado (nada é enviado até configurar)" || die "exemplo: $rc"
sed -i 's/^BOT_TOKEN=.*/BOT_TOKEN=123456789:AAEhBP0av28dEXAMPLEexampleTOKEN_xyz12/; s/^CHAT_IDS=.*/CHAT_IDS=1/' /tmp/example.conf
OUT=$(dnf-monitor-plus -c /tmp/example.conf --check-config 2>&1)
grep -qi 'cliente não configurado' <<<"$OUT" && die "seções do cliente vazias geraram aviso: $OUT" || ok "seções do cliente vazias (tudo comentado) não geram aviso"
grep -q 'Relatório ao cliente: desativado' <<<"$OUT" && ok "--check-config: cliente desativado" || die "check-config do cliente: $OUT"

step "Configuração real apontando para Telegram/SMTP falsos"
# Em container o needs-restarting real sempre acusa "reboot" (boot do host): usa um stub neutro.
printf '#!/bin/sh\nexit 0\n' > /usr/local/bin/needs-restarting; chmod +x /usr/local/bin/needs-restarting
python3 /work/plus/tests/docker/mock_servers.py & MOCK=$!
cat > $CONF <<'EOC'
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
rm -f $STATE/state.json
sleep 1
dnf-monitor-plus --check-config >/dev/null && ok "--check-config"
dnf-monitor-plus --dry-run 2>&1 | grep -q 'kernel-core' && ok "--dry-run mostra o alerta"
[ ! -e $STATE/state.json ] && ok "--dry-run não grava estado" || die "dry-run gravou estado"

step "Ciclo 1: solver real do DNF (kernel installonly, segurança, quebrado e excluído ignorados)"
dnf-monitor-plus --once; settle
expect "2 mensagens Telegram (2 chats)" "len(d['telegram'])==2"
expect "Telegram em HTML, URL com prefixo bot, CRÍTICO (kernel-core veio de install_set)" "'/bot123456789:' in d['telegram'][0]['path'] and 'CRÍTICO' in d['telegram'][0]['body']['text'] and d['telegram'][0]['body']['parse_mode']=='HTML'"
expect "lista tem demo-a, demo-sec, kernel-core, kernel-headers" "all(x in d['email'][0]['text/plain'] for x in ('demo-a','demo-sec','kernel-core','kernel-headers'))"
expect "demo-broken (dependência quebrada), demo-excl (exclude) e demo-lock (versionlock) NÃO aparecem" "all(x not in d['email'][0]['text/plain'] for x in ('demo-broken','demo-excl','demo-lock'))"
expect "e-mail multipart com Date/Message-ID" "len(d['email'])==1 and 'text/plain' in d['email'][0] and 'text/html' in d['email'][0] and d['email'][0]['date'] and d['email'][0]['message_id']"
expect "advisory de segurança detectada (só demo-sec)" "'demo-sec (1.0-1 -> 2.0-1) [segurança]' in d['email'][0]['text/plain'] and '(1 de segurança)' in d['email'][0]['text/plain']"
expect "assunto crítico" "'Kernel' in d['email'][0]['subject']"
expect "e-mail novo do Plus: cartões, selos, comando do dnf e marca" "all(x in d['email'][0]['text/html'] for x in ('CRÍTICO','Kernel','sudo dnf upgrade --refresh','DNF&nbsp;MONITOR&nbsp;<span','Detectado em'))"
expect "e-mail sem imagens/recursos externos" "'<img' not in d['email'][0]['text/html'] and 'http://' not in d['email'][0]['text/html'].replace('http://www.w3.org','')"
[ "$(stat -c %a $STATE/state.json)" = 600 ] && ok "state.json criado (600)"

step "Ciclo 2 e simulação de restart: nada novo => nenhum alerta repetido"
dnf-monitor-plus --once; dnf-monitor-plus --once; settle
expect "continua 2 Telegram + 1 e-mail" "len(d['telegram'])==2 and len(d['email'])==1"

step "Plugins do DNF: versionlock só vale com LOAD_DNF_PLUGINS=true"
sed 's/^\[Monitor\]/[Monitor]\nLOAD_DNF_PLUGINS=false/' $CONF > /tmp/noplug.conf
dnf-monitor-plus -c /tmp/noplug.conf --dry-run 2>&1 | grep -q 'demo-lock' && ok "sem plugins o pacote travado apareceria (confirma a necessidade)" || die "esperava demo-lock sem plugins"

step "Teste de envio (--test-notify)"
dnf-monitor-plus --test-notify >/dev/null; settle
expect "teste chegou nos dois canais" "len(d['telegram'])==4 and len(d['email'])==2 and 'Teste' in d['email'][1]['subject']"

step "Repositório fora do ar"
printf '[quebrado]\nname=quebrado\nbaseurl=file:///nao/existe\nenabled=1\ngpgcheck=0\nmetadata_expire=0\n' > /etc/yum.repos.d/quebrado.repo
dnf-monitor-plus --once >/tmp/skip.log 2>&1 && ok "SKIP_BROKEN_REPOS=true: monitor segue funcionando" || { cat /tmp/skip.log; die "ciclo falhou com repo quebrado"; }
sed -i 's/^UPDATE_CACHE=true/UPDATE_CACHE=true\nSKIP_BROKEN_REPOS=false/' $CONF
rc=0; dnf-monitor-plus --once >/tmp/strict.log 2>&1 || rc=$?
[ "$rc" = 1 ] && grep -q "Falha ao consultar o DNF" /tmp/strict.log && ok "SKIP_BROKEN_REPOS=false: falha do dnf => ciclo ignorado (exit 1), sem 'tudo em dia'" || { cat /tmp/strict.log; die "esperava falha, rc=$rc"; }
expect "e nada foi enviado nem resetado" "len(d['telegram'])==4"
rm -f /etc/yum.repos.d/quebrado.repo; sed -i '/^SKIP_BROKEN_REPOS=false/d' $CONF

step "Instala tudo => aviso de 'servidor em dia'"
dnf -y -q upgrade --nobest --skip-broken >/dev/null 2>&1
dnf-monitor-plus --once; settle
expect "alerta 'em dia' enviado (broken/excluído continuam ignorados)" "len(d['telegram'])==6 and 'em dia' in d['telegram'][5]['body']['text'] and 'Todas' in d['email'][2]['subject']"
dnf-monitor-plus --once; settle
expect "e não repete" "len(d['telegram'])==6"

step "Reinicialização pendente (needs-restarting)"
printf '#!/bin/sh\necho "Core libraries or services have been updated since boot-up:"\necho "  * kernel"\necho "Reboot is required to fully utilize these updates."\nexit 1\n' > /usr/local/bin/needs-restarting; chmod +x /usr/local/bin/needs-restarting
dnf-monitor-plus --once; dnf-monitor-plus --once; settle
expect "um único alerta de reboot" "len(d['telegram'])==8 and 'Reinicialização' in d['telegram'][7]['body']['text'] and 'kernel' in d['telegram'][7]['body']['text']"
rm -f /usr/local/bin/needs-restarting

step "Falha de entrega: alerta é retentado, não perdido"
rm -f $STATE/state.json
sed -i 's#^API_BASE=.*#API_BASE=http://127.0.0.1:9#; /^\[Email\]/,$d' $CONF
mkrpm demo-a 3.0 /srv/upd; createrepo_c -q --update /srv/upd >/dev/null
rc=0; dnf-monitor-plus --once >/tmp/fail.log 2>&1 || rc=$?
[ "$rc" = 1 ] && ok "exit 1 quando nenhum canal entrega" || die "rc=$rc"
if grep -q 'AAEhBP0av28d' /tmp/fail.log; then die "TOKEN VAZOU NO LOG"; else ok "token ausente do log mesmo com erro de conexão"; fi
[ "$(python3 -c "import json;print(json.load(open('$STATE/state.json'))['packages'])")" = "None" ] && ok "estado não marcou como notificado" || die "estado marcado indevidamente"

step "Daemon: SIGHUP recarrega, SIGTERM encerra limpo; cria o banco de histórico"
sed -i 's#^API_BASE=.*#API_BASE=http://127.0.0.1:8081#' $CONF
dnf-monitor-plus > /tmp/daemon.log 2>&1 & DPID=$!
sleep 12; kill -HUP $DPID; sleep 10
if grep -q 'Configuração recarregada' /tmp/daemon.log; then ok "SIGHUP recarregou"; else cat /tmp/daemon.log; die "sem reload"; fi
grep -q 'Iniciando DNF Monitor Plus' /tmp/daemon.log && ok "banner do Plus no log" || die "sem banner"
[ -f $STATE/history.db ] && [ "$(stat -c %a $STATE/history.db)" = 600 ] && ok "history.db criado (600)" || die "sem history.db"
kill -TERM $DPID; rc=0; wait $DPID || rc=$?
[ "$rc" = 0 ] && ok "SIGTERM => saída 0" || die "rc=$rc"

step "Cliente: configuração, teste de envio e prévia"
N0=$(num "len(d['telegram'])"); E0=$(num "len(d['email'])")
cat >> $CONF <<'EOC'

[Report]
ADMIN_NAME=Henrique Fagundes
REPORT_TIME=22:30
REPORT_TIMEZONE=America/Sao_Paulo

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
OUT=$(dnf-monitor-plus --check-config 2>&1)
grep -q 'Relatório ao cliente: telegram, email' <<<"$OUT" && ok "--check-config lista os canais do cliente" || die "check-config: $OUT"
dnf-monitor-plus --test-notify >/dev/null; settle
expect "teste do admin foi para os 2 chats do admin" "sum(1 for m in d['telegram'][$N0:] if m['body']['chat_id'] in ('111','222') and 'Teste' in m['body']['text'])==2"
expect "teste do cliente: relatório de exemplo, outro bot, outro chat" "any(m['body']['chat_id']=='999' and '/bot987654321:' in m['path'] and 'Teste do relatório diário' in m['body']['text'] for m in d['telegram'][$N0:])"
expect "teste do cliente por e-mail, só para o cliente" "any(e['to']=='cliente@example.com' and 'TESTE' in e['subject'] and 'Mensagem de teste' in e['text/html'] for e in d['email'][$E0:])"
expect "o cliente NUNCA recebe o alerta do admin" "all('CRÍTICO' not in m['body']['text'] and 'Alerta' not in m['body']['text'] for m in d['telegram'][$N0:] if m['body']['chat_id']=='999')"
expect "e-mail do cliente sem IPs" "all('IPs' not in e['text/plain'] for e in d['email'][$E0:] if e['to']=='cliente@example.com')"
OUT=$(dnf-monitor-plus --report --dry-run 2>&1)
grep -q 'Relatório diário\|Nenhuma atualização\|Atualizações pendentes' <<<"$OUT" && ok "--report --dry-run pré-visualiza" || die "dry-run do relatório: $OUT"

step "Relatório real: transações REAIS do DNF (upgrade manual + kernel novo + dnf-automatic)"
sleep 2     # o banco só registra o que acontece DEPOIS de criado
mkrpm kernel-core 3.0 /srv/upd 'installonlypkg(kernel)'; mkrpm demo-auto 2.0 /srv/upd; createrepo_c -q --update /srv/upd >/dev/null
dnf -y upgrade demo-a kernel-core >/tmp/manual.log 2>&1 || { cat /tmp/manual.log; die "upgrade manual falhou"; }
[ "$(rpm -q kernel-core | wc -l)" -ge 2 ] && ok "o DNF instalou o kernel novo ao lado do antigo (installonly)" || die "kernel não ficou em paralelo"
sed -i 's/^apply_updates.*/apply_updates = yes/; s/^upgrade_type.*/upgrade_type = default/' /etc/dnf/automatic.conf
sed -i 's/^\[base\]/[base]\nbest=False\nstrict=False\nskip_broken=True/' /etc/dnf/automatic.conf     # tolera o demo-broken, de propósito
dnf-automatic /etc/dnf/automatic.conf --timer >/tmp/auto.log 2>&1 || true
[ "$(rpm -q demo-auto)" = "demo-auto-2.0-1.noarch" ] && ok "o dnf-automatic atualizou o demo-auto (transação sem linha de comando)" || { cat /tmp/auto.log; die "dnf-automatic não atualizou"; }
python3 -c "
import sqlite3
db = sqlite3.connect('file:/var/lib/dnf/history.sqlite?mode=ro', uri=True)
rows = db.execute(\"select cmdline from trans order by id desc limit 2\").fetchall()
assert rows[0][0] == '' and rows[1][0] != '', rows
print('  (a última transação, do dnf-automatic, tem cmdline vazio:', rows[0], ')')"
N1=$(num "len(d['telegram'])"); E1=$(num "len(d['email'])")
dnf-monitor-plus --report; settle
expect "Telegram ao cliente: administrador, 1 atualização, 2 pacotes com sucesso" "any(m['body']['chat_id']=='999' and 'O administrador Henrique Fagundes atualizou o servidor 1 vez' in m['body']['text'] and 'Pacotes atualizados: <b>2</b>' in m['body']['text'] and 'Com sucesso: <b>2</b>' in m['body']['text'] and 'Com falha: <b>0</b>' in m['body']['text'] for m in d['telegram'][$N1:])"
expect "e-mail ao cliente: demo-a e kernel-core (kernel novo conta), SEM o demo-auto do dnf-automatic" "any(e['to']=='cliente@example.com' and 'Relatório diário' in e['subject'] and 'demo-a' in e['text/html'] and 'kernel-core' in e['text/html'] and 'demo-auto' not in e['text/html'] and 'Suporte Técnico' in e['from'] for e in d['email'][$E1:])"
expect "nada foi para os chats dos administradores" "all(m['body']['chat_id']=='999' for m in d['telegram'][$N1:])"
N2=$(num "len(d['telegram'])")
dnf-monitor-plus --report >/tmp/again.log 2>&1; settle
[ "$(num "len(d['telegram'])")" = "$N2" ] && grep -q 'Não há atualizações novas' /tmp/again.log && ok "sem novidade, nada é reenviado" || { cat /tmp/again.log; die "reenviou"; }

step "Pendência antiga (mais de 3 dias): relatório de pendências, sem citar nomes"
python3 -c "
import sqlite3, time
db = sqlite3.connect('$STATE/history.db')
db.execute(\"insert or replace into pending values ('demo-antigo','1.0','2.0',1,?)\", (int(time.time()) - 5*86400,))
db.execute(\"insert or replace into pending values ('demo-recente','1.0','2.0',0,?)\", (int(time.time()) - 86400,))
db.commit()"
N3=$(num "len(d['telegram'])"); E3=$(num "len(d['email'])")
dnf-monitor-plus --report; settle
expect "Telegram: pendências com idade e a sugestão" "any(m['body']['chat_id']=='999' and 'Atualizações pendentes' in m['body']['text'] and 'demo-antigo — 5 dias' in m['body']['text'] and 'demo-recente' in m['body']['text'] and 'Sugerimos entrar em contato com o administrador do servidor' in m['body']['text'] for m in d['telegram'][$N3:])"
expect "NENHUM nome do administrador no relatório de pendências" "all('Henrique' not in m['body']['text'] for m in d['telegram'][$N3:]) and all('Henrique' not in str(e) for e in d['email'][$E3:])"
expect "e-mail: assunto de atenção e tabela 'Aguardando'" "any('Atenção: atualizações pendentes' in e['subject'] and 'Aguardando' in e['text/html'] and '5 dias' in e['text/html'] for e in d['email'][$E3:])"
python3 -c "
import sqlite3
db = sqlite3.connect('$STATE/history.db'); db.execute('delete from pending'); db.commit()"
N4=$(num "len(d['telegram'])")
dnf-monitor-plus --report >/tmp/again2.log 2>&1; settle
[ "$(num "len(d['telegram'])")" = "$N4" ] && ok "sem pendência e sem novidade, nada é enviado" || die "enviou sem motivo"

step "Remoção"
kill $MOCK 2>/dev/null || true
dnf -y -q remove dnf-monitor-plus >/dev/null 2>&1
[ ! -e /usr/sbin/dnf-monitor-plus ] && [ ! -e /usr/sbin/dnf-monitor ] && [ ! -e /usr/libexec/dnf-monitor-plus ] && ok "binários removidos" || die "sobrou binário"
[ ! -e $STATE ] && [ ! -e /var/log/dnf-monitor-plus.log ] && ok "estado, histórico e log removidos" || die "estado/log restaram"
[ -f $CONF.rpmsave ] && ok "conf do administrador guardado em .rpmsave"
echo; echo "✅ $PRETTY_NAME: tudo OK"
