"""Gera HTML de exemplo de todos os e-mails (data fixa, para capturas comparáveis): gen.py PASTA_DE_SAIDA"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))
import dnf_monitor as am  # noqa: E402

out = sys.argv[1]
now = 1795000000
U, P, R, PP = am.Update, am.PackageResult, am.RunRecord, am.PendingPackage
tz = "America/Sao_Paulo"
upd = [U("kernel-core", "5.14.0-503.el9", "5.14.0-570.el9", True), U("openssl", "3.2.2-6.el9", "3.2.2-6.el9_5.1", True),
       U("openssl-libs", "3.2.2-6.el9", "3.2.2-6.el9_5.1", True), U("curl", "7.76.1-29.el9", "7.76.1-31.el9", False),
       U("nginx", "1.22.1-4.el9", "1.22.1-6.el9", False), U("tzdata", "2024a-1.el9", "2024b-1.el9", False)]
runs = [R(1, now - 9 * 3600, None, 12, 0, [P("openssl", "3.2.2-6.el9", "3.2.2-6.el9_5.1", True),
                                            P("curl", "7.76.1-29.el9", "7.76.1-31.el9", True),
                                            P("kernel-core", "5.14.0-503.el9", "5.14.0-570.el9", True)]),
        R(2, now - 2 * 3600, None, 3, 1, [P("nginx", "1.22.1-4.el9", "1.22.1-6.el9", False),
                                           P("tzdata", "2024a-1.el9", "2024b-1.el9", True),
                                           P("vim", "8.2.2637-20.el9", "8.2.2637-21.el9", True)])]
pend = [PP("openssl", "3.2.2-6.el9", "3.2.2-6.el9_5.1", True, now - 6 * 86400), PP("openssl-libs", "3.2.2-6.el9", "3.2.2-6.el9_5.1", True, now - 6 * 86400),
        PP("kernel-core", "5.14.0-503.el9", "5.14.0-570.el9", True, now - 5 * 86400), PP("curl", "7.76.1-29.el9", "7.76.1-31.el9", False, now - 4 * 86400),
        PP("vim", "8.2.2637-20.el9", "8.2.2637-21.el9", False, now - 86400)]
host, ips, day = "srv-prod", "192.168.1.50", "2026-11-24"
pages = {
    "admin-critico": am.Alert("new", host, ips + ", 2001:db8::50", upd, True, ["kernel-core"], now, tz).email_html(),
    "admin-comum": am.Alert("new", host, ips, upd[3:5], False, [], now, tz).email_html(),
    "admin-lembrete": am.Alert("reminder", host, ips, upd[1:3], False, [], now, tz).email_html(),
    "admin-em-dia": am.Alert("cleared", host, ips, [], False, [], now, tz).email_html(),
    "cliente": am.DailyReport(host, ips, day, runs, "Henrique Fagundes", tz, pending=4, reboot=True, now=now).email_html(),
    "cliente-vazio": am.DailyReport(host, ips, day, [], "", tz, now=now).email_html(),
    "cliente-pendencias": am.DailyReport(host, ips, day, [], "Henrique Fagundes", tz, overdue=pend, overdue_days=3, now=now).email_html(),
    "cliente-misto": am.DailyReport(host, ips, day, runs, "Henrique Fagundes", tz, overdue=pend, overdue_days=3, now=now).email_html(),
}
for name, page in pages.items():
    with open(os.path.join(out, name + ".html"), "w", encoding="utf-8") as handle:
        handle.write(page)
print(" ".join(sorted(pages)))
