#!/usr/bin/python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""DNF Monitor Daemon.

Monitora atualizações de pacotes DNF em servidores RHEL e derivados (Rocky,
AlmaLinux, Oracle Linux, CentOS Stream...) e envia alertas por Telegram e/ou
e-mail (HTML + texto puro).

Compatível com Python >= 3.6 (EL8+). Depende apenas de ``python3-dnf``; o
restante usa somente a biblioteca padrão.
"""

import argparse
import fnmatch
import html
import json
import logging
import logging.handlers
import os
import re
import signal
import smtplib
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import namedtuple
from configparser import ConfigParser, Error as ConfigParserError
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

__version__ = "1.1.1"

DEFAULT_CONFIG = "/etc/dnf-monitor/dnf-monitor.conf"
DEFAULT_STATE = "/var/lib/dnf-monitor/state.json"
DEFAULT_LOG = "/var/log/dnf-monitor.log"
DEFAULT_API_BASE = "https://api.telegram.org"
DEFAULT_IGNORE_INTERFACES = "lo,docker*,br-*,veth*,virbr*,cni*,flannel*,cali*,tun*,tap*"
COLLECT_MARKER = "@@DNF-MONITOR-JSON@@"
EX_CONFIG = 78          # sysexits.h: erro de configuração (systemd não reinicia)
MIN_INTERVAL = 60       # segundos
RETRY_INTERVAL = 900    # segundos, após falha de envio / de leitura do dnf

log = logging.getLogger("dnf-monitor")


# --------------------------------------------------------------------------
# Logging (com remoção de segredos)
# --------------------------------------------------------------------------

class RedactingFilter(logging.Filter):
    """Remove tokens e senhas de qualquer mensagem de log."""

    TOKEN_RE = re.compile(r"(?:bot)?\d{5,}:[A-Za-z0-9_-]{20,}")

    def __init__(self):
        logging.Filter.__init__(self)
        self.secrets = set()

    def add(self, *values):
        for value in values:
            if value and len(value) >= 4:
                self.secrets.add(value)

    def redact(self, text):
        for secret in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(secret, "***")
        return self.TOKEN_RE.sub("***", text)

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return True
        record.msg = self.redact(message)
        record.args = ()
        if record.exc_info:
            # Evita que o traceback (com a URL/token) escape sem filtro.
            record.msg += " [%s]" % self.redact(str(record.exc_info[1]))
            record.exc_info = None
            record.exc_text = None
        return True


REDACTOR = RedactingFilter()


def setup_logging(log_file=DEFAULT_LOG, verbose=False):
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    # Sob o systemd o journal já carrega o timestamp.
    if os.environ.get("JOURNAL_STREAM"):
        fmt = "[%(levelname)s] %(message)s"
    else:
        fmt = "%(asctime)s [%(levelname)s] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        try:
            # WatchedFileHandler reabre o arquivo após o logrotate: não é
            # preciso reiniciar o serviço (o que antes causava alerta repetido).
            file_handler = logging.handlers.WatchedFileHandler(log_file, encoding="utf-8")
            try:
                os.chmod(log_file, 0o640)
            except OSError:
                pass
            handlers.append(file_handler)
        except OSError as exc:
            sys.stderr.write("Aviso: sem acesso ao log %s (%s); usando só stdout.\n" % (log_file, exc))

    for handler in handlers:
        handler.setFormatter(logging.Formatter(fmt, datefmt))
        handler.addFilter(REDACTOR)
        root.addHandler(handler)


# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------

class ConfigProblem(Exception):
    """Erro fatal de configuração."""


class Config(object):
    def __init__(self):
        self.bot_token = ""
        self.chat_ids = []
        self.api_base = DEFAULT_API_BASE
        self.interval_hours = 4.0
        self.reminder_days = 0.0
        self.notify_on_clear = False
        self.update_cache = True
        self.skip_broken_repos = True
        self.load_plugins = True
        self.collect_timeout = 900
        self.ignore_interfaces = []
        self.log_file = DEFAULT_LOG
        self.state_file = DEFAULT_STATE
        self.email = None

    @property
    def telegram_enabled(self):
        return bool(self.bot_token and self.chat_ids)

    @property
    def email_enabled(self):
        return self.email is not None

    @property
    def interval_seconds(self):
        return max(MIN_INTERVAL, int(self.interval_hours * 3600))

    def channels(self):
        names = []
        if self.telegram_enabled:
            names.append("telegram")
        if self.email_enabled:
            names.append("email")
        return names


def is_placeholder(value):
    value = (value or "").strip().lower()
    return (not value or "seuservidor" in value or value in
            ("seu_bot_token_aqui", "sua_senha_segura", "seu_token_aqui"))


def split_list(value):
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def parse_bool(value):
    return str(value).strip().lower() in ("1", "true", "yes", "sim", "on")


def normalize_token(token):
    token = (token or "").strip()
    # Aceita "123:ABC" e também "bot123:ABC" (formato que a v1.0 exigia).
    if token.lower().startswith("bot") and token[3:4].isdigit():
        token = token[3:]
    return token


def load_config(path):
    if not os.path.isfile(path):
        raise ConfigProblem("Arquivo de configuração %s não encontrado." % path)

    parser = ConfigParser(inline_comment_prefixes=("#", ";"), interpolation=None)
    try:
        with open(path, encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, ConfigParserError, UnicodeDecodeError) as exc:
        raise ConfigProblem("Não foi possível ler %s: %s" % (path, exc))

    cfg = Config()
    get = parser.get

    try:
        cfg.interval_hours = parser.getfloat("Monitor", "CHECK_INTERVAL_HOURS", fallback=4.0)
        cfg.reminder_days = parser.getfloat("Monitor", "REMINDER_DAYS", fallback=0.0)
    except ValueError as exc:
        raise ConfigProblem("Valor numérico inválido na seção [Monitor]: %s" % exc)
    if cfg.interval_hours <= 0:
        raise ConfigProblem("CHECK_INTERVAL_HOURS deve ser maior que zero.")
    cfg.notify_on_clear = parse_bool(get("Monitor", "NOTIFY_ON_CLEAR", fallback="false"))
    cfg.update_cache = parse_bool(get("Monitor", "UPDATE_CACHE", fallback="true"))
    cfg.skip_broken_repos = parse_bool(get("Monitor", "SKIP_BROKEN_REPOS", fallback="true"))
    cfg.load_plugins = parse_bool(get("Monitor", "LOAD_DNF_PLUGINS", fallback="true"))
    try:
        cfg.collect_timeout = int(parser.getfloat("Monitor", "COLLECT_TIMEOUT_MINUTES", fallback=15.0) * 60)
    except ValueError as exc:
        raise ConfigProblem("COLLECT_TIMEOUT_MINUTES inválido: %s" % exc)
    if cfg.collect_timeout < 30:
        raise ConfigProblem("COLLECT_TIMEOUT_MINUTES deve ser de pelo menos 0.5.")
    cfg.ignore_interfaces = split_list(get("Monitor", "IGNORE_INTERFACES", fallback=DEFAULT_IGNORE_INTERFACES))
    cfg.log_file = get("Monitor", "LOG_FILE", fallback=DEFAULT_LOG).strip()
    cfg.state_file = get("Monitor", "STATE_FILE", fallback=DEFAULT_STATE).strip()

    token = normalize_token(get("Telegram", "BOT_TOKEN", fallback=""))
    chat_ids = split_list(get("Telegram", "CHAT_IDS", fallback=""))
    if token and not is_placeholder(token) and chat_ids:
        cfg.bot_token = token
        cfg.chat_ids = chat_ids
        cfg.api_base = get("Telegram", "API_BASE", fallback=DEFAULT_API_BASE).strip().rstrip("/")
    elif parser.has_section("Telegram"):
        log.warning("Telegram não configurado (token/chat ids ausentes ou de exemplo): canal desativado.")

    if parser.has_section("Email"):
        server = get("Email", "SMTP_SERVER", fallback="").strip()
        recipients = [r for r in split_list(get("Email", "RECIPIENT_EMAILS", fallback="")) if not is_placeholder(r)]
        if is_placeholder(server) or not recipients:
            log.warning("E-mail não configurado (servidor/destinatários ausentes ou de exemplo): canal desativado.")
        else:
            try:
                port = parser.getint("Email", "SMTP_PORT", fallback=587)
            except ValueError as exc:
                raise ConfigProblem("SMTP_PORT inválido: %s" % exc)
            security = get("Email", "SMTP_SECURITY", fallback="auto").strip().lower()
            if security not in ("auto", "ssl", "starttls", "none"):
                raise ConfigProblem("SMTP_SECURITY deve ser auto, ssl, starttls ou none.")
            user = get("Email", "SMTP_USER", fallback="").strip()
            sender = get("Email", "SENDER_EMAIL", fallback=user).strip()
            if not sender:
                raise ConfigProblem("Defina SENDER_EMAIL (ou SMTP_USER) na seção [Email].")
            cfg.email = {
                "server": server,
                "port": port,
                "security": security,
                "user": user,
                "password": get("Email", "SMTP_PASS", fallback="").strip(),
                "sender_name": get("Email", "SENDER_NAME", fallback="DNF Monitor").strip(),
                "sender_email": sender,
                "recipients": recipients,
            }

    REDACTOR.add(cfg.bot_token, (cfg.email or {}).get("password"))

    if not cfg.channels():
        raise ConfigProblem("Nenhum canal de notificação válido (configure [Telegram] e/ou [Email] em %s)." % path)
    return cfg


# --------------------------------------------------------------------------
# Coleta de informações do sistema
# --------------------------------------------------------------------------

Update = namedtuple("Update", "name installed candidate security")

KERNEL_RE = re.compile(
    r"^kernel(-(uek|rt|64k|debug|automotive|zfcpdump))*"
    r"(-(core|modules|modules-core|modules-extra|modules-internal))?$"
)


def is_kernel_package(name):
    """True só para o kernel em si (kernel, kernel-core, kernel-modules*, kernel-uek*, kernel-rt*...).

    ``kernel-headers``, ``kernel-devel``, ``kernel-tools`` e afins não contam.
    """
    return bool(KERNEL_RE.match(name))


def _evr(pkg):
    return str(pkg.evr)


def _collect_in_process(update_cache=True, skip_broken=True, load_plugins=True):
    """Roda o solver do DNF e devolve ``(lista_de_dicts, aviso_ou_None)``.

    Executado em um processo filho (ver ``collect_updates``): a libdnf acumula
    memória e o DNF pode travar em mirrors lentos; assim o daemon fica isolado.
    """
    import dnf

    base = dnf.Base()
    try:
        base.conf.read()
        plugins = False
        if load_plugins:
            # A API não carrega plugins sozinha; sem isso o "versionlock" (e afins) seriam ignorados
            # e pacotes travados gerariam alertas para sempre.
            try:
                base.init_plugins()
                base.pre_configure_plugins()
                plugins = True
            except Exception as exc:
                sys.stderr.write("aviso: plugins do DNF não carregados (%s)\n" % exc)
        # Como o "dnf upgrade --nobest --skip-broken": um pacote com dependência quebrada
        # (ou retido por exclude/versionlock) não impede os demais e não conta como pendente.
        base.conf.best = False
        base.conf.strict = False
        if update_cache:
            base.conf.metadata_expire = 0     # equivalente a "dnf makecache --refresh"
        else:
            base.conf.cacheonly = True
        base.read_all_repos()
        if plugins:
            base.configure_plugins()
        if skip_broken:
            for repo in base.repos.iter_enabled():
                repo.skip_if_unavailable = True   # um repositório fora do ar não cega o monitor
        base.fill_sack()

        installed = {}
        for pkg in base.sack.query().installed():
            current = installed.get(pkg.name)
            if current is None or pkg.evr_cmp(current) > 0:
                installed[pkg.name] = pkg

        security = set()
        try:
            security = set(p.name for p in base.sack.query().upgrades().filter(advisory_type="security"))
        except Exception:
            pass    # sem updateinfo: segue sem marcar segurança

        warning = None
        chosen = []
        try:
            base.upgrade_all()
            base.resolve()
            transaction = base.transaction
            if transaction:
                chosen = list(transaction.install_set)
        except Exception as exc:
            # Falha do solver (dependências quebradas etc.): cai para a lista simples de upgrades.
            warning = "solver do DNF falhou (%s); usando a lista simples de upgrades" % exc
            base.reset(goal=True)
            chosen = list(base.sack.query().upgrades().latest())

        result = {}
        for pkg in chosen:
            current = result.get(pkg.name)
            if current is not None and pkg.evr_cmp(current) <= 0:
                continue
            result[pkg.name] = pkg
        items = []
        for name, pkg in result.items():
            old = installed.get(name)
            items.append({"name": name, "installed": _evr(old) if old is not None else "-",
                          "candidate": _evr(pkg), "security": name in security})
        return items, warning
    finally:
        base.close()


def collect_updates(update_cache=True, skip_broken=True, timeout=900, load_plugins=True):
    """Retorna a lista de ``Update`` pendentes, ou ``None`` se o DNF falhou.

    Distinguir "falha" de "nenhuma atualização" evita falsos "tudo em dia".
    """
    cmd = [sys.executable, os.path.abspath(__file__), "--collect-json"]
    if not update_cache:
        cmd.append("--no-refresh")
    if not skip_broken:
        cmd.append("--strict-repos")
    if not load_plugins:
        cmd.append("--no-plugins")
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log.error("O DNF não respondeu em %d s; ciclo abortado.", timeout)
        return None
    except OSError as exc:
        log.error("Não foi possível executar o coletor do DNF: %s", exc)
        return None

    payload = None
    for line in proc.stdout.splitlines():
        if line.startswith(COLLECT_MARKER):
            try:
                payload = json.loads(line[len(COLLECT_MARKER):])
            except ValueError:
                payload = None
    if proc.returncode != 0 or payload is None:
        detail = (proc.stderr or "").strip().splitlines()
        log.error("Falha ao consultar o DNF (código %s): %s", proc.returncode,
                  " | ".join(detail[-3:]) if detail else "sem detalhes")
        return None
    if payload.get("warning"):
        log.warning("%s", payload["warning"])
    updates = [Update(i["name"], i["installed"], i["candidate"], bool(i["security"])) for i in payload["updates"]]
    updates.sort(key=lambda u: (not u.security, u.name))
    return updates


def collect_main(argv):
    """Ponto de entrada do processo filho: imprime o resultado em JSON numa linha marcada."""
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(message)s")
    try:
        items, warning = _collect_in_process("--no-refresh" not in argv, "--strict-repos" not in argv,
                                         "--no-plugins" not in argv)
    except ImportError:
        sys.stderr.write("Módulo python3-dnf não encontrado (dnf install python3-dnf).\n")
        return 2
    except Exception as exc:
        sys.stderr.write("%s: %s\n" % (type(exc).__name__, exc))
        return 2
    sys.stdout.write(COLLECT_MARKER + json.dumps({"updates": items, "warning": warning}) + "\n")
    return 0


def reboot_required():
    """Usa ``needs-restarting -r`` (pacote dnf-utils / dnf-plugins-core). Retorna ``(precisa, motivos)``."""
    for cmd in (["needs-restarting", "-r"], ["dnf", "-q", "needs-restarting", "-r"]):
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  universal_newlines=True, timeout=180)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            return False, []
        if proc.returncode == 1:
            reasons = []
            for line in proc.stdout.splitlines():
                match = re.match(r"^\s*\*\s+(.+?)\s*$", line)
                if match:
                    reasons.append(match.group(1))
            return True, sorted(set(reasons))
    return False, []


def get_machine_ips(ignore_patterns=()):
    """IPs globais (v4 e v6), sem loopback e sem interfaces virtuais."""
    ips = []
    try:
        output = subprocess.check_output(
            ["ip", "-o", "addr", "show", "scope", "global"],
            universal_newlines=True, stderr=subprocess.DEVNULL, timeout=10)
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 4 or parts[2] not in ("inet", "inet6"):
                continue
            ifname = parts[1].split("@")[0]
            if any(fnmatch.fnmatch(ifname, pat) for pat in ignore_patterns):
                continue
            addr = parts[3].split("/")[0]
            if addr not in ips:
                ips.append(addr)
    except (OSError, subprocess.SubprocessError):
        try:  # iproute2 ausente: cai para 'hostname -I'
            output = subprocess.check_output(["hostname", "-I"], universal_newlines=True,
                                             stderr=subprocess.DEVNULL, timeout=10)
            ips = output.split()
        except (OSError, subprocess.SubprocessError) as exc:
            log.error("Erro ao obter IPs da máquina: %s", exc)
            return "Erro ao obter IPs"
    return ", ".join(ips) if ips else "Nenhum IP detectado"


# --------------------------------------------------------------------------
# Alertas (conteúdo das mensagens)
# --------------------------------------------------------------------------

class Alert(object):
    """kind: new | reminder | cleared | reboot | test"""

    def __init__(self, kind, hostname, ips, updates=(), reboot=False, reboot_pkgs=()):
        self.kind = kind
        self.hostname = hostname
        self.ips = ips
        self.updates = list(updates)
        self.reboot = reboot
        self.reboot_pkgs = list(reboot_pkgs)

    @property
    def kernel(self):
        return any(is_kernel_package(u.name) for u in self.updates)

    @property
    def security_count(self):
        return sum(1 for u in self.updates if u.security)

    @property
    def critical(self):
        return self.kind in ("new", "reminder") and self.kernel

    @property
    def title(self):
        if self.kind == "test":
            return "🧪 Teste do DNF Monitor"
        if self.kind == "cleared":
            return "✅ Servidor em dia"
        if self.kind == "reboot":
            return "🔁 Reinicialização necessária"
        if self.kernel:
            title = "🚨 CRÍTICO: ATUALIZAÇÃO DE KERNEL"
        elif self.security_count:
            title = "🔐 Atualizações de segurança disponíveis"
        else:
            title = "⚠️ Alerta de Atualização"
        return ("🔔 Lembrete: " + title) if self.kind == "reminder" else title

    @property
    def subject(self):
        if self.kind == "test":
            tag = "Teste de notificação"
        elif self.kind == "cleared":
            tag = "Todas as atualizações foram instaladas"
        elif self.kind == "reboot":
            tag = "Reinicialização necessária"
        elif self.kernel:
            tag = "🚨 CRÍTICO: Kernel Update"
        elif self.security_count:
            tag = "🔐 Atualizações de segurança (%d)" % self.security_count
        else:
            tag = "Atualizações Disponíveis"
        if self.kind == "reminder":
            tag = "Lembrete - " + tag
        return "[%s] %s" % (self.hostname, tag)

    @property
    def accent(self):
        if self.kind in ("cleared", "test"):
            return "#198754"
        if self.critical:
            return "#d9534f"
        if self.kind == "reboot" or self.security_count:
            return "#f0ad4e"
        return "#0275d8"

    def summary_line(self):
        qtd = len(self.updates)
        if self.kind == "test":
            return "Esta é uma mensagem de teste: as notificações estão funcionando."
        if self.kind == "cleared":
            return "Não há mais pacotes pendentes de atualização."
        if self.kind == "reboot":
            return "Atualizações já instaladas exigem reiniciar o servidor."
        line = "Temos %d pacote%s pronto%s para atualizar" % (qtd, "" if qtd == 1 else "s", "" if qtd == 1 else "s")
        if self.security_count:
            line += " (%d de segurança)" % self.security_count
        return line + "."

    # ---- Telegram (HTML) ------------------------------------------------
    def telegram_text(self):
        esc = html.escape
        lines = ["<b>%s</b>" % esc(self.title),
                 "🖥️ <b>Servidor:</b> %s" % esc(self.hostname),
                 "🌐 <b>IPs:</b> %s" % esc(self.ips), "",
                 esc(self.summary_line())]
        if self.updates and self.kind != "cleared":
            names = [esc(u.name) for u in self.updates[:5]]
            more = "..." if len(self.updates) > 5 else ""
            lines.append("📦 Alguns deles: %s%s" % (", ".join(names), more))
        if self.reboot:
            lines.append("🔁 <b>Reinicialização pendente</b>" +
                         (" (%s)" % esc(", ".join(self.reboot_pkgs[:5])) if self.reboot_pkgs else ""))
        return "\n".join(lines)

    # ---- E-mail ---------------------------------------------------------
    def email_text(self):
        lines = [self.title, "", "Servidor: %s" % self.hostname, "IPs: %s" % self.ips, "", self.summary_line(), ""]
        for u in self.updates:
            lines.append("  - %s (%s -> %s)%s" % (u.name, u.installed, u.candidate, " [segurança]" if u.security else ""))
        if self.reboot:
            lines += ["", "Reinicialização pendente" +
                      (": " + ", ".join(self.reboot_pkgs) if self.reboot_pkgs else ".")]
        lines += ["", "-- Gerado automaticamente pelo DNF Monitor Daemon"]
        return "\n".join(lines)

    def email_html(self):
        esc = html.escape
        rows = "".join(
            "<tr><td style=\"padding:3px 8px;\">{n}</td><td style=\"padding:3px 8px;color:#777;\">{i}</td>"
            "<td style=\"padding:3px 8px;\">{c}</td><td style=\"padding:3px 8px;\">{s}</td></tr>".format(
                n=esc(u.name), i=esc(u.installed), c=esc(u.candidate),
                s="🔐 segurança" if u.security else "")
            for u in self.updates)
        table = ""
        if rows and self.kind != "cleared":
            table = ("<h3>Lista de Pacotes:</h3><table style=\"background-color:#f1f1f1;border-radius:5px;"
                     "font-size:13px;width:100%;border-collapse:collapse;\">"
                     "<tr style=\"text-align:left;\"><th style=\"padding:3px 8px;\">Pacote</th>"
                     "<th style=\"padding:3px 8px;\">Instalada</th><th style=\"padding:3px 8px;\">Nova</th>"
                     "<th></th></tr>" + rows + "</table>")
        reboot = ""
        if self.reboot:
            reboot = ("<p style=\"background:#fff3cd;padding:10px;border-radius:5px;\">🔁 <strong>Reinicialização "
                      "pendente</strong>%s</p>" % (" (%s)" % esc(", ".join(self.reboot_pkgs)) if self.reboot_pkgs else ""))
        return (
            "<html><body style=\"font-family:Arial,sans-serif;color:#333;line-height:1.6;max-width:600px;"
            "margin:0 auto;padding:20px;border:1px solid #ddd;border-radius:8px;\">"
            "<h2 style=\"color:{accent};border-bottom:2px solid {accent};padding-bottom:10px;\">{title}</h2>"
            "<p><strong>Servidor:</strong> {host}</p><p><strong>Endereços IP:</strong> {ips}</p>"
            "<div style=\"background-color:#f9f9f9;padding:15px;border-left:4px solid {accent};margin:20px 0;\">"
            "<p style=\"margin:0;font-size:16px;\">{summary}</p></div>{reboot}{table}"
            "<hr style=\"border:0;border-top:1px solid #eee;margin-top:30px;\">"
            "<p style=\"font-size:12px;color:#777;text-align:center;\">Gerado automaticamente pelo DNF Monitor "
            "Daemon</p></body></html>"
        ).format(accent=self.accent, title=esc(self.title), host=esc(self.hostname), ips=esc(self.ips),
                 summary=esc(self.summary_line()), reboot=reboot, table=table)


# --------------------------------------------------------------------------
# Notificadores
# --------------------------------------------------------------------------

class TelegramNotifier(object):
    name = "telegram"
    TIMEOUT = 15
    ATTEMPTS = 3

    def __init__(self, token, chat_ids, api_base=DEFAULT_API_BASE, sleep=time.sleep):
        self.token = token
        self.chat_ids = chat_ids
        self.api_base = api_base.rstrip("/")
        self.sleep = sleep

    def _post(self, chat_id, text):
        url = "%s/bot%s/sendMessage" % (self.api_base, self.token)
        body = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                           "disable_web_page_preview": True}).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json", "User-Agent": "dnf-monitor/" + __version__})
        with urllib.request.urlopen(request, timeout=self.TIMEOUT) as response:
            response.read()

    def send(self, alert):
        text = alert.telegram_text()
        ok_any = False
        for chat_id in self.chat_ids:
            for attempt in range(1, self.ATTEMPTS + 1):
                try:
                    self._post(chat_id, text)
                    log.info("Telegram enviado com sucesso para Chat ID: %s", chat_id)
                    ok_any = True
                    break
                except urllib.error.HTTPError as exc:
                    detail, retry_after = self._describe(exc)
                    if exc.code == 429 or exc.code >= 500:
                        wait = min(retry_after or 2 * attempt, 30)
                        log.warning("Telegram HTTP %s para %s (tentativa %d/%d): %s",
                                    exc.code, chat_id, attempt, self.ATTEMPTS, detail)
                        if attempt < self.ATTEMPTS:
                            self.sleep(wait)
                        continue
                    log.error("Falha ao enviar Telegram para Chat ID %s: HTTP %s - %s", chat_id, exc.code, detail)
                    break
                except (urllib.error.URLError, socket.timeout, OSError) as exc:
                    log.warning("Erro de rede no Telegram para %s (tentativa %d/%d): %s",
                                chat_id, attempt, self.ATTEMPTS, getattr(exc, "reason", exc))
                    if attempt < self.ATTEMPTS:
                        self.sleep(2 * attempt)
            else:
                log.error("Falha ao enviar Telegram para Chat ID %s após %d tentativas.", chat_id, self.ATTEMPTS)
        return ok_any

    @staticmethod
    def _describe(exc):
        try:
            data = json.loads(exc.read().decode("utf-8", "replace"))
            return data.get("description", exc.reason), (data.get("parameters") or {}).get("retry_after")
        except Exception:
            return exc.reason, None


class EmailNotifier(object):
    name = "email"
    TIMEOUT = 30

    def __init__(self, settings):
        self.s = settings

    def _connect(self):
        s = self.s
        security = s["security"]
        context = ssl.create_default_context()
        if security == "auto" and s["port"] == 465:
            security = "ssl"
        if security == "ssl":
            server = smtplib.SMTP_SSL(s["server"], s["port"], timeout=self.TIMEOUT, context=context)
            server.ehlo()
        else:
            server = smtplib.SMTP(s["server"], s["port"], timeout=self.TIMEOUT)
            server.ehlo()
            if security == "starttls" or (security == "auto" and server.has_extn("starttls")):
                server.starttls(context=context)
                server.ehlo()
            elif security == "auto" and s["user"]:
                server.quit()
                raise smtplib.SMTPNotSupportedError(
                    "o servidor não oferece STARTTLS; recusando enviar credenciais sem criptografia "
                    "(use SMTP_SECURITY=none para forçar)")
        if s["user"]:
            server.login(s["user"], s["password"])
        return server

    def _build(self, alert, recipient):
        msg = MIMEMultipart("alternative")
        msg["Subject"] = alert.subject
        msg["From"] = formataddr((self.s["sender_name"], self.s["sender_email"]), charset="utf-8")
        msg["To"] = recipient
        msg["Date"] = formatdate(localtime=True)
        domain = self.s["sender_email"].rpartition("@")[2] or None
        msg["Message-ID"] = make_msgid(domain=domain)
        msg["X-Auto-Response-Suppress"] = "All"
        msg.attach(MIMEText(alert.email_text(), "plain", "utf-8"))
        msg.attach(MIMEText(alert.email_html(), "html", "utf-8"))
        return msg

    def send(self, alert):
        try:
            server = self._connect()
        except Exception as exc:
            log.error("Falha ao conectar no SMTP %s:%s - %s", self.s["server"], self.s["port"], exc)
            return False
        ok_any = False
        try:
            for recipient in self.s["recipients"]:
                try:
                    server.sendmail(self.s["sender_email"], [recipient], self._build(alert, recipient).as_string())
                    log.info("E-mail enviado com sucesso para: %s", recipient)
                    ok_any = True
                except Exception as exc:  # um destinatário ruim não derruba os demais
                    log.error("Falha ao enviar e-mail para %s: %s", recipient, exc)
        finally:
            try:
                server.quit()
            except Exception:
                pass
        return ok_any


class PrintNotifier(object):
    """Usado em --dry-run: mostra o que seria enviado."""
    name = "dry-run"

    def send(self, alert):
        print("----- [dry-run] %s -----" % alert.subject)
        print(alert.email_text())
        print("----- [dry-run] Telegram (HTML) -----")
        print(alert.telegram_text())
        return True


def build_notifiers(cfg):
    notifiers = []
    if cfg.telegram_enabled:
        notifiers.append(TelegramNotifier(cfg.bot_token, cfg.chat_ids, cfg.api_base))
    if cfg.email_enabled:
        notifiers.append(EmailNotifier(cfg.email))
    return notifiers


# --------------------------------------------------------------------------
# Estado persistente e lógica de decisão
# --------------------------------------------------------------------------

class State(object):
    """Guarda o que já foi notificado, para sobreviver a restart/reboot."""

    def __init__(self, path):
        self.path = path
        self.packages = None        # None = nunca avaliado; set() = nada pendente
        self.last_notified = 0.0
        self.reboot_notified = False

    def load(self):
        if not self.path:
            return self
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
            if data.get("packages") is not None:
                self.packages = set(data["packages"])
            self.last_notified = float(data.get("last_notified", 0))
            self.reboot_notified = bool(data.get("reboot_notified", False))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            log.warning("Estado %s ilegível (%s); começando do zero.", self.path, exc)
        return self

    def save(self):
        if not self.path:
            return
        data = {"packages": None if self.packages is None else sorted(self.packages),
                "last_notified": self.last_notified, "reboot_notified": self.reboot_notified}
        try:
            directory = os.path.dirname(self.path) or "."
            os.makedirs(directory, mode=0o750, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".state-", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("Não foi possível gravar o estado em %s: %s", self.path, exc)


def package_keys(updates):
    return set("%s=%s" % (u.name, u.candidate) for u in updates)


def decide(state, updates, now, reminder_days=0.0):
    """Decide o que fazer: 'new' | 'reminder' | 'cleared' | 'sync' | None.

    - new:      surgiu pacote/versão ainda não notificado
    - reminder: nada novo, mas o lembrete periódico venceu
    - cleared:  tudo foi atualizado (o chamador decide se avisa)
    - sync:     só atualizar o estado (ex.: o admin instalou parte dos pacotes)
    """
    current = package_keys(updates)
    previous = state.packages
    if not current:
        if previous:
            return "cleared"
        return "sync" if previous is None else None
    if previous is None or (current - previous):
        return "new"
    if current != previous:
        return "sync"
    if reminder_days > 0 and now - state.last_notified >= reminder_days * 86400:
        return "reminder"
    return None


class Monitor(object):
    def __init__(self, cfg, notifiers, state, collector=collect_updates, hostname=None,
                 ips_func=None, reboot_func=reboot_required, clock=time.time, persist=True):
        self.cfg = cfg
        self.notifiers = notifiers
        self.state = state
        self.collector = collector
        self.hostname = hostname or socket.gethostname()
        self.ips_func = ips_func or (lambda: get_machine_ips(cfg.ignore_interfaces))
        self.reboot_func = reboot_func
        self.clock = clock
        self.persist = persist

    def _dispatch(self, alert):
        results = [n.send(alert) for n in self.notifiers]
        return any(results)

    def _commit(self):
        if self.persist:
            self.state.save()

    def run_cycle(self):
        """Executa uma checagem. Retorna False se algo precisa de nova tentativa."""
        updates = self.collector(self.cfg.update_cache)
        if updates is None:
            log.warning("Não foi possível ler as atualizações do DNF; ciclo ignorado.")
            return False

        now = self.clock()
        needs_reboot, reboot_pkgs = self.reboot_func()
        action = decide(self.state, updates, now, self.cfg.reminder_days)
        ips = self.ips_func()
        healthy = True
        update_alert_sent = False

        if action in ("new", "reminder"):
            kernel = any(is_kernel_package(u.name) for u in updates)
            log.info("Encontrados %d pacotes pendentes. Crítico (Kernel): %s", len(updates), kernel)
            alert = Alert(action, self.hostname, ips, updates, needs_reboot, reboot_pkgs)
            if self._dispatch(alert):
                self.state.packages = package_keys(updates)
                self.state.last_notified = now
                update_alert_sent = True
                if needs_reboot:
                    self.state.reboot_notified = True
            else:
                log.error("Nenhum canal conseguiu enviar o alerta; nova tentativa em breve.")
                healthy = False
        elif action == "cleared":
            log.info("Todos os pacotes foram atualizados. Aguardando novas atualizações.")
            sent = True
            if self.cfg.notify_on_clear:
                sent = self._dispatch(Alert("cleared", self.hostname, ips))
            if sent:
                self.state.packages = set()
            else:
                healthy = False
        elif action == "sync":
            self.state.packages = package_keys(updates)
            if not updates:
                log.info("Nenhum pacote pendente.")

        if not needs_reboot:
            self.state.reboot_notified = False
        elif not update_alert_sent and not self.state.reboot_notified:
            if self._dispatch(Alert("reboot", self.hostname, ips, [], True, reboot_pkgs)):
                self.state.reboot_notified = True
            else:
                healthy = False

        self._commit()
        return healthy


# --------------------------------------------------------------------------
# Daemon
# --------------------------------------------------------------------------

class Daemon(object):
    def __init__(self, args):
        self.args = args
        self.wake = threading.Event()
        self.stop = False
        self.reload = False

    def _on_term(self, signum, _frame):
        log.info("Sinal %s recebido; encerrando.", signum)
        self.stop = True
        self.wake.set()

    def _on_hup(self, _signum, _frame):
        self.reload = True
        self.wake.set()

    def build(self, cfg):
        state = State(cfg.state_file).load()
        notifiers = [PrintNotifier()] if self.args.dry_run else build_notifiers(cfg)
        def collector(update_cache):
            return collect_updates(update_cache, cfg.skip_broken_repos, cfg.collect_timeout, cfg.load_plugins)

        return Monitor(cfg, notifiers, state, collector=collector, persist=not self.args.dry_run)

    def run(self):
        cfg = load_config(self.args.config)
        monitor = self.build(cfg)
        signal.signal(signal.SIGTERM, self._on_term)
        signal.signal(signal.SIGINT, self._on_term)
        signal.signal(signal.SIGHUP, self._on_hup)
        log.info("=== Iniciando DNF Monitor %s em %s ===", __version__, monitor.hostname)
        log.info("Canais: %s | intervalo: %s hora(s)", ", ".join(cfg.channels()), cfg.interval_hours)

        while not self.stop:
            healthy = monitor.run_cycle()
            delay = cfg.interval_seconds if healthy else min(cfg.interval_seconds, RETRY_INTERVAL)
            self.wake.clear()
            self.wake.wait(delay)
            if self.reload and not self.stop:
                self.reload = False
                try:
                    cfg = load_config(self.args.config)
                    monitor = self.build(cfg)
                    log.info("Configuração recarregada (canais: %s).", ", ".join(cfg.channels()))
                except ConfigProblem as exc:
                    log.error("Recarga ignorada, mantendo a configuração anterior: %s", exc)
        return 0


def test_notify(cfg):
    alert = Alert("test", socket.gethostname(), get_machine_ips(cfg.ignore_interfaces),
                  [Update("pacote-exemplo", "1.0", "1.1", True)])
    results = {n.name: n.send(alert) for n in build_notifiers(cfg)}
    for name, ok in sorted(results.items()):
        log.info("Teste %s: %s", name, "OK" if ok else "FALHOU")
    return 0 if all(results.values()) else 1


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="dnf-monitor", description="Daemon de monitoramento de atualizações DNF.")
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG, help="arquivo de configuração")
    parser.add_argument("--once", action="store_true", help="faz uma única checagem e sai")
    parser.add_argument("--dry-run", action="store_true", help="mostra os alertas sem enviar nem gravar estado")
    parser.add_argument("--test-notify", action="store_true", help="envia uma mensagem de teste e sai")
    parser.add_argument("--check-config", action="store_true", help="valida a configuração e sai")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version="dnf-monitor " + __version__)
    return parser.parse_args(argv)


def main(argv=None):
    raw = sys.argv[1:] if argv is None else list(argv)
    if "--collect-json" in raw:         # modo interno: processo filho que consulta o DNF
        return collect_main(raw)
    args = parse_args(raw)
    setup_logging(None, args.verbose)       # log mínimo até ler a configuração
    try:
        cfg = load_config(args.config)
    except ConfigProblem as exc:
        log.error("%s", exc)
        return EX_CONFIG

    interactive = args.check_config or args.test_notify or args.once or args.dry_run
    setup_logging(None if interactive and not os.access(os.path.dirname(cfg.log_file) or ".", os.W_OK)
                  else cfg.log_file, args.verbose)

    if args.check_config:
        log.info("Configuração OK. Canais ativos: %s", ", ".join(cfg.channels()))
        return 0
    if args.test_notify:
        return test_notify(cfg)
    if args.once or args.dry_run:
        monitor = Daemon(args).build(cfg)
        return 0 if monitor.run_cycle() else 1
    try:
        return Daemon(args).run()
    except ConfigProblem as exc:
        log.error("%s", exc)
        return EX_CONFIG


if __name__ == "__main__":
    sys.exit(main())
