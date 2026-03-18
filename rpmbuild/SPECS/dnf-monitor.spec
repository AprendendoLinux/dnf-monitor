Name:           dnf-monitor
Version:        1.0
Release:        2%{?dist}
Summary:        Daemon para monitoramento de atualizações DNF com alertas.
BuildArch:      noarch
License:        GPL
Requires:       python3, python3-dnf, python3-requests

%description
Daemon independente que monitora atualizações via DNF e envia alertas para o Telegram e E-mail.

%prep
# Nenhuma preparação de código fonte necessária (sem compilação C/C++)

%build
# Nenhum build complexo necessário para Python

%install
# Cria a estrutura de diretórios falsa para o RPM copiar os arquivos
mkdir -p %{buildroot}/opt/dnf-monitor
mkdir -p %{buildroot}/etc/dnf-monitor
mkdir -p %{buildroot}/etc/systemd/system
mkdir -p %{buildroot}/etc/logrotate.d

# Instala o script Python
install -m 755 %{_sourcedir}/dnf_monitor.py %{buildroot}/opt/dnf-monitor/dnf_monitor.py

# Cria o arquivo de configuração padrão
cat <<EOF > %{buildroot}/etc/dnf-monitor/dnf-monitor.conf
[Telegram]
BOT_TOKEN=
CHAT_IDS=

[Monitor]
CHECK_INTERVAL_HOURS=4

[Email]
# SMTP_SERVER=
# SMTP_PORT=587
# SMTP_USER=
# SMTP_PASS=
# SENDER_EMAIL=
# RECIPIENT_EMAILS=
EOF

# Cria o serviço do Systemd
cat <<EOF > %{buildroot}/etc/systemd/system/dnf-monitor.service
[Unit]
Description=Daemon de Monitoramento DNF
After=network.target

[Service]
Type=simple
User=root
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 /opt/dnf-monitor/dnf_monitor.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

# Cria a configuração do Logrotate
cat <<EOF > %{buildroot}/etc/logrotate.d/dnf-monitor
/var/log/dnf-monitor.log {
    weekly
    rotate 4
    compress
    delaycompress
    missingok
    notifempty
    create 0640 root root
    postrotate
        systemctl restart dnf-monitor > /dev/null 2>&1 || true
    endscript
}
EOF

%post
# Executado APÓS a instalação
systemctl daemon-reload
systemctl enable dnf-monitor
systemctl start dnf-monitor || true

%preun
# Executado ANTES da desinstalação
if [ $1 -eq 0 ]; then
    systemctl stop dnf-monitor > /dev/null 2>&1 || true
    systemctl disable dnf-monitor > /dev/null 2>&1 || true
    rm -f /var/log/dnf-monitor.log*
fi

%files
# Define quem é dono dos arquivos e protege o .conf (equivalente ao conffiles)
%defattr(-,root,root,-)
/opt/dnf-monitor/dnf_monitor.py
/etc/systemd/system/dnf-monitor.service
/etc/logrotate.d/dnf-monitor
%config(noreplace) /etc/dnf-monitor/dnf-monitor.conf
