# Compressão xz: legível por qualquer rpm do EL8 em diante.
%global _binary_payload w19.xzdio

Name:           dnf-monitor-plus
Version:        @VERSION@
Release:        1
Summary:        DNF Monitor Plus: alertas de atualização e relatório diário ao cliente
BuildArch:      noarch
License:        GPL-3.0-or-later
URL:            https://github.com/AprendendoLinux/dnf-monitor
Source0:        dnf_monitor.py
Source1:        dnf-monitor.conf
Source2:        dnf-monitor.service
Source3:        dnf-monitor.logrotate
Source4:        dnf-monitor.8
Source5:        README.md
Source6:        CHANGELOG.md
Source7:        LICENSE

Requires:       python3 >= 3.6
Requires:       python3-dnf
Requires:       tzdata
Recommends:     dnf-utils
Recommends:     iproute
# O Plus substitui o dnf-monitor (e migra a configuração e o estado dele no %post).
Obsoletes:      dnf-monitor

%description
Edição Plus do DNF Monitor, para RHEL e derivados (Rocky, AlmaLinux, Oracle
Linux, CentOS Stream). Avisa os administradores, na hora, por Telegram e/ou
e-mail (HTML) quando há atualizações pendentes, destacando kernel e segurança.
Envia ao cliente um relatório diário do que o administrador atualizou, com o
total de pacotes (sucesso e falha) e aviso de pendências antigas. Substitui o
pacote dnf-monitor e migra a configuração dele.

%prep
cp -p %{SOURCE5} %{SOURCE6} %{SOURCE7} .

%build

%install
install -D -m 0755 %{SOURCE0} %{buildroot}%{_libexecdir}/dnf-monitor-plus/dnf_monitor.py
install -d -m 0755 %{buildroot}%{_sbindir}
ln -s ../libexec/dnf-monitor-plus/dnf_monitor.py %{buildroot}%{_sbindir}/dnf-monitor-plus
ln -s ../libexec/dnf-monitor-plus/dnf_monitor.py %{buildroot}%{_sbindir}/dnf-monitor
install -D -m 0600 %{SOURCE1} %{buildroot}%{_sysconfdir}/dnf-monitor-plus/dnf-monitor.conf
install -D -m 0644 %{SOURCE2} %{buildroot}/usr/lib/systemd/system/dnf-monitor-plus.service
install -D -m 0644 %{SOURCE3} %{buildroot}%{_sysconfdir}/logrotate.d/dnf-monitor-plus
install -D -m 0644 %{SOURCE4} %{buildroot}%{_mandir}/man8/dnf-monitor-plus.8
install -d -m 0750 %{buildroot}%{_localstatedir}/lib/dnf-monitor-plus
install -D -m 0644 %{SOURCE1} %{buildroot}%{_docdir}/%{name}/examples/dnf-monitor.conf
cp -a %{_sourcedir}/docs %{buildroot}%{_docdir}/%{name}/docs

%post
CONF=%{_sysconfdir}/dnf-monitor-plus/dnf-monitor.conf
OLD_CONF=%{_sysconfdir}/dnf-monitor/dnf-monitor.conf
OLD_STATE=%{_localstatedir}/lib/dnf-monitor/state.json
NEW_STATE=%{_localstatedir}/lib/dnf-monitor-plus/state.json
if [ "$1" -eq 1 ]; then
    # Primeira instalação do Plus (inclusive a troca vinda do dnf-monitor). Neste momento o pacote antigo ainda está
    # instalado (o rpm só o remove depois), então a configuração e o estado dele estão acessíveis.
    if [ -f "$OLD_CONF" ] && grep -q 'seu_bot_token_aqui' "$CONF" 2>/dev/null; then
        tmp="$CONF.migrando"
        {
            cat "$OLD_CONF"
            printf '\n\n'
            # Acrescenta a parte do Plus (seções do cliente), toda comentada, a partir do marcador
            # (a menos que o .conf antigo já a tenha, como num rollback seguido de nova instalação do Plus).
            if ! grep -q '^# >>> DNF Monitor Plus >>>' "$OLD_CONF"; then
                sed -n '/^# >>> DNF Monitor Plus >>>/,$p' "$CONF"
            fi
        } > "$tmp"
        chmod 0600 "$tmp"
        mv "$tmp" "$CONF"
        echo "dnf-monitor-plus: configuração do dnf-monitor migrada para $CONF." >&2
    fi
    if [ -f "$OLD_STATE" ] && [ ! -e "$NEW_STATE" ]; then
        cp -p "$OLD_STATE" "$NEW_STATE"      # evita reenviar alertas já avisados
        chmod 0600 "$NEW_STATE"
        echo "dnf-monitor-plus: estado anterior migrado (alertas já enviados não se repetem)." >&2
    fi
fi
# O arquivo guarda token do bot e senha SMTP: só root deve ler.
chmod 0600 "$CONF" 2>/dev/null || :
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload >/dev/null 2>&1 || :
    # Instalação nova: habilita. (Upgrades respeitam a escolha do administrador.)
    if [ "$1" -eq 1 ]; then
        systemctl enable dnf-monitor-plus.service >/dev/null 2>&1 || :
    fi
    if %{_libexecdir}/dnf-monitor-plus/dnf_monitor.py --check-config >/dev/null 2>&1; then
        if [ "$1" -eq 1 ]; then
            systemctl start dnf-monitor-plus.service >/dev/null 2>&1 || :
        else
            systemctl try-restart dnf-monitor-plus.service >/dev/null 2>&1 || :
        fi
    else
        echo "dnf-monitor-plus: configuração ainda não preenchida; o serviço NÃO foi iniciado." >&2
        echo "dnf-monitor-plus: edite $CONF, valide com 'sudo dnf-monitor-plus --check-config'," >&2
        echo "dnf-monitor-plus: teste com 'sudo dnf-monitor-plus --test-notify' e inicie com" >&2
        echo "dnf-monitor-plus: 'sudo systemctl restart dnf-monitor-plus'." >&2
    fi
fi
exit 0

%preun
if [ "$1" -eq 0 ] && [ -d /run/systemd/system ]; then
    systemctl stop dnf-monitor-plus.service >/dev/null 2>&1 || :
    systemctl disable dnf-monitor-plus.service >/dev/null 2>&1 || :
fi
exit 0

%postun
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload >/dev/null 2>&1 || :
fi
if [ "$1" -eq 0 ]; then
    rm -rf %{_localstatedir}/lib/dnf-monitor-plus
    rm -f %{_localstatedir}/log/dnf-monitor-plus.log %{_localstatedir}/log/dnf-monitor-plus.log-*
fi
exit 0

%posttrans
# A versão 1.0 do dnf-monitor instalava em /opt/dnf-monitor (diretório que ela não declarava como seu): limpa o resto vazio
# que fica quando o Plus a substitui.
rmdir /opt/dnf-monitor 2>/dev/null || :

%files
%dir %{_libexecdir}/dnf-monitor-plus
%attr(0755,root,root) %{_libexecdir}/dnf-monitor-plus/dnf_monitor.py
%{_sbindir}/dnf-monitor-plus
%{_sbindir}/dnf-monitor
/usr/lib/systemd/system/dnf-monitor-plus.service
%config(noreplace) %attr(0600,root,root) %{_sysconfdir}/dnf-monitor-plus/dnf-monitor.conf
%config(noreplace) %{_sysconfdir}/logrotate.d/dnf-monitor-plus
%dir %attr(0750,root,root) %{_localstatedir}/lib/dnf-monitor-plus
%{_mandir}/man8/dnf-monitor-plus.8*
%license LICENSE
%doc README.md CHANGELOG.md
%{_docdir}/%{name}/examples
%{_docdir}/%{name}/docs

%changelog
* Wed Nov 25 2026 Luiz Henrique Marques Fagundes <henrique@henrique.tec.br> - @VERSION@-1
- Primeira versão da edição Plus: alerta imediato, relatório diário ao cliente (Telegram e e-mail), pendências antigas, histórico em SQLite e migração automática a partir do dnf-monitor (veja CHANGELOG.md).
