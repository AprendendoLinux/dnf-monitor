"""Testes unitários (somente biblioteca padrão): python3 -m unittest discover -s tests -v"""

import base64
import http.server
import io
import json
import logging
import os
import socketserver
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
        with self.assertLogs("dnf-monitor", level="WARNING") as logs:
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
        mon = self.make([], reboot=(True, ["kernel-core"]))
        mon.run_cycle()
        mon.run_cycle()
        self.assertEqual([a.kind for a in self.notifier.alerts], ["reboot"])
        self.reboot = (False, [])
        mon.run_cycle()
        self.reboot = (True, [])
        mon.run_cycle()                           # novo reboot pendente depois de reiniciar: avisa de novo
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

    def test_headers_and_devel_not_critical(self):
        alert = am.Alert("new", "srv", "ip", [upd("kernel-headers"), upd("kernel-devel")])
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
        with self.assertLogs("dnf-monitor", level="ERROR") as logs:
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
        with self.assertLogs("dnf-monitor", level="ERROR") as logs:
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


if __name__ == "__main__":
    unittest.main()
