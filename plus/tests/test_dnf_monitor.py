"""Testes unitários (somente biblioteca padrão): python3 -m unittest discover -s tests -v"""

import base64
import calendar
import http.server
import io
import json
import logging
import os
import re
import socketserver
import sqlite3
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import dnf_monitor as am  # noqa: E402

TOKEN = "123456789:AAEhBP0av28dEXAMPLEexampleTOKEN_xyz12"


def write_conf(directory, body):
    path = os.path.join(directory, "dnf-monitor.conf")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    return path


def upd(name, new="2", old="1", sec=False):
    return am.Update(name, old, new, sec)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def conf(self, body):
        return am.load_config(write_conf(self.tmp.name, body))

    def test_inline_comment_and_percent_password(self):
        cfg = self.conf("[Email]\nSMTP_SERVER=mail.example.com\nSMTP_PORT=587 # STARTTLS\n"
                        "SMTP_USER=u@example.com\nSMTP_PASS=pa%ss%d\nRECIPIENT_EMAILS=a@example.com, b@example.com\n")
        self.assertEqual(cfg.email["port"], 587)
        self.assertEqual(cfg.email["password"], "pa%ss%d")
        self.assertEqual(cfg.email["recipients"], ["a@example.com", "b@example.com"])
        self.assertEqual(cfg.email["sender_email"], "u@example.com")

    def test_token_with_and_without_bot_prefix(self):
        for raw in (TOKEN, "bot" + TOKEN):
            cfg = self.conf("[Telegram]\nBOT_TOKEN=%s\nCHAT_IDS=1, 2\n" % raw)
            self.assertEqual(cfg.bot_token, TOKEN)
            self.assertEqual(cfg.chat_ids, ["1", "2"])

    def test_placeholders_are_disabled_and_no_channel_is_fatal(self):
        with self.assertRaises(am.ConfigProblem):
            self.conf("[Telegram]\nBOT_TOKEN=seu_bot_token_aqui\nCHAT_IDS=12345678\n"
                      "[Email]\nSMTP_SERVER=smtp.seuservidor.com\nRECIPIENT_EMAILS=admin@seuservidor.com\n")

    def test_telegram_only_is_valid(self):
        cfg = self.conf("[Telegram]\nBOT_TOKEN=%s\nCHAT_IDS=1\n[Email]\nSMTP_SERVER=\n" % TOKEN)
        self.assertEqual(cfg.channels(), ["telegram"])

    def test_defaults(self):
        cfg = self.conf("[Telegram]\nBOT_TOKEN=%s\nCHAT_IDS=1\n" % TOKEN)
        self.assertEqual(cfg.interval_seconds, 4 * 3600)
        self.assertEqual(cfg.reminder_days, 0)
        self.assertFalse(cfg.notify_on_clear)

    def test_invalid_values(self):
        for body in ("[Monitor]\nCHECK_INTERVAL_HOURS=abc\n", "[Monitor]\nCHECK_INTERVAL_HOURS=0\n",
                     "[Email]\nSMTP_SERVER=m.example.com\nSMTP_PORT=x\nRECIPIENT_EMAILS=a@b.com\nSENDER_EMAIL=a@b.com\n",
                     "[Email]\nSMTP_SERVER=m.example.com\nSMTP_SECURITY=foo\nRECIPIENT_EMAILS=a@b.com\nSENDER_EMAIL=a@b.com\n"):
            with self.assertRaises(am.ConfigProblem, msg=body):
                self.conf("[Telegram]\nBOT_TOKEN=%s\nCHAT_IDS=1\n" % TOKEN + body)

    def test_missing_file(self):
        with self.assertRaises(am.ConfigProblem):
            am.load_config(os.path.join(self.tmp.name, "nope.conf"))


class RedactionTests(unittest.TestCase):
    def test_token_and_password_are_redacted(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(am.REDACTOR)
        logger = logging.getLogger("redact-test")
        logger.addHandler(handler)
        logger.propagate = False
        am.REDACTOR.add("hunter2-secret")
        logger.error("erro em https://api.telegram.org/bot%s/sendMessage: %s", TOKEN, "senha hunter2-secret")
        out = stream.getvalue()
        self.assertNotIn(TOKEN, out)
        self.assertNotIn("AAEhBP0av28d", out)
        self.assertNotIn("hunter2-secret", out)


class DecideTests(unittest.TestCase):
    def test_flow(self):
        state = am.State(None)
        a, b = upd("a"), upd("b")
        self.assertEqual(am.decide(state, [], 0), "sync")           # primeira avaliação, nada pendente
        state.packages = set()
        self.assertIsNone(am.decide(state, [], 0))
        self.assertEqual(am.decide(state, [a], 0), "new")
        state.packages = am.package_keys([a])
        self.assertIsNone(am.decide(state, [a], 10))                # nada novo
        self.assertEqual(am.decide(state, [a, b], 10), "new")       # surgiu pacote
        self.assertEqual(am.decide(state, [upd("a", "3")], 10), "new")  # nova versão
        state.packages = am.package_keys([a, b])
        self.assertEqual(am.decide(state, [a], 10), "sync")         # admin instalou parte: sem alerta
        self.assertEqual(am.decide(state, [], 10), "cleared")

    def test_same_count_different_packages_alerts(self):
        # bug da v1.0: contagem igual (1 -> 1) escondia um pacote novo
        state = am.State(None)
        state.packages = am.package_keys([upd("a")])
        self.assertEqual(am.decide(state, [upd("b")], 0), "new")

    def test_reminder(self):
        state = am.State(None)
        state.packages = am.package_keys([upd("a")])
        state.last_notified = 1000
        self.assertIsNone(am.decide(state, [upd("a")], 1000 + 86400, reminder_days=0))
        self.assertIsNone(am.decide(state, [upd("a")], 1000 + 86399, reminder_days=1))
        self.assertEqual(am.decide(state, [upd("a")], 1000 + 86400, reminder_days=1), "reminder")


class StateTests(unittest.TestCase):
    def test_roundtrip_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "state.json")
            st = am.State(path)
            st.packages = {"a=1"}
            st.last_notified = 5.0
            st.reboot_notified = True
            st.save()
            self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
            loaded = am.State(path).load()
            self.assertEqual((loaded.packages, loaded.last_notified, loaded.reboot_notified), ({"a=1"}, 5.0, True))
            with open(path, "w") as handle:
                handle.write("{lixo")
            self.assertIsNone(am.State(path).load().packages)


class FakeNotifier(object):
    name = "fake"

    def __init__(self, ok=True):
        self.ok = ok
        self.alerts = []

    def send(self, alert):
        self.alerts.append(alert)
        return self.ok


class MonitorTests(unittest.TestCase):
    def make(self, updates, notifier=None, reboot=(False, []), **cfg_over):
        cfg = am.Config()
        cfg.update_cache = False
        for key, value in cfg_over.items():
            setattr(cfg, key, value)
        self.notifier = notifier or FakeNotifier()
        self.updates = updates
        self.reboot = reboot
        self.now = 1000.0
        self.state = am.State(None)
        return am.Monitor(cfg, [self.notifier], self.state, collector=lambda _u: self.updates, hostname="srv",
                          ips_func=lambda: "10.0.0.1", reboot_func=lambda: self.reboot,
                          clock=lambda: self.now, persist=False)

    def test_alert_once_per_batch(self):
        mon = self.make([upd("a"), upd("b")])
        mon.run_cycle()
        mon.run_cycle()
        self.assertEqual(len(self.notifier.alerts), 1)
        self.assertEqual(self.notifier.alerts[0].kind, "new")

    def test_dnf_failure_does_not_reset_state(self):
        mon = self.make([upd("a")])
        mon.run_cycle()
        self.updates = None                      # dnf falhou
        self.assertFalse(mon.run_cycle())
        self.updates = [upd("a")]
        mon.run_cycle()
        self.assertEqual(len(self.notifier.alerts), 1)   # v1.0 reenviaria aqui

    def test_failed_delivery_is_retried(self):
        mon = self.make([upd("a")], notifier=FakeNotifier(ok=False))
        self.assertFalse(mon.run_cycle())
        self.assertIsNone(self.state.packages)
        self.notifier.ok = True
        self.assertTrue(mon.run_cycle())
        self.assertEqual(len(self.notifier.alerts), 2)
        self.assertIsNotNone(self.state.packages)
        mon.run_cycle()
        self.assertEqual(len(self.notifier.alerts), 2)

    def test_restart_with_persisted_state_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.json")
            first = self.make([upd("a")])
            first.state = am.State(path)
            first.persist = True
            first.run_cycle()
            second = self.make([upd("a")])
            second.state = am.State(path).load()
            second.run_cycle()
            self.assertEqual(len(self.notifier.alerts), 0)

    def test_cleared_notification_optional(self):
        mon = self.make([upd("a")])
        mon.run_cycle()
        self.updates = []
        mon.run_cycle()
        self.assertEqual([a.kind for a in self.notifier.alerts], ["new"])
        mon = self.make([upd("a")], notify_on_clear=True)
        mon.run_cycle()
        self.updates = []
        mon.run_cycle()
        self.assertEqual([a.kind for a in self.notifier.alerts], ["new", "cleared"])

    def test_reminder_flow(self):
        mon = self.make([upd("a")], reminder_days=1)
        mon.run_cycle()
        self.now += 3600
        mon.run_cycle()
        self.now += 86400
        mon.run_cycle()
        self.assertEqual([a.kind for a in self.notifier.alerts], ["new", "reminder"])

    def test_reboot_required_alert_once(self):
        mon = self.make([], reboot=(True, ["linux-image-6.1"]))
        mon.run_cycle()
        mon.run_cycle()
        self.assertEqual([a.kind for a in self.notifier.alerts], ["reboot"])
        self.reboot = (False, [])
        mon.run_cycle()
        self.reboot = (True, [])
        mon.run_cycle()                           # novo reboot-required depois de reiniciar: avisa de novo
        self.assertEqual(len(self.notifier.alerts), 2)

    def test_reboot_note_included_in_update_alert(self):
        mon = self.make([upd("a")], reboot=(True, []))
        mon.run_cycle()
        self.assertEqual([a.kind for a in self.notifier.alerts], ["new"])
        self.assertTrue(self.notifier.alerts[0].reboot)


class MessageTests(unittest.TestCase):
    def test_kernel_is_critical_and_html_escaped(self):
        alert = am.Alert("new", "srv<script>", "10.0.0.1", [upd("kernel-core"), upd("a&b", sec=True)], True, ["x<y"])
        self.assertTrue(alert.critical)
        text = alert.telegram_text()
        self.assertIn("CRÍTICO", text)
        self.assertIn("srv&lt;script&gt;", text)
        self.assertNotIn("<script>", text)
        self.assertIn("a&amp;b", alert.email_html())
        self.assertNotIn("<script>", alert.email_html())
        self.assertIn("segurança", alert.email_text())

    def test_headers_and_firmware_not_critical(self):
        alert = am.Alert("new", "srv", "ip", [upd("linux-firmware"), upd("kernel-headers")])
        self.assertFalse(alert.critical)
        self.assertIn("Alerta de Atualização", alert.title)

    def test_security_title_and_truncation(self):
        updates = [upd("p%d" % i, sec=(i == 0)) for i in range(8)]
        alert = am.Alert("new", "srv", "ip", updates)
        self.assertIn("segurança", alert.title)
        self.assertIn("(1 de segurança)", alert.summary_line())
        self.assertTrue(alert.telegram_text().rstrip().endswith("..."))


# ---- Telegram contra um servidor HTTP local -----------------------------

class TelegramHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
        self.server.requests.append((self.path, body))
        code, payload = self.server.responses.pop(0) if self.server.responses else (200, {"ok": True})
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.server = http.server.HTTPServer(("127.0.0.1", 0), TelegramHandler)
        self.server.requests, self.server.responses = [], []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.sleeps = []
        self.alert = am.Alert("new", "srv", "10.0.0.1", [upd("a")])

    def notifier(self, chats=("1", "2")):
        return am.TelegramNotifier(TOKEN, list(chats), self.base, sleep=self.sleeps.append)

    def test_success_uses_bot_prefix_and_html(self):
        self.assertTrue(self.notifier().send(self.alert))
        path, body = self.server.requests[0]
        self.assertEqual(path, "/bot%s/sendMessage" % TOKEN)
        self.assertEqual(body["parse_mode"], "HTML")
        self.assertEqual([r[1]["chat_id"] for r in self.server.requests], ["1", "2"])

    def test_retry_after_429_then_ok(self):
        self.server.responses = [(429, {"ok": False, "description": "Too Many", "parameters": {"retry_after": 3}})]
        self.assertTrue(self.notifier(["1"]).send(self.alert))
        self.assertEqual(self.sleeps, [3])
        self.assertEqual(len(self.server.requests), 2)

    def test_permanent_error_no_retry_and_no_token_in_log(self):
        self.server.responses = [(400, {"ok": False, "description": "chat not found"})] * 5
        am.REDACTOR.add(TOKEN)
        with self.assertLogs("dnf-monitor-plus", level="ERROR") as logs:
            ok = self.notifier(["1"]).send(self.alert)
        self.assertFalse(ok)
        self.assertEqual(len(self.server.requests), 1)
        self.assertIn("chat not found", "\n".join(logs.output))
        for record in logs.records:
            self.assertNotIn(TOKEN, record.getMessage())

    def test_one_bad_chat_does_not_block_other(self):
        self.server.responses = [(400, {"ok": False, "description": "bad"})]
        self.assertTrue(self.notifier().send(self.alert))

    def test_unreachable_server_gives_up(self):
        self.server.shutdown()
        notifier = am.TelegramNotifier(TOKEN, ["1"], self.base, sleep=self.sleeps.append)
        notifier.TIMEOUT = 2
        self.assertFalse(notifier.send(self.alert))
        self.assertEqual(len(self.sleeps), 2)       # 3 tentativas -> 2 esperas


# ---- SMTP falso ----------------------------------------------------------

class SMTPHandler(socketserver.StreamRequestHandler):
    def reply(self, line):
        self.wfile.write((line + "\r\n").encode())

    def handle(self):
        srv = self.server
        self.reply("220 fake ESMTP")
        while True:
            line = self.rfile.readline().decode().rstrip("\r\n")
            if not line:
                return
            cmd = line.split(" ")[0].upper()
            if cmd == "EHLO":
                self.wfile.write(b"250-fake\r\n250-AUTH PLAIN\r\n250 8BITMIME\r\n")
            elif cmd == "AUTH":
                srv.auth.append(base64.b64decode(line.split(" ")[2]).decode())
                self.reply("235 ok")
            elif cmd == "MAIL":
                self.reply("250 ok")
            elif cmd == "RCPT":
                rcpt = line.split("<")[1].split(">")[0]
                if rcpt.startswith("bad"):
                    self.reply("550 no such user")
                else:
                    srv.rcpts.append(rcpt)
                    self.reply("250 ok")
            elif cmd == "DATA":
                self.reply("354 go")
                data = []
                while True:
                    row = self.rfile.readline().decode()
                    if row == ".\r\n":
                        break
                    data.append(row)
                srv.messages.append("".join(data))
                self.reply("250 queued")
            elif cmd == "QUIT":
                self.reply("221 bye")
                return
            else:
                self.reply("250 ok")


class EmailTests(unittest.TestCase):
    def setUp(self):
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SMTPHandler)
        self.server.daemon_threads = True
        self.server.auth, self.server.rcpts, self.server.messages = [], [], []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.alert = am.Alert("new", "srv", "10.0.0.1", [upd("kernel-core")])

    def settings(self, **over):
        s = {"server": "127.0.0.1", "port": self.server.server_address[1], "security": "none",
             "user": "u", "password": "p%w", "sender_name": "Monitor ção", "sender_email": "a@example.com",
             "recipients": ["x@example.com", "bad@example.com", "y@example.com"]}
        s.update(over)
        return s

    def test_individual_delivery_and_resilience(self):
        ok = am.EmailNotifier(self.settings()).send(self.alert)
        self.assertTrue(ok)
        self.assertEqual(self.server.rcpts, ["x@example.com", "y@example.com"])   # 'bad' falhou, os outros foram
        self.assertEqual(self.server.auth, ["\x00u\x00p%w"])
        msg = self.server.messages[0]
        for header in ("Date:", "Message-ID:", "Subject:", "Content-Type: multipart/alternative"):
            self.assertIn(header, msg)
        self.assertIn("text/plain", msg)
        self.assertIn("text/html", msg)

    def test_all_recipients_fail(self):
        self.assertFalse(am.EmailNotifier(self.settings(recipients=["bad@example.com"])).send(self.alert))

    def test_refuses_credentials_without_tls_in_auto_mode(self):
        with self.assertLogs("dnf-monitor-plus", level="ERROR") as logs:
            ok = am.EmailNotifier(self.settings(security="auto")).send(self.alert)
        self.assertFalse(ok)
        self.assertIn("STARTTLS", "\n".join(logs.output))
        self.assertEqual(self.server.auth, [])

    def test_auto_without_credentials_allowed_for_local_relay(self):
        self.assertTrue(am.EmailNotifier(self.settings(security="auto", user="", recipients=["x@example.com"])).send(self.alert))
        self.assertEqual(self.server.auth, [])

    def test_connection_refused(self):
        self.server.shutdown()
        self.server.server_close()
        self.assertFalse(am.EmailNotifier(self.settings()).send(self.alert))


class CLITests(unittest.TestCase):
    def test_check_config_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = write_conf(tmp, "[Telegram]\nBOT_TOKEN=%s\nCHAT_IDS=1\n[Monitor]\nLOG_FILE=\n" % TOKEN)
            self.assertEqual(am.main(["-c", good, "--check-config"]), 0)
            bad = write_conf(tmp, "[Telegram]\nBOT_TOKEN=seu_bot_token_aqui\nCHAT_IDS=1\n")
            self.assertEqual(am.main(["-c", bad, "--check-config"]), am.EX_CONFIG)
            self.assertEqual(am.main(["-c", os.path.join(tmp, "nao-existe"), "--check-config"]), am.EX_CONFIG)


# --------------------------------------------------------------------------
# v1.2: histórico de atualizações e relatório diário ao cliente
# --------------------------------------------------------------------------



def epoch_utc(*parts):
    return calendar.timegm(parts)


def local_epoch(text):
    """Interpreta 'AAAA-MM-DD HH:MM:SS' no fuso do sistema (fuso do sistema)."""
    import time as _time
    return _time.mktime(_time.strptime(text, "%Y-%m-%d %H:%M:%S"))


class TimeTests(unittest.TestCase):
    TZ = "America/Sao_Paulo"      # UTC-3, sem horário de verão

    def test_report_due_and_day(self):
        before = epoch_utc(2026, 11, 25, 1, 29, 0)      # 22:29 em São Paulo (dia 24)
        after = epoch_utc(2026, 11, 25, 1, 30, 0)       # 22:30 em São Paulo
        self.assertFalse(am.report_due(before, self.TZ, (22, 30)))
        self.assertTrue(am.report_due(after, self.TZ, (22, 30)))
        self.assertEqual(am.local_day(after, self.TZ), "2026-11-24")
        self.assertEqual(am.local_day(epoch_utc(2026, 11, 25, 3, 0, 0), self.TZ), "2026-11-25")

    def test_next_report(self):
        now = epoch_utc(2026, 11, 24, 12, 0, 0)         # 09:00 em São Paulo
        self.assertEqual(am.next_report_ts(now, self.TZ, (22, 30)), epoch_utc(2026, 11, 25, 1, 30, 0))
        late = epoch_utc(2026, 11, 25, 1, 31, 0)        # já passou das 22:30
        self.assertEqual(am.next_report_ts(late, self.TZ, (22, 30)), epoch_utc(2026, 11, 26, 1, 30, 0))

    def test_timezone_context_restores_environment(self):
        before = os.environ.get("TZ")
        am.local_format(0, self.TZ, "%H")
        self.assertEqual(os.environ.get("TZ"), before)


class _null(object):
    """Contexto vazio (unittest.assertNoLogs só existe no Python 3.10+)."""
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ReportConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def conf(self, extra=""):
        return am.load_config(write_conf(self.tmp.name, "[Telegram]\nBOT_TOKEN=%s\nCHAT_IDS=1\n%s" % (TOKEN, extra)))

    def test_defaults(self):
        cfg = self.conf()
        self.assertEqual(cfg.report_time, (22, 30))
        self.assertEqual(cfg.report_tz, "America/Sao_Paulo")
        self.assertEqual(cfg.admin_name, "")
        self.assertFalse(cfg.report_send_empty)
        self.assertEqual(cfg.client_channels(), [])
        self.assertEqual(cfg.channels(), ["telegram"])

    def test_client_channels_and_report_options(self):
        cfg = self.conf("[Report]\nADMIN_NAME=Henrique Fagundes\nREPORT_TIME=7:05\nREPORT_TIMEZONE=UTC\n"
                        "[ClientTelegram]\nBOT_TOKEN=%s\nCHAT_IDS=99\n"
                        "[ClientEmail]\nSMTP_SERVER=mail.example.com\nSENDER_EMAIL=r@example.com\n"
                        "RECIPIENT_EMAILS=cliente@example.com\n" % TOKEN.replace("123456789", "987654321"))
        self.assertEqual(cfg.admin_name, "Henrique Fagundes")
        self.assertEqual(cfg.report_time, (7, 5))
        self.assertEqual(cfg.report_tz, "UTC")
        self.assertEqual(cfg.client_channels(), ["telegram", "email"])
        self.assertEqual(cfg.client_chat_ids, ["99"])
        self.assertEqual(cfg.client_email["recipients"], ["cliente@example.com"])

    def test_client_placeholders_disable_client_channels(self):
        cfg = self.conf("[ClientTelegram]\nBOT_TOKEN=seu_bot_token_aqui\nCHAT_IDS=1\n"
                        "[ClientEmail]\nSMTP_SERVER=smtp.seuservidor.com\nRECIPIENT_EMAILS=a@seuservidor.com\n")
        self.assertEqual(cfg.client_channels(), [])

    def test_repeated_sections_are_merged_instead_of_rejected(self):
        # Caso real: o .conf migrado já traz '[Report]' vazio; o administrador cola outro '[Report]' preenchido.
        cfg = self.conf("[Report]\n#ADMIN_NAME=\n[Monitor]\nCHECK_INTERVAL_HOURS=2\n"
                        "[Report]\nADMIN_NAME=Ana\nREPORT_TIME=07:15\n"
                        "[ClientTelegram]\n[ClientTelegram]\nBOT_TOKEN=%s\nCHAT_IDS=9\n" % TOKEN)
        self.assertEqual(cfg.admin_name, "Ana")
        self.assertEqual(cfg.report_time, (7, 15))
        self.assertEqual(cfg.interval_hours, 2.0)
        self.assertEqual(cfg.client_channels(), ["telegram"])

    def test_empty_client_sections_are_silent(self):
        with self.assertNoLogs("dnf-monitor-plus", level="WARNING") if hasattr(self, "assertNoLogs") else _null():
            cfg = self.conf("[Report]\n#ADMIN_NAME=x\n[ClientTelegram]\n#BOT_TOKEN=x\n[ClientEmail]\n#SMTP_SERVER=x\n")
        self.assertEqual(cfg.client_channels(), [])

    def test_client_alone_is_not_enough(self):
        with self.assertRaises(am.ConfigProblem):
            am.load_config(write_conf(self.tmp.name, "[ClientTelegram]\nBOT_TOKEN=%s\nCHAT_IDS=1\n" % TOKEN))

    def test_invalid_time_and_timezone(self):
        for bad in ("REPORT_TIME=25:00", "REPORT_TIME=22h30", "REPORT_TIME=22:60",
                    "REPORT_TIMEZONE=Marte/Olympus", "REPORT_TIMEZONE=../etc/passwd"):
            with self.assertRaises(am.ConfigProblem, msg=bad):
                self.conf("[Report]\n%s\n" % bad)

    def test_client_secrets_are_redacted(self):
        secret = "987654321:AAEhBP0av28dEXAMPLEexampleTOKEN_abc"
        self.conf("[ClientTelegram]\nBOT_TOKEN=%s\nCHAT_IDS=1\n" % secret)
        self.assertNotIn(secret, am.REDACTOR.redact("falha em " + secret))


class HistoryDBTests(unittest.TestCase):
    def entry(self, start=1000, cmd="dnf upgrade"):
        return am.HistoryEntry(start, start + 60, cmd, "", False, "", [])

    def test_add_is_idempotent_and_counts(self):
        db = am.HistoryDB(":memory:", since_ts=0)
        results = [am.PackageResult("a", "1", "2", True), am.PackageResult("b", "1", "2", False)]
        run_id = db.add_run(self.entry(), results)
        self.assertIsNotNone(run_id)
        self.assertIsNone(db.add_run(self.entry(), results))
        self.assertTrue(db.has_run(1000, "dnf upgrade"))
        run = db.unreported_runs()[0]
        self.assertEqual((run.ok, run.failed), (1, 1))
        self.assertEqual([p.ok for p in run.packages], [False, True])    # falhas primeiro

    def test_flags_and_channels(self):
        db = am.HistoryDB(":memory:", since_ts=0)
        run_id = db.add_run(self.entry(), [am.PackageResult("a", "1", "2", True)])
        self.assertEqual(len(db.unreported_runs()), 1)
        db.mark_reported([run_id], "2026-11-24")
        self.assertEqual(db.unreported_runs(), [])
        db.mark_channel_done("2026-11-24", "telegram", 5)
        self.assertEqual(db.channels_done("2026-11-24"), {"telegram"})
        self.assertEqual(db.channels_done("2026-11-25"), set())

    def test_since_ts_is_fixed_at_creation_and_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "h.db")
            first = am.HistoryDB(path, clock=lambda: 777.0)
            first.close()
            second = am.HistoryDB(path, clock=lambda: 9999.0)
            self.assertEqual(second.since_ts, 777.0)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            second.close()


class ReportMessageTests(unittest.TestCase):
    def runs(self):
        return [am.RunRecord(1, epoch_utc(2026, 11, 24, 13, 0, 0), None, 4, 0,
                             [am.PackageResult("glibc", "1", "2", True)]),
                am.RunRecord(2, epoch_utc(2026, 11, 24, 20, 0, 0), None, 1, 1,
                             [am.PackageResult("<b>x</b>", "1", "2", False)])]

    def report(self, **kw):
        args = dict(hostname="srv", ips="10.0.0.1", day="2026-11-24", runs=self.runs(),
                    admin_name="Henrique Fagundes", tz="America/Sao_Paulo")
        args.update(kw)
        return am.DailyReport(**args)

    def test_totals_and_headline(self):
        report = self.report()
        self.assertEqual((report.total, report.ok, report.failed), (6, 5, 1))
        text = report.telegram_text()
        self.assertIn("O administrador Henrique Fagundes atualizou o servidor 2 vezes em 24/11/2026.", text)
        self.assertIn("Pacotes atualizados: <b>6</b>", text)
        self.assertIn("Com sucesso: <b>5</b>", text)
        self.assertIn("Com falha: <b>1</b>", text)
        self.assertEqual(report.subject, "[srv] Relatório diário de atualizações - 24/11/2026")

    def test_singular_and_anonymous(self):
        report = self.report(runs=self.runs()[:1], admin_name="")
        self.assertIn("A equipe técnica atualizou o servidor 1 vez em", report.headline())

    def test_email_is_escaped_and_has_local_times(self):
        report = self.report(pending=3, reboot=True)
        page = report.email_html()
        self.assertNotIn("<b>x</b>", page)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)
        self.assertIn("10:00", page)                    # 13:00 UTC em São Paulo
        self.assertIn("17:00", page)
        self.assertIn("Reinicialização pendente", page)
        self.assertIn("Henrique Fagundes", page)
        plain = report.email_text()
        self.assertIn("Com falha: 1", plain)
        self.assertIn("[FALHOU]", plain)

    def test_package_list_can_be_hidden(self):
        report = self.report(package_list=False)
        self.assertNotIn("libc6", report.email_html())
        self.assertNotIn("libc6", report.email_text())


class EmailLayoutTests(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(am.fmt_duration(42), "42 s")
        self.assertEqual(am.fmt_duration(312), "5 min 12 s")
        self.assertEqual(am.fmt_duration(3900), "1 h 05 min")
        self.assertEqual(am.fmt_duration(-5), "0 s")

    def test_success_bar_never_claims_100_with_failures(self):
        self.assertEqual(am._success_bar(0, 0), "")
        bar = am._success_bar(199, 1)
        self.assertIn("99%", bar.replace("99%\"", "99%"))
        self.assertNotIn(">100%<", bar)
        self.assertIn(">100%<", am._success_bar(5, 0))
        self.assertIn(">0%<", am._success_bar(0, 3))

    def test_cards_and_pills_render_values(self):
        cards = am._stat_cards([(7, "Pacotes", am.BLUE), (0, "Falhas", am.RED)])
        self.assertIn(">7<", cards)
        self.assertIn("Falhas", cards)
        self.assertNotIn("calc(", cards)                       # Outlook não entende

    def test_emails_are_self_contained_and_escaped(self):
        run = am.RunRecord(1, 1000, 1300, 0, 1, [am.PackageResult("<img src=x>", "1", "2", False)])
        for page in (am.Alert("new", "h<script>", "i", [am.Update("<img src=x>", "1", "2", True)]).email_html(),
                     am.DailyReport("h", "i", "2026-11-24", [run], "<script>").email_html()):
            self.assertNotIn("<script>", page)
            self.assertNotIn("<img", page)                      # nada de imagens externas/remotas
            self.assertIn("&lt;img src=x&gt;", page)
            self.assertIn("lang=\"pt-BR\"", page)

    def test_empty_report_and_test_banner(self):
        page = am.DailyReport("h", "i", "2026-11-24", []).email_html()
        self.assertIn("Nenhuma atualização foi realizada", page)
        self.assertIn("SEM MOVIMENTO", page)
        self.assertIn("Mensagem de teste", am.DailyReport("h", "i", "2026-11-24", [], test=True).email_html())

    def test_preheader_summarizes_report(self):
        run = am.RunRecord(1, 1000, None, 3, 1, [])
        page = am.DailyReport("h", "i", "2026-11-24", [run]).email_html()
        self.assertIn("4 pacotes atualizados, 3 com sucesso e 1 com falha", page)


class AdminAlertEmailTests(unittest.TestCase):
    def alert(self, updates, kind="new", **kw):
        return am.Alert(kind, "srv", "10.0.0.1", updates, detected_at=epoch_utc(2026, 11, 24, 13, 0, 0),
                        tz="America/Sao_Paulo", **kw)

    def test_kernel_alert_is_red_critical_and_has_command(self):
        page = self.alert([upd("kernel-core"), upd("openssl", sec=True), upd("vim")], reboot=True,
                          reboot_pkgs=["linux-image"]).email_html()
        self.assertIn("CRÍTICO", page)
        self.assertIn("#dc2626", page)
        self.assertIn("sudo dnf upgrade --refresh", page)
        self.assertIn("Detectado em", page)
        self.assertIn("24/11/2026 às 10:00", page)              # 13:00 UTC em São Paulo
        self.assertIn("Reinicialização pendente", page)
        self.assertIn("Kernel", page)
        self.assertIn("Segurança", page)

    def test_plain_and_security_variants(self):
        plain = self.alert([upd("vim"), upd("curl")]).email_html()
        self.assertIn("ATUALIZAÇÕES", plain)
        self.assertNotIn("CRÍTICO", plain)
        sec = self.alert([upd("openssl", sec=True)]).email_html()
        self.assertIn("SEGURANÇA", sec)
        self.assertIn("Correções de segurança", sec)

    def test_reminder_cleared_reboot_and_test_render(self):
        self.assertIn("Lembrete", self.alert([upd("vim")], kind="reminder").email_html())
        cleared = self.alert([], kind="cleared").email_html()
        self.assertIn("EM DIA", cleared)
        self.assertNotIn("Como atualizar", cleared)
        reboot = self.alert([], kind="reboot", reboot=True, reboot_pkgs=["libc6"]).email_html()
        self.assertIn("REINICIAR", reboot)
        self.assertIn("libc6", reboot)
        self.assertIn("TESTE", self.alert([upd("x")], kind="test").email_html())

    def test_works_without_detection_time_and_truncates_long_lists(self):
        page = am.Alert("new", "srv", "ip", [upd("p%d" % i) for i in range(130)]).email_html()
        self.assertNotIn("Detectado em", page)
        self.assertIn("e mais 30 pacote(s)", page)

    def test_monitor_stamps_detection_time(self):
        notifier = FakeNotifier()
        cfg = am.Config()
        cfg.update_cache = False
        mon = am.Monitor(cfg, [notifier], am.State(None), collector=lambda _u: [upd("a")], hostname="srv",
                         ips_func=lambda: "ip", reboot_func=lambda: (False, []), clock=lambda: 5000.0, persist=False)
        mon.run_cycle()
        self.assertEqual(notifier.alerts[0].detected_at, 5000.0)


DAY = 86400


class PendingTrackingTests(unittest.TestCase):
    def test_first_seen_survives_version_bumps_and_resets_when_installed(self):
        db = am.HistoryDB(":memory:", since_ts=0)
        db.sync_pending([am.Update("openssl", "1", "2", True), am.Update("vim", "1", "2", False)], 1000)
        db.sync_pending([am.Update("openssl", "1", "3", True)], 5000)          # versão nova; vim foi instalado
        pkgs = {p.name: p for p in db.pending_packages()}
        self.assertEqual(list(pkgs), ["openssl"])
        self.assertEqual((pkgs["openssl"].candidate, pkgs["openssl"].first_seen), ("3", 1000))
        db.sync_pending([], 6000)
        self.assertEqual(db.pending_packages(), [])
        db.sync_pending([am.Update("openssl", "3", "4", True)], 9000)           # voltou a ficar pendente: relógio zera
        self.assertEqual(db.pending_packages()[0].first_seen, 9000)

    def test_overdue_is_strictly_more_than_n_days_and_returns_everything(self):
        db = am.HistoryDB(":memory:", since_ts=0)
        db.sync_pending([am.Update("velho", "1", "2", False)], 0)
        db.sync_pending([am.Update("velho", "1", "2", False), am.Update("novo", "1", "2", False)], 2 * DAY)
        self.assertEqual(db.overdue(3 * DAY, 3), [])                           # exatamente 3 dias: ainda não
        overdue = db.overdue(3 * DAY + 1, 3)
        self.assertEqual([p.name for p in overdue], ["velho", "novo"])         # lista TODOS os pendentes
        self.assertEqual(db.overdue(30 * DAY, 0), [])                          # 0 desativa
        self.assertEqual(db.overdue(30 * DAY, -1), [])

    def test_config_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = "[Telegram]\nBOT_TOKEN=%s\nCHAT_IDS=1\n" % TOKEN
            self.assertEqual(am.load_config(write_conf(tmp, base)).pending_alert_days, 3.0)
            self.assertEqual(am.load_config(write_conf(tmp, base + "[Report]\nPENDING_ALERT_DAYS=5\n")).pending_alert_days, 5.0)
            self.assertEqual(am.load_config(write_conf(tmp, base + "[Report]\nPENDING_ALERT_DAYS=0\n")).pending_alert_days, 0.0)
            for bad in ("-1", "tres"):
                with self.assertRaises(am.ConfigProblem):
                    am.load_config(write_conf(tmp, base + "[Report]\nPENDING_ALERT_DAYS=%s\n" % bad))


class OverdueReportFlowTests(unittest.TestCase):
    TZ = "America/Sao_Paulo"

    def setUp(self):
        self.client = FakeNotifier()
        self.client.name = "cliente"
        self.now = epoch_utc(2026, 11, 20, 15, 0, 0)               # 12:00 em São Paulo
        self.updates = [am.Update("openssl", "1", "2", True), am.Update("vim", "1", "2", False)]
        cfg = am.Config()
        cfg.update_cache = False
        cfg.report_tz = self.TZ
        cfg.admin_name = "Henrique Fagundes"
        self.cfg = cfg
        self.db = am.HistoryDB(":memory:", since_ts=0)
        self.mon = am.Monitor(cfg, [FakeNotifier()], am.State(None), collector=lambda _u: self.updates, hostname="srv",
                              ips_func=lambda: "10.0.0.1", reboot_func=lambda: (False, []), clock=lambda: self.now,
                              persist=True, client_notifiers=[self.client], history=self.db,
                              history_reader=lambda since: [])

    def at_report_time(self, days_after_start):
        return epoch_utc(2026, 11, 20 + days_after_start + 1, 1, 30, 0)   # 22:30 em São Paulo do dia (início + n)

    def test_not_reported_before_more_than_three_days(self):
        self.mon.run_cycle()
        self.now = self.at_report_time(2)
        self.mon.run_cycle()
        self.mon.run_report()
        self.assertEqual(self.client.alerts, [])

    def test_overdue_without_any_update_sends_pending_report_with_no_names(self):
        self.mon.run_cycle()
        self.now = self.at_report_time(4)                          # 4 dias e meio depois
        self.mon.run_cycle()
        self.mon.run_report()
        self.assertEqual(len(self.client.alerts), 1)
        report = self.client.alerts[0]
        self.assertTrue(report.pending_only)
        self.assertEqual([p.name for p in report.overdue], ["openssl", "vim"])     # todos os pendentes
        self.assertEqual(report.oldest_days, 4)
        everything = report.telegram_text() + report.email_text() + report.email_html() + report.subject
        self.assertNotIn("Henrique", everything)                                   # nenhum nome
        self.assertIn("entrar em contato com o administrador do servidor", everything)
        self.assertIn("openssl", everything)
        self.mon.run_report()                                                      # só uma vez no dia
        self.assertEqual(len(self.client.alerts), 1)

    def test_pending_report_repeats_next_day_until_installed(self):
        self.mon.run_cycle()
        self.now = self.at_report_time(4)
        self.mon.run_cycle()
        self.mon.run_report()
        self.now = self.at_report_time(5)
        self.mon.run_cycle()
        self.mon.run_report()
        self.assertEqual(len(self.client.alerts), 2)
        self.updates = []                                                          # o administrador atualizou tudo
        self.now = self.at_report_time(6)
        self.mon.run_cycle()
        self.mon.run_report()
        self.assertEqual(len(self.client.alerts), 2)

    def test_runs_and_overdue_go_in_the_same_report(self):
        self.mon.run_cycle()
        self.db.add_run(am.HistoryEntry(self.now + 3 * DAY, None, "dnf upgrade", "", False, "", []),
                        [am.PackageResult("a", "1", "2", True)])
        self.now = self.at_report_time(4)
        self.mon.run_cycle()
        self.mon.run_report()
        self.assertEqual(len(self.client.alerts), 1)
        report = self.client.alerts[0]
        self.assertFalse(report.pending_only)
        self.assertEqual((len(report.runs), len(report.overdue)), (1, 2))
        self.assertIn("(há pendências)", report.subject)
        self.assertIn("Henrique Fagundes", report.telegram_text())                 # o nome só aparece na parte do trabalho feito

    def test_disabled_with_zero_days(self):
        self.cfg.pending_alert_days = 0
        self.mon.run_cycle()
        self.now = self.at_report_time(10)
        self.mon.run_cycle()
        self.mon.run_report()
        self.assertEqual(self.client.alerts, [])

    def test_dnf_failure_does_not_wipe_pending_clock(self):
        self.mon.run_cycle()
        self.updates = None
        self.now = self.at_report_time(2)
        self.mon.run_cycle()
        self.updates = [am.Update("openssl", "1", "2", True)]
        self.now = self.at_report_time(4)
        self.mon.run_cycle()
        self.assertEqual(self.db.pending_packages()[0].first_seen, int(epoch_utc(2026, 11, 20, 15, 0, 0)))


class OverdueMessageTests(unittest.TestCase):
    NOW = epoch_utc(2026, 11, 24, 15, 0, 0)

    def pend(self, name, days, sec=False):
        return am.PendingPackage(name, "1", "2", sec, self.NOW - days * DAY - 60)

    def report(self, **kw):
        args = dict(hostname="srv", ips="ip", day="2026-11-24", runs=[], admin_name="Henrique Fagundes",
                    overdue=[self.pend("novo", 1), self.pend("velho", 6, True)], overdue_days=3, now=self.NOW)
        args.update(kw)
        return am.DailyReport(**args)

    def test_sentence_orders_oldest_first_and_counts_security(self):
        report = self.report()
        self.assertEqual([p.name for p in report.overdue], ["velho", "novo"])
        self.assertEqual(report.overdue_sentence(),
                         "Há 2 pacotes aguardando atualização, o mais antigo há 6 dias (1 de segurança).")
        self.assertEqual(report.subject, "[srv] Atenção: atualizações pendentes - 24/11/2026")
        self.assertIn("• velho — 6 dias 🔐", report.telegram_text())
        self.assertIn("• novo — 1 dia", report.telegram_text())

    def test_age_text(self):
        self.assertEqual(am.age_text(self.NOW - 100, self.NOW), "menos de 1 dia")
        self.assertEqual(am.age_text(self.NOW - DAY, self.NOW), "1 dia")
        self.assertEqual(am.age_text(self.NOW - 9 * DAY - 5, self.NOW), "9 dias")
        self.assertEqual(am.age_text(self.NOW + 500, self.NOW), "menos de 1 dia")      # relógio adiantado não quebra

    def test_html_has_table_ages_and_escaping(self):
        page = self.report(overdue=[self.pend("<b>x</b>", 6)]).email_html()
        self.assertIn("Aguardando", page)
        self.assertIn("6 dias", page)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)
        self.assertNotIn("<b>x</b>", page)
        self.assertIn("PENDÊNCIAS", page)
        self.assertIn("Dias de espera", page)

    def test_mixed_report_badge_and_long_list_truncation_in_telegram(self):
        run = am.RunRecord(1, self.NOW, None, 0, 1, [am.PackageResult("a", "1", "2", False)])
        mixed = self.report(runs=[run], overdue=[self.pend("p%d" % i, 5) for i in range(30)])
        self.assertIn("ATENÇÃO", mixed.email_html())
        text = mixed.telegram_text()
        self.assertIn("… e mais 5.", text)
        self.assertLess(len(text), 4096)                                            # limite do Telegram

    def test_no_overdue_keeps_old_behaviour(self):
        report = am.DailyReport("srv", "ip", "2026-11-24", [am.RunRecord(1, 0, None, 1, 0, [])], "Ana", pending=3)
        self.assertFalse(report.pending_only)
        self.assertIn("Ainda aguardam atualização: <b>3</b>", report.telegram_text())
        self.assertNotIn("Sugerimos", report.telegram_text())


class DocumentationExampleTests(unittest.TestCase):
    """O exemplo de plus/docs/exemplo-configuracao.md precisa continuar válido (e fictício)."""

    DOC = os.path.join(os.path.dirname(__file__), "..", "docs", "exemplo-configuracao.md")

    def example(self):
        with open(self.DOC, encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("```ini\n") + len("```ini\n")
        return text[start:text.index("\n```", start)] + "\n"

    def test_example_loads_with_the_four_channels_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = am.load_config(write_conf(tmp, self.example()))
        self.assertEqual(cfg.channels(), ["telegram", "email"])                 # analista
        self.assertEqual(cfg.client_channels(), ["telegram", "email"])          # cliente
        self.assertEqual(cfg.chat_ids, ["12345678"])
        self.assertEqual(cfg.client_chat_ids, ["-1009876543210"])
        self.assertNotEqual(cfg.bot_token, cfg.client_bot_token)                 # bots diferentes
        self.assertEqual(cfg.email["recipients"], ["analista@exemplo.com"])
        self.assertEqual(cfg.client_email["recipients"], ["cliente@empresa.exemplo.com"])
        self.assertEqual((cfg.admin_name, cfg.report_time, cfg.report_tz, cfg.pending_alert_days),
                         ("Maria Souza", (22, 30), "America/Sao_Paulo", 3.0))
        self.assertEqual((cfg.interval_hours, cfg.report_send_empty, cfg.report_package_list), (1.0, False, True))

    def test_example_has_only_fictitious_data(self):
        text = self.example()
        self.assertIsNone(re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text), "IP real no exemplo")
        self.assertNotRegex(text, r"[A-Za-z0-9._-]+@(?!exemplo\.com|empresa\.exemplo\.com)[A-Za-z0-9.-]+")   # só domínios fictícios
        for token in re.findall(r"BOT_TOKEN=(\S+)", text):
            self.assertIn("Exemplo", token)                                      # nunca um token de verdade

    def test_documented_example_is_linked_from_the_docs(self):
        base = os.path.join(os.path.dirname(__file__), "..")
        for rel in ("README.md", os.path.join("docs", "configuracao.md")):
            with open(os.path.join(base, rel), encoding="utf-8") as handle:
                self.assertTrue("exemplo-configuracao.md" in handle.read(), "falta o link para o exemplo em " + rel)


# --------------------------------------------------------------------------
# Específicos do DNF: coletor, kernel, reboot, configuração, histórico e detecção de mudança
# --------------------------------------------------------------------------

class KernelTests(unittest.TestCase):
    def test_kernel_detection(self):
        yes = ["kernel", "kernel-core", "kernel-modules", "kernel-modules-core", "kernel-modules-extra",
               "kernel-uek", "kernel-uek-core", "kernel-uek-modules", "kernel-rt", "kernel-rt-core",
               "kernel-64k-core", "kernel-debug-core"]
        no = ["kernel-headers", "kernel-devel", "kernel-tools", "kernel-tools-libs", "kernel-doc",
              "kernel-uek-devel", "kernel-debuginfo", "kernel-abi-stablelists", "linux-firmware",
              "kernelshark", "curl", "kernel-srpm-macros", "kernel-rpm-macros"]
        for name in yes:
            self.assertTrue(am.is_kernel_package(name), name)
        for name in no:
            self.assertFalse(am.is_kernel_package(name), name)


class CompletedStub(object):
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class CollectTests(unittest.TestCase):
    """Parte do processo pai: interpretação da saída do coletor (o DNF real é exercitado nos containers)."""

    def run_with(self, completed=None, exc=None, **kwargs):
        original = am.subprocess.run

        def fake(cmd, **kw):
            self.cmd, self.kw = cmd, kw
            if exc:
                raise exc
            return completed
        am.subprocess.run = fake
        try:
            return am.collect_updates(**kwargs)
        finally:
            am.subprocess.run = original

    def payload(self, items, warning=None):
        return am.COLLECT_MARKER + json.dumps({"updates": items, "warning": warning}) + "\n"

    def test_parses_and_sorts_security_first(self):
        items = [{"name": "b", "installed": "1", "candidate": "2", "security": False},
                 {"name": "z", "installed": "1", "candidate": "2", "security": True},
                 {"name": "a", "installed": "-", "candidate": "2", "security": False}]
        out = self.run_with(CompletedStub(0, "ruído do dnf\n" + self.payload(items)))
        self.assertEqual([u.name for u in out], ["z", "a", "b"])
        self.assertTrue(out[0].security)
        self.assertIn("--collect-json", self.cmd)

    def test_empty_list_is_not_failure(self):
        self.assertEqual(self.run_with(CompletedStub(0, self.payload([]))), [])

    def test_failure_is_none_not_empty(self):
        self.assertIsNone(self.run_with(CompletedStub(2, "", "Error: Failed to download metadata")))
        self.assertIsNone(self.run_with(CompletedStub(0, "sem marcador")))
        self.assertIsNone(self.run_with(exc=am.subprocess.TimeoutExpired("x", 1)))
        self.assertIsNone(self.run_with(exc=OSError("boom")))

    def test_flags_and_timeout_are_passed(self):
        self.run_with(CompletedStub(0, self.payload([])), update_cache=False, skip_broken=False, timeout=77)
        self.assertIn("--no-refresh", self.cmd)
        self.assertIn("--strict-repos", self.cmd)
        self.assertEqual(self.kw["timeout"], 77)
        self.run_with(CompletedStub(0, self.payload([])), load_plugins=False)
        self.assertIn("--no-plugins", self.cmd)

    def test_solver_warning_is_logged(self):
        with self.assertLogs("dnf-monitor-plus", level="WARNING") as logs:
            self.run_with(CompletedStub(0, self.payload([], "solver do DNF falhou (x)")))
        self.assertIn("solver do DNF falhou", "\n".join(logs.output))


class RebootTests(unittest.TestCase):
    def with_script(self, body):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "needs-restarting")
        with open(path, "w") as handle:
            handle.write("#!/bin/sh\n" + body)
        os.chmod(path, 0o755)
        old = os.environ["PATH"]
        os.environ["PATH"] = tmp.name + os.pathsep + old
        self.addCleanup(os.environ.__setitem__, "PATH", old)

    def test_reboot_needed_lists_reasons(self):
        self.with_script('echo "Core libraries or services have been updated since boot-up:"\n'
                         'echo "  * kernel"\necho "  * glibc"\necho "Reboot is required."\nexit 1\n')
        self.assertEqual(am.reboot_required(), (True, ["glibc", "kernel"]))

    def test_no_reboot(self):
        self.with_script('echo "No core libraries or services have been updated since boot-up."\nexit 0\n')
        self.assertEqual(am.reboot_required(), (False, []))

    def test_unknown_exit_status_is_ignored(self):
        self.with_script("exit 2\n")
        self.assertFalse(am.reboot_required()[0])


class DnfConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def conf(self, extra=""):
        return am.load_config(write_conf(self.tmp.name, "[Telegram]\nBOT_TOKEN=%s\nCHAT_IDS=1\n%s" % (TOKEN, extra)))

    def test_defaults(self):
        cfg = self.conf()
        self.assertEqual((cfg.skip_broken_repos, cfg.load_plugins, cfg.collect_timeout), (True, True, 900))
        self.assertIn("podman*", cfg.ignore_interfaces)
        self.assertEqual(am.DEFAULT_CONFIG, "/etc/dnf-monitor-plus/dnf-monitor.conf")
        self.assertEqual(am.DEFAULT_DB, "/var/lib/dnf-monitor-plus/history.db")

    def test_dnf_options(self):
        cfg = self.conf("[Monitor]\nSKIP_BROKEN_REPOS=false\nLOAD_DNF_PLUGINS=no\nCOLLECT_TIMEOUT_MINUTES=2.5\n")
        self.assertEqual((cfg.skip_broken_repos, cfg.load_plugins, cfg.collect_timeout), (False, False, 150))

    def test_invalid_dnf_options(self):
        for bad in ("COLLECT_TIMEOUT_MINUTES=abc", "COLLECT_TIMEOUT_MINUTES=0.1"):
            with self.assertRaises(am.ConfigProblem, msg=bad):
                self.conf("[Monitor]\n%s\n" % bad)


def make_dnf_history(path, transactions):
    """Cria um history.sqlite no esquema real do DNF (conferido em Rocky 8/9, Alma 10 e Oracle 9).

    transactions: [(id, begin, end, cmdline, state, [(action, item_state, nome, epoch, versão, release)])]
    """
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE trans (id INTEGER PRIMARY KEY, dt_begin INTEGER, dt_end INTEGER, rpmdb_version_begin TEXT,"
        " rpmdb_version_end TEXT, releasever TEXT, user_id INTEGER, cmdline TEXT, state INTEGER, comment TEXT,"
        " persistence INTEGER);"
        "CREATE TABLE trans_item (id INTEGER PRIMARY KEY, trans_id INTEGER, item_id INTEGER, repo_id INTEGER,"
        " action INTEGER, reason INTEGER, state INTEGER);"
        "CREATE TABLE rpm (item_id INTEGER PRIMARY KEY, name TEXT, epoch INTEGER, version TEXT, release TEXT, arch TEXT);")
    item = 0
    for tid, begin, end, cmdline, state, items in transactions:
        db.execute("INSERT INTO trans (id, dt_begin, dt_end, user_id, cmdline, state) VALUES (?, ?, ?, -1, ?, ?)",
                   (tid, begin, end, cmdline, state))
        for action, item_state, name, epoch, version, release in items:
            item += 1
            db.execute("INSERT INTO rpm VALUES (?, ?, ?, ?, ?, 'x86_64')", (item, name, epoch, version, release))
            db.execute("INSERT INTO trans_item (trans_id, item_id, action, state) VALUES (?, ?, ?, ?)",
                       (tid, item, action, item_state))
    db.commit()
    db.close()


UP, OLD, INST = am.ACTION_UPGRADE, am.ACTION_UPGRADED, am.ACTION_INSTALL


class ReadDnfHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "history.sqlite")

    def read(self, transactions, since=0):
        make_dnf_history(self.path, transactions)
        return am.read_dnf_history(self.path, since)

    def test_upgrade_pairs_new_and_old_versions_and_succeeds(self):
        (entry,) = self.read([(1, 1000, 1010, "-y upgrade pa pb", 1, [
            (UP, 1, "pa", 0, "2.0", "1"), (OLD, 1, "pa", 0, "1.0", "1"),
            (UP, 1, "pb", 1, "3.0", "2.el9"), (OLD, 1, "pb", 0, "2.9", "1.el9")])])
        self.assertEqual(entry.upgrades, [("pa", "1.0-1", "2.0-1", True), ("pb", "2.9-1.el9", "1:3.0-2.el9", True)])
        self.assertEqual((entry.start_ts, entry.end_ts, entry.commandline), (1000.0, 1010.0, "-y upgrade pa pb"))
        self.assertFalse(entry.automatic)
        self.assertEqual(entry.error, "")

    def test_failed_item_and_failed_transaction(self):
        (entry,) = self.read([(1, 1000, 1010, "upgrade -y", 2, [(UP, 2, "pa", 0, "2.0", "1"), (UP, 1, "pb", 0, "2.0", "1")])])
        self.assertEqual([u[3] for u in entry.upgrades], [False, True])      # o estado de cada pacote vem do DNF
        self.assertIn("erro", entry.error)

    def test_plain_installs_and_removals_do_not_count(self):
        (entry,) = self.read([(1, 1000, 1010, "install htop", 1, [(INST, 1, "htop", 0, "3.2", "1")])])
        self.assertEqual(entry.upgrades, [])

    def test_new_kernel_counts_even_though_dnf_installs_it_beside_the_old_one(self):
        (entry,) = self.read([(1, 1000, 1010, "-y upgrade", 1, [
            (INST, 1, "kernel-core", 0, "5.14.0", "570.el9"), (INST, 1, "kernel-headers", 0, "5.14.0", "570.el9"),
            (INST, 1, "dependencia", 0, "1", "1")])])
        self.assertEqual(entry.upgrades, [("kernel-core", "-", "5.14.0-570.el9", True)])     # headers e dependência não

    def test_dnf_automatic_and_api_tools_have_no_commandline(self):
        for cmdline in ("", "  ", "/usr/bin/dnf-automatic /etc/dnf/automatic.conf --timer"):
            (entry,) = self.read([(1, 1000, 1010, cmdline, 1, [(UP, 1, "pa", 0, "2.0", "1")])])
            self.assertTrue(entry.automatic, repr(cmdline))
            os.remove(self.path)
        (manual,) = self.read([(1, 1000, 1010, "-y upgrade", 1, [(UP, 1, "pa", 0, "2.0", "1")])])
        self.assertFalse(manual.automatic)

    def test_running_transaction_has_no_end_and_since_filter(self):
        entries = self.read([(1, 500, 510, "-y upgrade", 1, [(UP, 1, "velho", 0, "2", "1")]),
                             (2, 2000, None, "-y upgrade", 0, [(UP, 0, "pa", 0, "2.0", "1")])], since=1000)
        self.assertEqual([e.start_ts for e in entries], [2000.0])             # a de 500 é anterior ao banco do Plus
        self.assertIsNone(entries[0].end_ts)
        self.assertEqual(entries[0].error, "")                                # em andamento não é erro

    def test_missing_or_foreign_database_raises_so_the_monitor_can_ignore_it(self):
        with self.assertRaises(sqlite3.Error):
            am.read_dnf_history(os.path.join(self.tmp.name, "nao-existe.sqlite"))
        sqlite3.connect(self.path).close()
        with self.assertRaises(sqlite3.Error):
            am.read_dnf_history(self.path)                                    # sem as tabelas do DNF

    def test_database_is_opened_read_only(self):
        make_dnf_history(self.path, [(1, 1000, 1010, "-y upgrade", 1, [(UP, 1, "pa", 0, "2", "1")])])
        am.read_dnf_history(self.path)
        db = sqlite3.connect(self.path)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM trans").fetchone()[0], 1)
        db.close()


class RepoSignatureTests(unittest.TestCase):
    def test_signature_changes_and_settling(self):
        with tempfile.TemporaryDirectory() as tmp:
            repomd = os.path.join(tmp, "base-abc", "repodata", "repomd.xml")
            os.makedirs(os.path.dirname(repomd))
            with open(repomd, "w") as handle:
                handle.write("x")
            patterns = (os.path.join(tmp, "*", "repodata", "repomd.xml"), os.path.join(tmp, "rpmdb.sqlite"))
            before = am.repo_signature(patterns)
            self.assertEqual(len(before), 1)
            self.assertEqual(before, am.repo_signature(patterns))
            os.utime(repomd, (5000, 5000))
            after = am.repo_signature(patterns)
            self.assertNotEqual(before, after)
            self.assertFalse(am.repo_changed(before, after, now=5005))        # o DNF ainda está mexendo
            self.assertTrue(am.repo_changed(before, after, now=5060))         # assentou: checar
            self.assertFalse(am.repo_changed(after, after, now=5060))         # nada novo
            with open(os.path.join(tmp, "rpmdb.sqlite"), "w") as handle:      # arquivo novo também conta
                handle.write("db")
            self.assertEqual(len(am.repo_signature(patterns)), 2)

    def test_watch_list_avoids_files_that_change_on_reads(self):
        for pattern in am.DNF_WATCH:
            self.assertFalse(pattern.endswith("-shm"), pattern)               # o -shm muda até em leituras (laço de checagens)
        self.assertTrue(any("repomd.xml" in p for p in am.DNF_WATCH))
        self.assertEqual(am.repo_signature(("/nao/existe/*",)), ())
        self.assertFalse(am.repo_changed((), (), now=10 ** 9))

    def test_cycle_without_cache_update_uses_override(self):
        seen = []
        cfg = am.Config()
        cfg.update_cache = True
        mon = am.Monitor(cfg, [FakeNotifier()], am.State(None), collector=lambda u: seen.append(u) or [],
                         hostname="srv", ips_func=lambda: "ip", reboot_func=lambda: (False, []), persist=False)
        mon.run_cycle()
        mon.run_cycle(update_cache=False)
        self.assertEqual(seen, [True, False])


def entry(start, packages, end=True, cmd="-y upgrade", automatic=False, error="", ok=True):
    """HistoryEntry de teste; ``packages`` é a lista de nomes."""
    return am.HistoryEntry(float(start), float(start + 60) if end else None, cmd, "", automatic, error,
                           [(p, "1", "2", ok) for p in packages])


class ReportFlowTests(unittest.TestCase):
    """Fluxo: transações do DNF -> histórico -> relatório diário (com um leitor de histórico falso)."""
    TZ = "America/Sao_Paulo"

    def setUp(self):
        self.admin = FakeNotifier()
        self.client = FakeNotifier()
        self.client.name = "cliente"
        self.now = epoch_utc(2026, 11, 24, 21, 0, 0)        # 18:00 em São Paulo
        self.entries = []
        cfg = am.Config()
        cfg.report_tz = self.TZ
        cfg.admin_name = "Henrique Fagundes"
        self.cfg = cfg
        self.db = am.HistoryDB(":memory:", since_ts=0)
        self.mon = am.Monitor(cfg, [self.admin], am.State(None), hostname="srv", ips_func=lambda: "10.0.0.1",
                              reboot_func=lambda: (False, []), clock=lambda: self.now, persist=True,
                              client_notifiers=[self.client], history=self.db,
                              history_reader=lambda since: [e for e in self.entries if e.start_ts >= since])

    def at(self, text):
        return local_epoch(text)

    def test_run_is_recorded_once_and_nobody_is_notified(self):
        self.entries = [am.HistoryEntry(self.at("2026-11-24 17:00:00"), self.at("2026-11-24 17:01:00"), "-y upgrade", "", False, "",
                                        [("bom", "1", "2", True), ("quebrado", "1", "2", False)])]
        self.assertTrue(self.mon.process_runs())
        self.mon.process_runs()
        runs = self.db.unreported_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual((runs[0].ok, runs[0].failed), (1, 1))
        self.assertEqual(self.admin.alerts, [])              # administrador NÃO recebe aviso de "concluído"
        self.assertEqual(self.client.alerts, [])             # e o cliente só recebe no horário do relatório

    def test_automatic_runs_are_ignored(self):
        self.entries = [entry(self.at("2026-11-24 06:00:00"), ["auto"], automatic=True, cmd=""),
                        entry(self.at("2026-11-24 17:00:00"), ["manual"])]
        self.mon.process_runs()
        (run,) = self.db.unreported_runs()
        self.assertEqual([p.name for p in run.packages], ["manual"])        # só a do administrador

    def test_old_history_is_not_replayed(self):
        self.db = am.HistoryDB(":memory:", since_ts=self.now)               # banco "nasceu" agora
        self.mon.history = self.db
        self.entries = [entry(self.at("2026-11-20 10:00:00"), ["a"])]
        self.mon.process_runs()
        self.assertEqual(self.db.unreported_runs(), [])

    def test_running_transaction_is_deferred_then_recorded_as_failed(self):
        start = self.now - 600
        self.entries = [entry(start, ["a"], end=False)]                      # sem fim, há 10 min: ainda pode estar rodando
        self.mon.process_runs()
        self.assertEqual(self.db.unreported_runs(), [])
        self.now += am.INPROGRESS_GRACE                                     # passou da tolerância: foi interrompida
        self.mon.process_runs()
        (run,) = self.db.unreported_runs()
        self.assertEqual((run.ok, run.failed), (0, 1))

    def test_transaction_with_error_marks_every_package_as_failed(self):
        self.entries = [entry(self.at("2026-11-24 17:00:00"), ["a", "b"], error="transação com erro (estado 2)")]
        self.mon.process_runs()
        (run,) = self.db.unreported_runs()
        self.assertEqual((run.ok, run.failed), (0, 2))

    def test_unreadable_dnf_history_is_ignored(self):
        def broken(_since):
            raise sqlite3.OperationalError("unable to open database file")
        self.mon.history_reader = broken
        self.assertTrue(self.mon.process_runs())
        self.assertEqual(self.db.unreported_runs(), [])

    def test_default_reader_reads_the_real_sqlite_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "history.sqlite")
            make_dnf_history(path, [(1, int(self.now) - 600, int(self.now) - 540, "-y upgrade", 1,
                                     [(UP, 1, "pa", 0, "2.0", "1"), (OLD, 1, "pa", 0, "1.0", "1")])])
            mon = am.Monitor(self.cfg, [self.admin], am.State(None), clock=lambda: self.now, persist=True,
                             history=self.db, history_path=path)
            mon.process_runs()
        (run,) = self.db.unreported_runs()
        self.assertEqual((run.ok, [(p.name, p.old, p.new) for p in run.packages]), (1, [("pa", "1.0-1", "2.0-1")]))

    def test_report_waits_for_time_and_is_sent_once_per_day(self):
        self.entries = [entry(self.at("2026-11-24 17:00:00"), ["a", "b"]), entry(self.at("2026-11-24 17:30:00"), ["c"])]
        self.mon.process_runs()
        self.mon.run_report()
        self.assertEqual(self.client.alerts, [])                           # 18:00: ainda não é 22:30
        self.now = epoch_utc(2026, 11, 25, 1, 30, 0)                       # 22:30
        self.mon.run_report()
        self.mon.run_report()
        self.assertEqual(len(self.client.alerts), 1)
        report = self.client.alerts[0]
        self.assertEqual((len(report.runs), report.total, report.ok, report.failed), (2, 3, 3, 0))
        self.assertEqual(report.day, "2026-11-24")
        self.entries.append(entry(self.at("2026-11-24 22:45:00"), ["d"]))   # depois do relatório: fica para amanhã
        self.now = epoch_utc(2026, 11, 25, 1, 50, 0)
        self.mon.process_runs()
        self.mon.run_report()
        self.assertEqual(len(self.client.alerts), 1)
        self.now = epoch_utc(2026, 11, 26, 1, 30, 0)
        self.mon.run_report()
        self.assertEqual(len(self.client.alerts), 2)
        self.assertEqual((self.client.alerts[1].day, self.client.alerts[1].total), ("2026-11-25", 1))

    def test_empty_day_sends_nothing_and_is_not_reconsidered(self):
        self.now = epoch_utc(2026, 11, 25, 1, 30, 0)
        self.mon.run_report()
        self.assertEqual(self.client.alerts, [])
        self.entries = [entry(self.at("2026-11-24 22:40:00"), ["a"])]
        self.now = epoch_utc(2026, 11, 25, 1, 45, 0)
        self.mon.process_runs()
        self.mon.run_report()
        self.assertEqual(self.client.alerts, [])                           # já decidido hoje: fica para amanhã

    def test_send_empty_option(self):
        self.cfg.report_send_empty = True
        self.now = epoch_utc(2026, 11, 25, 1, 30, 0)
        self.mon.run_report()
        self.assertEqual(len(self.client.alerts), 1)
        self.assertIn("Nenhuma atualização", self.client.alerts[0].telegram_text())

    def test_failed_channel_is_retried_without_resending_the_others(self):
        other = FakeNotifier(ok=False)
        other.name = "email"
        self.mon.client_notifiers = [self.client, other]
        self.entries = [entry(self.at("2026-11-24 17:00:00"), ["a"])]
        self.mon.process_runs()
        self.now = epoch_utc(2026, 11, 25, 1, 30, 0)
        self.assertFalse(self.mon.run_report())
        self.assertEqual((len(self.client.alerts), len(other.alerts)), (1, 1))
        self.assertEqual(len(self.db.unreported_runs()), 1)
        other.ok = True
        self.now += am.RETRY_INTERVAL + 1
        self.assertTrue(self.mon.run_report())
        self.assertEqual((len(self.client.alerts), len(other.alerts)), (1, 2))
        self.assertEqual(self.db.unreported_runs(), [])

    def test_force_sends_now_and_blocks_scheduled_duplicate(self):
        self.entries = [entry(self.at("2026-11-24 17:00:00"), ["a"])]
        self.mon.process_runs()
        self.assertTrue(self.mon.run_report(force=True))                   # 18:00, antes do horário
        self.assertEqual(len(self.client.alerts), 1)
        self.now = epoch_utc(2026, 11, 25, 1, 30, 0)
        self.mon.run_report()
        self.assertEqual(len(self.client.alerts), 1)

    def test_no_history_means_no_op(self):
        mon = am.Monitor(self.cfg, [self.admin], am.State(None), clock=lambda: self.now, persist=False)
        self.assertTrue(mon.process_runs())
        self.assertTrue(mon.run_report(force=True))

    def test_seconds_to_report(self):
        self.assertEqual(self.mon.seconds_to_report(), 4 * 3600 + 30 * 60)  # 18:00 -> 22:30


class ReportCLITests(unittest.TestCase):
    def test_report_requires_client_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = write_conf(tmp, "[Telegram]\nBOT_TOKEN=%s\nCHAT_IDS=1\n[Monitor]\nLOG_FILE=\n"
                                   "[Report]\nDB_FILE=%s\n" % (TOKEN, os.path.join(tmp, "h.db")))
            self.assertEqual(am.main(["-c", conf, "--report"]), am.EX_CONFIG)
            self.assertEqual(am.main(["-c", conf, "--report", "--dry-run"]), 0)


if __name__ == "__main__":
    unittest.main()
