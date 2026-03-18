Name:           dnf-monitor
Version:        1.0
Release:        4%{?dist}
Summary:        Daemon para monitoramento de atualizações DNF com alertas.
BuildArch:      noarch
License:        GPL
Requires:       python3, python3-dnf, python3-requests

%description
Daemon independente que monitora atualizações via DNF e envia alertas para o Telegram e E-mail em formato HTML.

%prep

%build

%install
mkdir -p %{buildroot}/opt/dnf-monitor
mkdir -p %{buildroot}/etc/dnf-monitor
mkdir -p %{buildroot}/etc/systemd/system
mkdir -p %{buildroot}/etc/logrotate.d

install -m 755 %{_sourcedir}/dnf_monitor.py %{buildroot}/opt/dnf-monitor/dnf_monitor.py

cat <<EOF > %{buildroot}/etc/dnf-monitor/dnf-monitor.conf
[Telegram]
BOT_TOKEN=
CHAT_IDS=

[Monitor]
CHECK_INTERVAL_HOURS=4

[Email]
# SMTP_SERVER=smtp.seuservidor.com
# SMTP_PORT=587
# SMTP_USER=
# SMTP_PASS=
# SENDER_NAME=DNF Monitor
# SENDER_EMAIL=
# RECIPIENT_EMAILS=
EOF

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
systemctl daemon-reload
systemctl enable dnf-monitor
systemctl start dnf-monitor || true

%preun
if [ \$1 -eq 0 ]; then
    systemctl stop dnf-monitor > /dev/null 2>&1 || true
    systemctl disable dnf-monitor > /dev/null 2>&1 || true
    rm -f /var/log/dnf-monitor.log*
fi

%files
%defattr(-,root,root,-)
/opt/dnf-monitor/dnf_monitor.py
/etc/systemd/system/dnf-monitor.service
/etc/logrotate.d/dnf-monitor
%config(noreplace) /etc/dnf-monitor/dnf-monitor.conf
