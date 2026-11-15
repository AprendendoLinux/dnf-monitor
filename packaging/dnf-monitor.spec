# Compressão xz: legível por qualquer rpm do EL8 em diante.
%global _binary_payload w19.xzdio

Name:           dnf-monitor
Version:        @VERSION@
Release:        1
Summary:        Daemon de monitoramento de atualizações DNF com alertas
BuildArch:      noarch
# Licença herdada do spec anterior (ainda sem arquivo LICENSE no repositório).
License:        GPL
URL:            https://github.com/AprendendoLinux/dnf-monitor
Source0:        dnf_monitor.py
Source1:        dnf-monitor.conf
Source2:        dnf-monitor.service
Source3:        dnf-monitor.logrotate
Source4:        dnf-monitor.8
Source5:        README.md
Source6:        CHANGELOG.md

Requires:       python3 >= 3.6
Requires:       python3-dnf
Recommends:     dnf-utils
Recommends:     iproute

%description
Daemon leve que verifica periodicamente se há atualizações pendentes em
sistemas RHEL e derivados (Rocky, AlmaLinux, Oracle Linux, CentOS Stream) e
avisa por Telegram e/ou e-mail (HTML). Usa o solver do DNF, destaca
atualizações de kernel e de segurança, avisa quando é preciso reiniciar e não
repete alertas já enviados.

%prep
cp -p %{SOURCE5} %{SOURCE6} .

%build

%install
install -D -m 0755 %{SOURCE0} %{buildroot}%{_libexecdir}/dnf-monitor/dnf_monitor.py
install -d -m 0755 %{buildroot}%{_sbindir}
ln -s ../libexec/dnf-monitor/dnf_monitor.py %{buildroot}%{_sbindir}/dnf-monitor
install -D -m 0600 %{SOURCE1} %{buildroot}%{_sysconfdir}/dnf-monitor/dnf-monitor.conf
install -D -m 0644 %{SOURCE2} %{buildroot}/usr/lib/systemd/system/dnf-monitor.service
install -D -m 0644 %{SOURCE3} %{buildroot}%{_sysconfdir}/logrotate.d/dnf-monitor
install -D -m 0644 %{SOURCE4} %{buildroot}%{_mandir}/man8/dnf-monitor.8
install -d -m 0750 %{buildroot}%{_localstatedir}/lib/dnf-monitor

%post
# O arquivo guarda token do bot e senha SMTP: só root deve ler (inclusive em upgrades da 1.0, que o deixava 0644).
chmod 0600 %{_sysconfdir}/dnf-monitor/dnf-monitor.conf 2>/dev/null || :
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload >/dev/null 2>&1 || :
    # Instalação nova: habilita. (Upgrades respeitam a escolha do administrador.)
    if [ "$1" -eq 1 ]; then
        systemctl enable dnf-monitor.service >/dev/null 2>&1 || :
    fi
    if %{_libexecdir}/dnf-monitor/dnf_monitor.py --check-config >/dev/null 2>&1; then
        if [ "$1" -eq 1 ]; then
            systemctl start dnf-monitor.service >/dev/null 2>&1 || :
        else
            systemctl try-restart dnf-monitor.service >/dev/null 2>&1 || :
        fi
    else
        echo "dnf-monitor: configuração ainda não preenchida; o serviço NÃO foi iniciado." >&2
        echo "dnf-monitor: edite /etc/dnf-monitor/dnf-monitor.conf, teste com 'sudo dnf-monitor --test-notify'" >&2
        echo "dnf-monitor: e inicie com 'sudo systemctl restart dnf-monitor'." >&2
    fi
fi
exit 0

%preun
if [ "$1" -eq 0 ] && [ -d /run/systemd/system ]; then
    systemctl stop dnf-monitor.service >/dev/null 2>&1 || :
    systemctl disable dnf-monitor.service >/dev/null 2>&1 || :
fi
exit 0

%postun
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload >/dev/null 2>&1 || :
fi
if [ "$1" -eq 0 ]; then
    rm -rf %{_localstatedir}/lib/dnf-monitor
    rm -f %{_localstatedir}/log/dnf-monitor.log %{_localstatedir}/log/dnf-monitor.log-*
fi
exit 0

%posttrans
# A 1.0 instalava em /opt/dnf-monitor (diretório que ela não declarava como seu): limpa o resto vazio.
rmdir /opt/dnf-monitor 2>/dev/null || :

%files
%dir %{_libexecdir}/dnf-monitor
%attr(0755,root,root) %{_libexecdir}/dnf-monitor/dnf_monitor.py
%{_sbindir}/dnf-monitor
/usr/lib/systemd/system/dnf-monitor.service
%config(noreplace) %attr(0600,root,root) %{_sysconfdir}/dnf-monitor/dnf-monitor.conf
%config(noreplace) %{_sysconfdir}/logrotate.d/dnf-monitor
%dir %attr(0750,root,root) %{_localstatedir}/lib/dnf-monitor
%{_mandir}/man8/dnf-monitor.8*
%doc README.md CHANGELOG.md

%changelog
* Sat Nov 14 2026 Luiz Henrique Marques Fagundes <henrique@henrique.tec.br> - @VERSION@-1
- Reescrita: estado persistente, segurança, kernel/segurança/reboot, testes e documentação (veja CHANGELOG.md).

* Sun Aug 30 2026 Luiz Henrique Marques Fagundes <henrique@henrique.tec.br> - 1.0-6
- Fix: Implementado o solver nativo do DNF (base.upgrade_all() e base.resolve()) para ignorar corretamente pacotes retidos por quebra de dependências.

* Thu Aug 13 2026 Luiz Henrique Marques Fagundes <henrique@henrique.tec.br> - 1.0-5
- Correcao de pacotes duplicados nas notificacoes (Telegram e E-mail) utilizando set().
