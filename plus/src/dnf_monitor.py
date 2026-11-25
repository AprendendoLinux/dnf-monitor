#!/usr/bin/python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""DNF Monitor Plus.

Monitora atualizações de pacotes DNF em servidores RHEL e derivados (Rocky, AlmaLinux, Oracle Linux, CentOS Stream...)
e envia alertas por Telegram e/ou e-mail (HTML + texto puro). Na edição Plus, também guarda o histórico do que o
administrador atualizou e envia ao cliente um relatório diário.

Compatível com Python >= 3.6 (EL8+). Depende apenas de ``python3-dnf``; o restante usa somente a biblioteca padrão
(o SQLite e o fuso horário vêm da própria biblioteca padrão e do ``tzdata``).
"""

import argparse
import fnmatch
import glob
import html
import json
import logging
import logging.handlers
import os
import re
import signal
import smtplib
import socket
import sqlite3
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
from contextlib import contextmanager
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

__version__ = "1.0.0"
PRODUCT = "DNF Monitor Plus"
PROG = "dnf-monitor-plus"

DEFAULT_CONFIG = "/etc/dnf-monitor-plus/dnf-monitor.conf"
DEFAULT_STATE = "/var/lib/dnf-monitor-plus/state.json"
DEFAULT_LOG = "/var/log/dnf-monitor-plus.log"
DEFAULT_API_BASE = "https://api.telegram.org"
DEFAULT_IGNORE_INTERFACES = "lo,docker*,br-*,veth*,virbr*,cni*,flannel*,cali*,tun*,tap*,podman*"
DEFAULT_DB = "/var/lib/dnf-monitor-plus/history.db"
DEFAULT_REPORT_TIME = "22:30"
DEFAULT_REPORT_TZ = "America/Sao_Paulo"
DNF_HISTORY_DB = "/var/lib/dnf/history.sqlite"
# O que muda quando o DNF baixa metadados novos ou o rpm instala pacotes (sem os arquivos -shm, que mudam até em leituras).
DNF_WATCH = ("/var/cache/dnf/*/repodata/repomd.xml", "/var/lib/rpm/Packages", "/var/lib/rpm/rpmdb.sqlite",
             "/var/lib/rpm/rpmdb.sqlite-wal")
ZONEINFO_DIR = "/usr/share/zoneinfo"
COLLECT_MARKER = "@@DNF-MONITOR-JSON@@"
EX_CONFIG = 78          # sysexits.h: erro de configuração (systemd não reinicia)
MIN_INTERVAL = 60       # segundos
RETRY_INTERVAL = 900    # segundos, após falha de envio / de leitura do dnf
POLL_INTERVAL = 60      # segundos entre as olhadas no histórico do DNF / horário do relatório
REPO_SETTLE = 20        # segundos sem mexer nesses arquivos até considerar que o DNF terminou
INPROGRESS_GRACE = 6 * 3600     # segundos: transação do DNF sem fim e mais nova que isso ainda pode estar rodando

log = logging.getLogger(PROG)


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
        # Relatório diário para o cliente (segundo bot / segundo conjunto de e-mails)
        self.client_bot_token = ""
        self.client_chat_ids = []
        self.client_api_base = DEFAULT_API_BASE
        self.client_email = None
        self.admin_name = ""
        self.report_time = (22, 30)
        self.report_tz = DEFAULT_REPORT_TZ
        self.report_send_empty = False
        self.report_package_list = True
        self.pending_alert_days = 3.0
        self.db_file = DEFAULT_DB

    @property
    def telegram_enabled(self):
        return bool(self.bot_token and self.chat_ids)

    @property
    def email_enabled(self):
        return self.email is not None

    @property
    def interval_seconds(self):
        return max(MIN_INTERVAL, int(self.interval_hours * 3600))

    @property
    def client_telegram_enabled(self):
        return bool(self.client_bot_token and self.client_chat_ids)

    @property
    def client_email_enabled(self):
        return self.client_email is not None

    def channels(self):
        names = []
        if self.telegram_enabled:
            names.append("telegram")
        if self.email_enabled:
            names.append("email")
        return names

    def client_channels(self):
        names = []
        if self.client_telegram_enabled:
            names.append("telegram")
        if self.client_email_enabled:
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


def parse_report_time(value):
    match = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", value or "")
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise ConfigProblem("REPORT_TIME inválido ('%s'): use HH:MM, de 00:00 a 23:59." % (value or "").strip())
    return int(match.group(1)), int(match.group(2))


def valid_timezone(name):
    if not name or name.startswith("/") or ".." in name.split("/"):
        return False
    return os.path.isfile(os.path.join(ZONEINFO_DIR, name))


def _parse_email(parser, section, label, sender_default="DNF Monitor"):
    """Lê uma seção SMTP ([Email] ou [ClientEmail]); None se desativada/exemplo."""
    if not parser.has_section(section) or not parser.options(section):
        return None         # seção ausente ou só com cabeçalho (tudo comentado): canal simplesmente desligado
    get = parser.get
    server = get(section, "SMTP_SERVER", fallback="").strip()
    recipients = [r for r in split_list(get(section, "RECIPIENT_EMAILS", fallback="")) if not is_placeholder(r)]
    if is_placeholder(server) or not recipients:
        log.warning("%s não configurado (servidor/destinatários ausentes ou de exemplo): canal desativado.", label)
        return None
    try:
        port = parser.getint(section, "SMTP_PORT", fallback=587)
    except ValueError as exc:
        raise ConfigProblem("SMTP_PORT inválido em [%s]: %s" % (section, exc))
    security = get(section, "SMTP_SECURITY", fallback="auto").strip().lower()
    if security not in ("auto", "ssl", "starttls", "none"):
        raise ConfigProblem("SMTP_SECURITY deve ser auto, ssl, starttls ou none (seção [%s])." % section)
    user = get(section, "SMTP_USER", fallback="").strip()
    sender = get(section, "SENDER_EMAIL", fallback=user).strip()
    if not sender:
        raise ConfigProblem("Defina SENDER_EMAIL (ou SMTP_USER) na seção [%s]." % section)
    return {
        "server": server,
        "port": port,
        "security": security,
        "user": user,
        "password": get(section, "SMTP_PASS", fallback="").strip(),
        "sender_name": get(section, "SENDER_NAME", fallback=sender_default).strip(),
        "sender_email": sender,
        "recipients": recipients,
    }


def load_config(path):
    if not os.path.isfile(path):
        raise ConfigProblem("Arquivo de configuração %s não encontrado." % path)

    # strict=False: seção repetida é mesclada (vale a última chave) em vez de recusar o arquivo; assim quem
    # cola de novo um bloco como [Report] por cima dos cabeçalhos já existentes não derruba o serviço.
    parser = ConfigParser(inline_comment_prefixes=("#", ";"), interpolation=None, strict=False)
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

    cfg.email = _parse_email(parser, "Email", "E-mail")

    # ---- Relatório diário para o cliente ---------------------------------
    cfg.admin_name = get("Report", "ADMIN_NAME", fallback="").strip()
    cfg.report_send_empty = parse_bool(get("Report", "SEND_EMPTY", fallback="false"))
    cfg.report_package_list = parse_bool(get("Report", "PACKAGE_LIST", fallback="true"))
    cfg.db_file = get("Report", "DB_FILE", fallback=DEFAULT_DB).strip()
    try:
        cfg.pending_alert_days = parser.getfloat("Report", "PENDING_ALERT_DAYS", fallback=3.0)
    except ValueError as exc:
        raise ConfigProblem("PENDING_ALERT_DAYS inválido: %s" % exc)
    if cfg.pending_alert_days < 0:
        raise ConfigProblem("PENDING_ALERT_DAYS não pode ser negativo (0 desativa).")
    cfg.report_time = parse_report_time(get("Report", "REPORT_TIME", fallback=DEFAULT_REPORT_TIME))
    cfg.report_tz = get("Report", "REPORT_TIMEZONE", fallback=DEFAULT_REPORT_TZ).strip()
    if not valid_timezone(cfg.report_tz):
        raise ConfigProblem("REPORT_TIMEZONE '%s' não existe em %s (ex.: America/Sao_Paulo)." % (cfg.report_tz, ZONEINFO_DIR))

    client_token = normalize_token(get("ClientTelegram", "BOT_TOKEN", fallback=""))
    client_chats = split_list(get("ClientTelegram", "CHAT_IDS", fallback=""))
    if client_token and not is_placeholder(client_token) and client_chats:
        cfg.client_bot_token = client_token
        cfg.client_chat_ids = client_chats
        cfg.client_api_base = get("ClientTelegram", "API_BASE", fallback=DEFAULT_API_BASE).strip().rstrip("/")
    cfg.client_email = _parse_email(parser, "ClientEmail", "E-mail do cliente", sender_default="DNF Monitor")

    REDACTOR.add(cfg.bot_token, (cfg.email or {}).get("password"),
                 cfg.client_bot_token, (cfg.client_email or {}).get("password"))

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
# Fuso horário (sem depender de zoneinfo, que só existe no Python >= 3.9)
# --------------------------------------------------------------------------

@contextmanager
def timezone(name):
    """Troca temporariamente o fuso do processo (o daemon é single-thread)."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def local_format(ts, tz, fmt):
    with timezone(tz):
        return time.strftime(fmt, time.localtime(ts))


def local_day(ts, tz):
    return local_format(ts, tz, "%Y-%m-%d")


def report_due(now, tz, report_time):
    """True quando, no fuso do relatório, o horário configurado de hoje já passou."""
    with timezone(tz):
        lt = time.localtime(now)
    return (lt.tm_hour, lt.tm_min) >= tuple(report_time)


def next_report_ts(now, tz, report_time):
    """Instante (epoch) do próximo horário de relatório."""
    with timezone(tz):
        lt = time.localtime(now)
        target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, report_time[0], report_time[1], 0, 0, 0, -1))
        if target <= now:
            target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, report_time[0], report_time[1], 0, 0, 0, -1))
    return target


# --------------------------------------------------------------------------
# Histórico de atualizações realizadas (banco de transações do DNF + SQLite próprio)
# --------------------------------------------------------------------------

HistoryEntry = namedtuple("HistoryEntry", "start_ts end_ts commandline requested_by automatic error upgrades")
PackageResult = namedtuple("PackageResult", "name old new ok")
RunRecord = namedtuple("RunRecord", "id start_ts end_ts ok failed packages")
PendingPackage = namedtuple("PendingPackage", "name installed candidate security first_seen")

# Códigos do banco de transações do DNF (libdnf), iguais no EL8, EL9 e EL10 (conferido em Rocky, Alma e Oracle Linux).
ACTION_INSTALL = 1      # pacote instalado (inclusive o kernel novo: kernel é "installonly", não vira upgrade)
ACTION_UPGRADE = 6      # pacote novo de uma atualização
ACTION_UPGRADED = 7     # pacote antigo, substituído por uma atualização
STATE_DONE = 1


def repo_signature(patterns=DNF_WATCH):
    """Quando foram modificados os arquivos que o DNF/rpm mexem ao baixar metadados ou instalar pacotes."""
    items = []
    for pattern in patterns:
        for path in glob.glob(pattern):
            try:
                items.append((path, os.stat(path).st_mtime))
            except OSError:
                pass
    return tuple(sorted(items))


def repo_changed(previous, current, now, settle=REPO_SETTLE):
    """True se o DNF/rpm mexeu nos arquivos e já terminou (evita checar no meio de um 'dnf makecache')."""
    if current == previous:
        return False
    newest = max([mtime for _path, mtime in current] or [0.0])
    return now - newest >= settle


def short_name(name):
    return name


def _evr_text(epoch, version, release):
    return "%s%s-%s" % ("%s:" % epoch if epoch not in (0, "0", None, "") else "", version, release)


def read_dnf_history(path=DNF_HISTORY_DB, since_ts=0):
    """Lê as transações do DNF iniciadas desde ``since_ts`` e devolve as que atualizaram pacotes.

    Atualização = pacote com ação *Upgrade* ou kernel novo (instalado em paralelo, "installonly"). Instalar ou remover
    pacotes comuns não conta. Transações sem linha de comando (dnf-automatic, PackageKit... que usam a API) são
    marcadas como automáticas. Sucesso por pacote vem do próprio banco do DNF.
    """
    db = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=10)
    entries = []
    try:
        transactions = db.execute(
            "SELECT id, dt_begin, dt_end, user_id, cmdline, state FROM trans WHERE dt_begin >= ? ORDER BY id",
            (int(since_ts) - 1,)).fetchall()
        for tid, begin, end, uid, cmdline, state in transactions:
            rows = db.execute(
                "SELECT ti.action, ti.state, r.name, r.epoch, r.version, r.release FROM trans_item ti "
                "JOIN rpm r ON r.item_id = ti.item_id WHERE ti.trans_id = ? AND ti.action IN (?, ?, ?)",
                (tid, ACTION_INSTALL, ACTION_UPGRADE, ACTION_UPGRADED)).fetchall()
            old = {}
            for action, _item_state, name, epoch, version, release in rows:
                if action == ACTION_UPGRADED:
                    old[name] = _evr_text(epoch, version, release)
            upgrades = []
            for action, item_state, name, epoch, version, release in rows:
                if action == ACTION_UPGRADE or (action == ACTION_INSTALL and is_kernel_package(name)):
                    upgrades.append((name, old.get(name, "-"), _evr_text(epoch, version, release),
                                     item_state == STATE_DONE))
            cmdline = (cmdline or "").strip()
            automatic = cmdline == "" or "dnf-automatic" in cmdline
            error = "" if state in (STATE_DONE, None) or end is None else "transação com erro (estado %s)" % state
            entries.append(HistoryEntry(float(begin), None if end is None else float(end), cmdline,
                                        "" if uid in (None, -1, 4294967295) else str(uid), automatic, error, upgrades))
    finally:
        db.close()
    return entries


class HistoryDB(object):
    """Memória do monitor: o que o administrador atualizou e o que já foi reportado."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        start_ts INTEGER NOT NULL, end_ts INTEGER, commandline TEXT NOT NULL DEFAULT '',
        ok INTEGER NOT NULL, failed INTEGER NOT NULL,
        reported_day TEXT,
        UNIQUE (start_ts, commandline));
    CREATE TABLE IF NOT EXISTS run_packages (
        run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        name TEXT NOT NULL, old_version TEXT, new_version TEXT, ok INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_run_packages_run ON run_packages(run_id);
    CREATE TABLE IF NOT EXISTS pending (
        name TEXT PRIMARY KEY, installed TEXT, candidate TEXT, security INTEGER NOT NULL DEFAULT 0,
        first_seen INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS daily_reports (
        day TEXT NOT NULL, channel TEXT NOT NULL, sent_at INTEGER NOT NULL, skipped INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, channel));
    """

    def __init__(self, path, since_ts=None, clock=time.time):
        self.path = path
        if path != ":memory:":
            directory = os.path.dirname(path) or "."
            os.makedirs(directory, mode=0o750, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(self.SCHEMA)
        if path != ":memory:":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        row = self.db.execute("SELECT value FROM meta WHERE key = 'since_ts'").fetchone()
        if row is None:
            # Só entra o que acontecer a partir da criação do banco: nada de avisar o passado.
            since = clock() if since_ts is None else since_ts
            self.db.execute("INSERT INTO meta (key, value) VALUES ('since_ts', ?)", (str(since),))
            self.db.commit()
            row = (str(since),)
        self.since_ts = float(row[0])

    def close(self):
        self.db.close()

    def has_run(self, start_ts, commandline):
        return self.db.execute("SELECT 1 FROM runs WHERE start_ts = ? AND commandline = ?",
                               (int(start_ts), commandline)).fetchone() is not None

    def add_run(self, entry, results):
        """Grava uma execução; ``results`` é uma lista de PackageResult. Devolve o id (ou None se já existia)."""
        ok = sum(1 for r in results if r.ok)
        with self.db:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO runs (start_ts, end_ts, commandline, ok, failed) VALUES (?, ?, ?, ?, ?)",
                (int(entry.start_ts), None if entry.end_ts is None else int(entry.end_ts),
                 entry.commandline, ok, len(results) - ok))
            if cur.rowcount == 0:
                return None
            run_id = cur.lastrowid
            self.db.executemany(
                "INSERT INTO run_packages (run_id, name, old_version, new_version, ok) VALUES (?, ?, ?, ?, ?)",
                [(run_id, r.name, r.old, r.new, 1 if r.ok else 0) for r in results])
        return run_id

    def _records(self, rows):
        records = []
        for run_id, start_ts, end_ts, ok, failed in rows:
            pkgs = [PackageResult(n, o, v, bool(k)) for n, o, v, k in self.db.execute(
                "SELECT name, old_version, new_version, ok FROM run_packages WHERE run_id = ? ORDER BY ok, name",
                (run_id,))]
            records.append(RunRecord(run_id, start_ts, end_ts, ok, failed, pkgs))
        return records

    def unreported_runs(self):
        return self._records(self.db.execute(
            "SELECT id, start_ts, end_ts, ok, failed FROM runs WHERE reported_day IS NULL ORDER BY start_ts").fetchall())

    def mark_reported(self, run_ids, day):
        with self.db:
            self.db.executemany("UPDATE runs SET reported_day = ? WHERE id = ?", [(day, i) for i in run_ids])

    def sync_pending(self, updates, now):
        """Espelha os pacotes pendentes. A data da 1ª vez que cada um foi visto só zera quando ele deixa de estar pendente."""
        current = {u.name: u for u in updates}
        with self.db:
            known = set(row[0] for row in self.db.execute("SELECT name FROM pending"))
            self.db.executemany("DELETE FROM pending WHERE name = ?", [(n,) for n in known - set(current)])
            for name, u in current.items():
                if name in known:
                    self.db.execute("UPDATE pending SET installed = ?, candidate = ?, security = ? WHERE name = ?",
                                    (u.installed, u.candidate, 1 if u.security else 0, name))
                else:
                    self.db.execute("INSERT INTO pending (name, installed, candidate, security, first_seen) VALUES (?, ?, ?, ?, ?)",
                                    (name, u.installed, u.candidate, 1 if u.security else 0, int(now)))

    def pending_packages(self):
        return [PendingPackage(n, i, c, bool(sec), f) for n, i, c, sec, f in self.db.execute(
            "SELECT name, installed, candidate, security, first_seen FROM pending ORDER BY first_seen, name")]

    def overdue(self, now, days):
        """Todos os pendentes, se algum espera há MAIS de ``days`` dias; senão lista vazia (0 desativa)."""
        if not days or days <= 0:
            return []
        packages = self.pending_packages()
        return packages if any(now - p.first_seen > days * 86400 for p in packages) else []

    def channels_done(self, day):
        return set(row[0] for row in self.db.execute("SELECT channel FROM daily_reports WHERE day = ?", (day,)))

    def mark_channel_done(self, day, channel, ts, skipped=False):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO daily_reports (day, channel, sent_at, skipped) VALUES (?, ?, ?, ?)",
                            (day, channel, int(ts), 1 if skipped else 0))


# --------------------------------------------------------------------------
# Alertas (conteúdo das mensagens)
# --------------------------------------------------------------------------

class Alert(object):
    """kind: new | reminder | cleared | reboot | test"""

    def __init__(self, kind, hostname, ips, updates=(), reboot=False, reboot_pkgs=(), detected_at=None,
                 tz=DEFAULT_REPORT_TZ):
        self.kind = kind
        self.detected_at = detected_at
        self.tz = tz
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
        lines += ["", "-- Gerado automaticamente pelo DNF Monitor Plus"]
        return "\n".join(lines)

    @property
    def headline(self):
        """Título sem emoji, para o cabeçalho do e-mail."""
        if self.kind == "test":
            return "Teste do DNF Monitor"
        if self.kind == "cleared":
            return "Servidor em dia"
        if self.kind == "reboot":
            return "Reinicialização necessária"
        if self.kernel:
            text = "Atualização de kernel disponível"
        elif self.security_count:
            text = "Atualizações de segurança disponíveis"
        else:
            text = "Atualizações disponíveis"
        return ("Lembrete: " + text) if self.kind == "reminder" else text

    @property
    def kernel_count(self):
        return sum(1 for u in self.updates if is_kernel_package(u.name))

    def _gradient(self):
        if self.kind in ("cleared", "test"):
            return "#0f766e", "#16a34a"
        if self.critical:
            return "#991b1b", "#dc2626"
        if self.kind == "reboot" or self.security_count:
            return "#b45309", "#f59e0b"
        return "#1d4ed8", "#4f46e5"

    def _badge(self):
        if self.kind == "test":
            return _pill("🧪 TESTE", "#5b21b6", "#ede9fe")
        if self.kind == "cleared":
            return _pill("✓ EM DIA", "#166534", "#dcfce7")
        if self.kind == "reboot":
            return _pill("🔁 REINICIAR", "#92400e", "#fef3c7")
        if self.kernel:
            return _pill("🚨 CRÍTICO", "#991b1b", "#fee2e2")
        if self.security_count:
            return _pill("🔐 SEGURANÇA", "#92400e", "#fef3c7")
        return _pill("📦 ATUALIZAÇÕES", "#1e40af", "#dbeafe")

    @staticmethod
    def _update_rows(updates):
        rows = []
        for index, u in enumerate(updates):
            background = "#ffffff" if index % 2 == 0 else "#f8fafc"
            tag = ""
            if is_kernel_package(u.name):
                tag = " " + _pill("🐧 Kernel", "#991b1b", "#fee2e2")
            elif u.security:
                tag = " " + _pill("🔐 Segurança", "#92400e", "#fef3c7")
            rows.append(
                "<tr><td bgcolor=\"%s\" style=\"background:%s;padding:10px 14px;border-bottom:1px solid #eef2f6;"
                "font:700 14px/1.9 %s;color:#0f172a;\">%s%s</td><td bgcolor=\"%s\" style=\"background:%s;padding:10px 14px 10px 8px;"
                "border-bottom:1px solid #eef2f6;font:400 12px/1.5 %s;color:%s;\">%s</td></tr>"
                % (background, background, FONT, _name_html(u.name), tag, background, background, MONO, SLATE,
                   _versions(u.installed, u.candidate)))
        return "".join(rows)

    def email_html(self):
        esc = html.escape
        c1, c2 = self._gradient()
        when = local_format(self.detected_at, self.tz, "%d/%m/%Y às %H:%M") if self.detected_at else ""
        total = len(self.updates)
        parts = []

        if self.kind in ("new", "reminder", "test") and self.updates:
            lead = "Encontramos <strong>%s</strong> pronto%s para instalar em <strong>%s</strong>" % (
                esc(plural(total, "pacote", "pacotes")), "" if total == 1 else "s", esc(self.hostname))
            if self.security_count:
                lead += ", sendo <strong>%d de segurança</strong>" % self.security_count
            lead += "."
        else:
            lead = esc(self.summary_line())
        parts.append("<p class=\"lead\" style=\"margin:0;font:400 17px/1.6 %s;color:#1e293b;\">%s</p>" % (FONT, lead))

        if self.updates and self.kind != "cleared":
            parts.append("<div style=\"margin-top:24px;\">%s</div>" % _stat_cards([
                (total, "Pendentes", BLUE),
                (self.security_count, "Segurança", AMBER if self.security_count else "#94a3b8"),
                (self.kernel_count, "Kernel", RED if self.kernel_count else "#94a3b8")]))

        meta = [("Servidor", "<strong>%s</strong>" % esc(self.hostname)), ("Endereços IP", esc(self.ips))]
        if when:
            meta.append(("Detectado em", esc(when)))
        parts.append(_meta_table(meta))

        if self.critical:
            parts.append(_callout("🚨", "Atualização de kernel", "Há uma nova imagem do kernel. Recomenda-se instalar o "
                                  "quanto antes e reiniciar o servidor em seguida.", RED, "#fef2f2", "#fecaca"))
        if self.security_count and not self.critical:
            parts.append(_callout("🔐", "Correções de segurança", "%s vêm de repositórios de segurança. Vale priorizar."
                                  % esc(plural(self.security_count, "pacote", "pacotes")), AMBER, "#fffbeb", "#fde68a"))
        if self.reboot:
            names = (" (%s)" % esc(", ".join(self.reboot_pkgs[:8]))) if self.reboot_pkgs else ""
            parts.append(_callout("🔁", "Reinicialização pendente", "O servidor precisa ser reiniciado para concluir "
                                  "atualizações já instaladas%s." % names, AMBER, "#fffbeb", "#fde68a"))

        if self.updates and self.kind != "cleared":
            parts.append(_section("Como atualizar"))
            parts.append("<table role=\"presentation\" width=\"100%%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\"><tr>"
                         "<td class=\"cmd\" bgcolor=\"#0f172a\" style=\"background:#0f172a;border-radius:10px;padding:14px 18px;"
                         "font:500 14px/1.6 %s;color:#e2e8f0;\"><span style=\"color:#64748b;\">$</span> "
                         "<span style=\"color:#86efac;\">sudo dnf upgrade --refresh</span></td></tr></table>" % MONO)
            parts.append(_section("Pacotes (%d)" % total))
            parts.append(
                "<table role=\"presentation\" width=\"100%%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" "
                "style=\"border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;\"><tr>"
                "<td bgcolor=\"#f1f5f9\" style=\"background:#f1f5f9;padding:9px 14px;font:700 11px/1 %(f)s;color:%(s)s;"
                "text-transform:uppercase;letter-spacing:.8px;\">Pacote</td><td bgcolor=\"#f1f5f9\" style=\"background:#f1f5f9;"
                "padding:9px 8px;font:700 11px/1 %(f)s;color:%(s)s;text-transform:uppercase;letter-spacing:.8px;\">Versão</td>"
                "</tr>%(rows)s</table>"
                % {"f": FONT, "s": SLATE, "rows": self._update_rows(self.updates[:100])})
            if total > 100:
                parts.append("<div style=\"font:400 12px/1.5 %s;color:%s;padding-top:8px;\">… e mais %d pacote(s).</div>"
                             % (FONT, SLATE, total - 100))

        footer = ("Alerta gerado automaticamente pelo <strong>DNF Monitor Plus</strong> · versão %s<br>%s · %s"
                  % (__version__, esc(self.hostname), esc(self.ips)))
        preheader = self.summary_line() + (" Há kernel entre eles." if self.kernel_count else "")
        return _email_shell(preheader, c1, c2, "Lembrete" if self.kind == "reminder" else "Alerta de atualização",
                            self.headline.replace("Lembrete: ", ""), "<strong>%s</strong>%s" % (
                                esc(self.hostname), (" · " + esc(when)) if when else ""),
                            self._badge(), "".join(parts), footer)


# --------------------------------------------------------------------------
# Peças visuais dos e-mails (tabelas + estilos inline: funciona em Gmail, Outlook e celulares)
# --------------------------------------------------------------------------

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"
MONO = "SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"
GREEN, RED, AMBER, BLUE, SLATE = "#16a34a", "#dc2626", "#d97706", "#2563eb", "#64748b"


def fmt_duration(seconds):
    seconds = int(max(0, seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return "%d h %02d min" % (hours, minutes)
    if minutes:
        return "%d min %02d s" % (minutes, secs)
    return "%d s" % secs


def _pill(text, color, background):
    return ("<span style=\"display:inline-block;padding:4px 11px;border-radius:999px;background:%s;color:%s;"
            "font:700 12px/1.2 %s;white-space:nowrap;\">%s</span>" % (background, color, FONT, text))


def _stat_cards(items):
    """items: [(valor, rótulo, cor)] — cartões lado a lado, de larguras iguais."""
    width = int(100 / len(items))
    cells = []
    for index, (value, label, color) in enumerate(items):
        left = 0 if index == 0 else 4
        right = 0 if index == len(items) - 1 else 4
        cells.append(
            "<td class=\"cc\" width=\"%d%%\" valign=\"top\" style=\"padding:0 %dpx 0 %dpx;\"><table role=\"presentation\" width=\"100%%\" "
            "cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"background:#ffffff;border:1px solid #e2e8f0;"
            "border-top:4px solid %s;border-radius:10px;\"><tr><td class=\"cp\" align=\"center\" valign=\"middle\" "
            "style=\"padding:16px 4px 13px;\"><div class=\"cn\" style=\"font:800 32px/1 %s;color:%s;\">%s</div>"
            "<div class=\"cl\" style=\"font:600 11px/1.3 %s;color:%s;text-transform:uppercase;letter-spacing:.9px;"
            "padding-top:7px;\">%s</div></td></tr></table></td>" % (width, right, left, color, FONT, color, value, FONT, SLATE, label))
    return ("<table class=\"cards c%d\" role=\"presentation\" width=\"100%%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" "
            "style=\"table-layout:fixed;\"><tr>%s</tr></table>" % (len(items), "".join(cells)))


def _success_bar(ok, failed):
    total = ok + failed
    if not total:
        return ""
    shown_pct = (100 * ok) // total          # arredonda para baixo: 100% só quando não há falha
    ok_pct = min(99, max(1, shown_pct)) if (failed and ok) else shown_pct
    bad = ("<td width=\"%d%%\" height=\"10\" bgcolor=\"%s\" style=\"background:%s;font-size:0;line-height:0;\">&nbsp;</td>"
           % (100 - ok_pct, RED, RED)) if failed else ""
    good = ("<td width=\"%d%%\" height=\"10\" bgcolor=\"%s\" style=\"background:%s;font-size:0;line-height:0;\">&nbsp;</td>"
            % (ok_pct if failed else 100, GREEN, GREEN)) if ok else ""
    return ("<table role=\"presentation\" width=\"100%%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" "
            "style=\"margin-top:20px;\"><tr><td style=\"font:600 12px/1 %s;color:%s;padding-bottom:7px;\">Taxa de sucesso</td>"
            "<td align=\"right\" style=\"font:800 13px/1 %s;color:%s;padding-bottom:7px;\">%d%%</td></tr>"
            "<tr><td colspan=\"2\"><table role=\"presentation\" width=\"100%%\" cellpadding=\"0\" cellspacing=\"0\" "
            "border=\"0\" bgcolor=\"#e2e8f0\" style=\"background:#e2e8f0;border-radius:6px;overflow:hidden;\"><tr>%s%s</tr>"
            "</table></td></tr></table>" % (FONT, SLATE, FONT, GREEN if not failed else (AMBER if shown_pct >= 50 else RED),
                                            shown_pct, good, bad))


def _callout(icon, title, text, color, background, border):
    return ("<table role=\"presentation\" width=\"100%%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" "
            "style=\"margin-top:16px;\"><tr><td bgcolor=\"%s\" style=\"background:%s;border:1px solid %s;"
            "border-left:5px solid %s;border-radius:8px;padding:13px 16px;\">"
            "<div style=\"font:700 14px/1.4 %s;color:%s;\">%s&nbsp; %s</div>"
            "<div style=\"font:400 13px/1.55 %s;color:#334155;padding-top:3px;\">%s</div></td></tr></table>"
            % (background, background, border, color, FONT, color, icon, title, FONT, text))


def _section(title):
    return ("<div style=\"font:800 12px/1 %s;color:%s;text-transform:uppercase;letter-spacing:1.4px;margin:30px 0 12px;"
            "padding-bottom:9px;border-bottom:1px solid #e2e8f0;\">%s</div>" % (FONT, SLATE, title))


def _meta_table(rows):
    body = "".join(
        "<tr><td valign=\"baseline\" style=\"padding:8px 6px 8px 16px;font:600 11px/1.6 %s;color:%s;text-transform:uppercase;"
        "letter-spacing:.7px;white-space:nowrap;width:1%%;\">%s</td><td valign=\"baseline\" style=\"padding:8px 16px 8px 10px;"
        "font:500 14px/1.6 %s;color:#0f172a;\">%s</td></tr>" % (FONT, SLATE, label, FONT, value)
        for label, value in rows)
    return ("<table role=\"presentation\" width=\"100%%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" "
            "bgcolor=\"#f8fafc\" style=\"background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;margin-top:22px;\">"
            "%s</table>" % body)


def _name_html(name):
    """Nome do pacote; os curtos não quebram de linha (só os muito longos podem)."""
    escaped = html.escape(name)
    return "<span style=\"white-space:nowrap;\">%s</span>" % escaped if len(name) <= 24 else escaped


def _versions(old, new):
    """'antiga → nova', sem quebrar no meio de uma versão (só no ponto da seta)."""
    return ("<span style=\"white-space:nowrap;\">%s</span> <span style=\"white-space:nowrap;\">"
            "<span style=\"color:#94a3b8;\">→</span>&nbsp;<span style=\"color:#0f172a;\">%s</span></span>"
            % (html.escape(old), html.escape(new)))


def _package_rows(packages, zebra_start=0):
    rows = []
    for index, p in enumerate(packages):
        if p.ok:
            background = "#ffffff" if (index + zebra_start) % 2 == 0 else "#f8fafc"
            badge = _pill("✓ OK", "#166534", "#dcfce7")
        else:
            background = "#fef2f2"
            badge = _pill("✕ FALHOU", "#991b1b", "#fee2e2")
        rows.append(
            "<tr><td bgcolor=\"%s\" style=\"background:%s;padding:10px 14px;border-bottom:1px solid #eef2f6;"
            "font:700 14px/1.4 %s;color:#0f172a;\">%s</td><td bgcolor=\"%s\" style=\"background:%s;padding:10px 8px;"
            "border-bottom:1px solid #eef2f6;font:400 12px/1.5 %s;color:%s;\">%s</td><td bgcolor=\"%s\" align=\"right\" "
            "style=\"background:%s;padding:10px 14px;border-bottom:1px solid #eef2f6;\">%s</td></tr>"
            % (background, background, FONT, _name_html(short_name(p.name)), background, background, MONO, SLATE,
               _versions(p.old, p.new), background, background, badge))
    return "".join(rows)


def _package_table(packages, limit=100):
    shown = packages[:limit]
    table = ("<table role=\"presentation\" width=\"100%%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" "
             "style=\"border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;\">"
             "<tr><td bgcolor=\"#f1f5f9\" style=\"background:#f1f5f9;padding:9px 14px;font:700 11px/1 %s;color:%s;"
             "text-transform:uppercase;letter-spacing:.8px;\">Pacote</td><td bgcolor=\"#f1f5f9\" style=\"background:#f1f5f9;"
             "padding:9px 8px;font:700 11px/1 %s;color:%s;text-transform:uppercase;letter-spacing:.8px;\">Versão</td>"
             "<td bgcolor=\"#f1f5f9\" align=\"right\" style=\"background:#f1f5f9;padding:9px 14px;font:700 11px/1 %s;"
             "color:%s;text-transform:uppercase;letter-spacing:.8px;\">Situação</td></tr>%s</table>"
             % (FONT, SLATE, FONT, SLATE, FONT, SLATE, _package_rows(shown)))
    if len(packages) > limit:
        table += ("<div style=\"font:400 12px/1.5 %s;color:%s;padding-top:8px;\">… e mais %d pacote(s).</div>"
                  % (FONT, SLATE, len(packages) - limit))
    return table


EMAIL_CSS = (
    "@media only screen and (max-width:480px){"
    ".px{padding-left:18px!important;padding-right:18px!important}"
    ".ttl{font-size:23px!important}.kk{letter-spacing:.8px!important}.lead{font-size:16px!important}"
    ".cn{font-size:26px!important}.cl{font-size:10px!important;letter-spacing:.4px!important}.cp{padding:14px 2px 11px!important}"
    ".c4,.c4>tbody,.c4>tbody>tr{display:block!important;width:100%!important}.c4{overflow:hidden!important}"
    ".c4 .cc{display:block!important;float:left!important;width:50%!important;box-sizing:border-box!important;"
    "padding:0 4px 8px 4px!important}.c4 .cc:nth-child(odd){padding-left:0!important}"
    ".c4 .cc:nth-child(even){padding-right:0!important}"
    ".cmd{font-size:12px!important;padding:12px 14px!important}}"
)


def _email_shell(preheader, color1, color2, kicker, title, subtitle, badge, body, footer):
    esc = html.escape
    return (
        "<!DOCTYPE html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<meta name=\"color-scheme\" content=\"light\"><meta name=\"supported-color-schemes\" content=\"light\">"
        "<title>%(title)s</title><style>%(css)s</style></head>"
        "<body style=\"margin:0;padding:0;background:#eef1f6;\">"
        "<div style=\"display:none;max-height:0;overflow:hidden;opacity:0;font-size:1px;line-height:1px;color:#eef1f6;\">"
        "%(pre)s" + "&nbsp;&zwnj;" * 40 + "</div>"
        "<table role=\"presentation\" width=\"100%%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" bgcolor=\"#eef1f6\" "
        "style=\"background:#eef1f6;\"><tr><td align=\"center\" style=\"padding:26px 12px 34px;\">"
        "<table role=\"presentation\" width=\"640\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" "
        "style=\"width:100%%;max-width:640px;\">"
        "<tr><td style=\"padding:0 6px 14px;font:800 12px/1 %(font)s;color:#475569;letter-spacing:2.4px;\">"
        "📦&nbsp; DNF&nbsp;MONITOR&nbsp;<span style=\"color:#d97706;\">PLUS</span></td></tr>"
        "<tr><td class=\"px\" bgcolor=\"%(c1)s\" style=\"background:%(c1)s;background-image:linear-gradient(135deg,%(c1)s,%(c2)s);"
        "border-radius:16px 16px 0 0;padding:30px 34px 28px;\">"
        "<table role=\"presentation\" width=\"100%%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\"><tr>"
        "<td class=\"kk\" valign=\"middle\" style=\"font:700 12px/1.3 %(font)s;color:#ffffff;letter-spacing:1.6px;text-transform:uppercase;\">"
        "%(kicker)s</td><td valign=\"middle\" align=\"right\" style=\"padding-left:10px;\">%(badge)s</td></tr>"
        "<tr><td colspan=\"2\" class=\"ttl\" style=\"font:800 28px/1.2 %(font)s;color:#ffffff;padding-top:14px;\">%(title)s</td></tr>"
        "<tr><td colspan=\"2\" style=\"font:500 14px/1.5 %(font)s;color:#ffffff;padding-top:8px;\">%(sub)s</td></tr></table>"
        "</td></tr>"
        "<tr><td class=\"px\" bgcolor=\"#ffffff\" style=\"background:#ffffff;padding:30px 34px 34px;border-left:1px solid #e2e8f0;"
        "border-right:1px solid #e2e8f0;\">%(body)s</td></tr>"
        "<tr><td class=\"px\" bgcolor=\"#f8fafc\" align=\"center\" style=\"background:#f8fafc;border:1px solid #e2e8f0;"
        "border-radius:0 0 16px 16px;padding:20px 24px;font:400 12px/1.7 %(font)s;color:%(slate)s;\">%(footer)s</td></tr>"
        "</table></td></tr></table></body></html>"
    ) % {"title": esc(title), "pre": esc(preheader), "font": FONT, "c1": color1, "c2": color2, "css": EMAIL_CSS,
         "kicker": esc(kicker), "sub": subtitle, "badge": badge, "body": body, "footer": footer, "slate": SLATE}


def _stat_cards_wrap(cards):
    return "<div style=\"margin-top:24px;\">%s</div>" % cards


def age_days(first_seen, now):
    return int(max(0, now - first_seen) // 86400)


def age_text(first_seen, now):
    days = age_days(first_seen, now)
    return "menos de 1 dia" if days == 0 else plural(days, "dia", "dias")


def esc_plural_days(days):
    return html.escape(plural(int(days), "dia", "dias"))


def plural(n, one, many):
    return "%d %s" % (n, one if n == 1 else many)


def who_label(admin_name):
    return "O administrador %s" % admin_name if admin_name else "A equipe técnica"


class DailyReport(object):
    """Relatório diário para o cliente: o que o administrador atualizou e as pendências antigas."""

    kind = "daily"
    CONTACT_HINT = "Sugerimos entrar em contato com o administrador do servidor."

    def __init__(self, hostname, ips, day, runs, admin_name="", tz=DEFAULT_REPORT_TZ, pending=None,
                 reboot=False, package_list=True, test=False, overdue=(), overdue_days=3, now=None):
        self.hostname = hostname
        self.ips = ips
        self.day = day                              # 'AAAA-MM-DD' no fuso do relatório
        self.runs = list(runs)
        self.admin_name = admin_name
        self.tz = tz
        self.pending = pending                      # pacotes ainda pendentes (None = desconhecido)
        self.reboot = reboot
        self.package_list = package_list
        self.test = test
        self.overdue = sorted(overdue, key=lambda p: (p.first_seen, p.name))   # todos os pendentes, mais antigos antes
        self.overdue_days = overdue_days
        self.now = time.time() if now is None else now

    @property
    def day_label(self):
        year, month, day = self.day.split("-")
        return "%s/%s/%s" % (day, month, year)

    @property
    def total(self):
        return sum(r.ok + r.failed for r in self.runs)

    @property
    def ok(self):
        return sum(r.ok for r in self.runs)

    @property
    def failed(self):
        return sum(r.failed for r in self.runs)

    @property
    def pending_only(self):
        """Dia sem atualização, mas com pendências antigas: o relatório muda de foco."""
        return bool(self.overdue) and not self.runs

    @property
    def oldest_days(self):
        return age_days(self.overdue[0].first_seen, self.now) if self.overdue else 0

    @property
    def overdue_security(self):
        return sum(1 for p in self.overdue if p.security)

    @property
    def subject(self):
        test = "TESTE - " if self.test else ""
        if self.pending_only:
            return "[%s] %sAtenção: atualizações pendentes - %s" % (self.hostname, test, self.day_label)
        suffix = " (há pendências)" if self.overdue else ""
        return "[%s] %sRelatório diário de atualizações%s - %s" % (self.hostname, test, suffix, self.day_label)

    @property
    def title(self):
        return "⏳ Atualizações pendentes" if self.pending_only else "📋 Relatório diário de atualizações"

    @property
    def accent(self):
        return "#d97706" if self.overdue else ("#d9534f" if self.failed else "#198754")

    def headline(self):
        if self.pending_only:
            return self.overdue_sentence()
        if not self.runs:
            return "Nenhuma atualização foi realizada em %s." % self.day_label
        return "%s atualizou o servidor %s em %s." % (
            who_label(self.admin_name), plural(len(self.runs), "vez", "vezes"), self.day_label)

    def overdue_sentence(self):
        n = len(self.overdue)
        text = "Há %s aguardando atualização, o mais antigo há %s" % (
            plural(n, "pacote", "pacotes"), age_text(self.overdue[0].first_seen, self.now))
        if self.overdue_security:
            text += " (%d de segurança)" % self.overdue_security
        return text + "."

    def _time(self, run):
        return local_format(run.start_ts, self.tz, "%H:%M")

    # ---- Telegram ---------------------------------------------------------
    def telegram_text(self):
        esc = html.escape
        lines = []
        if self.test:
            lines += ["🧪 <b>Teste do relatório diário</b> (dados de exemplo)", ""]
        lines += ["<b>%s — %s</b>" % (esc(self.title), esc(self.day_label)),
                  "🖥️ <b>Servidor:</b> %s" % esc(self.hostname), ""]
        if self.pending_only:
            lines += ["⚠️ %s" % esc(self.overdue_sentence()), "📞 %s" % esc(self.CONTACT_HINT)]
        else:
            lines += ["👤 %s" % esc(self.headline()), "",
                      "📦 Pacotes atualizados: <b>%d</b>" % self.total,
                      "✅ Com sucesso: <b>%d</b>" % self.ok,
                      "❌ Com falha: <b>%d</b>" % self.failed]
            if self.overdue:
                lines += ["", "⚠️ <b>Atualizações pendentes há mais de %s:</b>" % esc(plural(int(self.overdue_days), "dia", "dias")),
                          esc(self.overdue_sentence()), "📞 %s" % esc(self.CONTACT_HINT)]
            elif self.pending:
                lines += ["", "⏳ Ainda aguardam atualização: <b>%d</b>" % self.pending]
        if self.overdue:
            lines += ["", "📋 <b>Pacotes pendentes:</b>"]
            for p in self.overdue[:25]:
                lines.append("• %s — %s%s" % (esc(p.name), esc(age_text(p.first_seen, self.now)), " 🔐" if p.security else ""))
            if len(self.overdue) > 25:
                lines.append("… e mais %d." % (len(self.overdue) - 25))
        if self.reboot:
            lines += ["", "🔁 <b>Reinicialização pendente</b>"]
        return "\n".join(lines)

    # ---- E-mail (texto) ---------------------------------------------------
    def email_text(self):
        lines = ["%s - %s" % (self.title, self.day_label), "", "Servidor: %s" % self.hostname, "", self.headline(), ""]
        if self.pending_only:
            lines += [self.CONTACT_HINT, ""]
        else:
            lines += ["Pacotes atualizados: %d" % self.total, "Com sucesso: %d" % self.ok, "Com falha: %d" % self.failed]
            if self.pending and not self.overdue:
                lines.append("Ainda aguardam atualização: %d" % self.pending)
        if self.reboot:
            lines.append("Reinicialização pendente.")
        if self.runs:
            lines += ["", "Atualizações do dia:"]
            for r in self.runs:
                lines.append("  %s - %s (%d com sucesso, %d com falha)" % (
                    self._time(r), plural(r.ok + r.failed, "pacote", "pacotes"), r.ok, r.failed))
            if self.package_list:
                lines += ["", "Pacotes:"]
                for r in self.runs:
                    for p in r.packages:
                        lines.append("  - %s (%s -> %s)%s" % (short_name(p.name), p.old, p.new, "" if p.ok else " [FALHOU]"))
        if self.overdue:
            if not self.pending_only:
                lines += ["", "ATENÇÃO: %s" % self.overdue_sentence(), self.CONTACT_HINT]
            lines += ["", "Pacotes pendentes (%d):" % len(self.overdue)]
            for p in self.overdue:
                lines.append("  - %s (%s -> %s) - aguardando há %s%s" % (
                    p.name, p.installed, p.candidate, age_text(p.first_seen, self.now), " [segurança]" if p.security else ""))
        lines += ["", "-- Gerado automaticamente pelo DNF Monitor Plus"]
        return "\n".join(lines)

    # ---- E-mail (HTML) ----------------------------------------------------
    def _pending_section(self):
        n = len(self.overdue)
        callout = _callout("⏳", "Atualizações pendentes há mais de %s" % esc_plural_days(self.overdue_days),
                           "%s %s" % (html.escape(self.overdue_sentence()), html.escape(self.CONTACT_HINT)),
                           AMBER, "#fffbeb", "#fde68a")
        rows = []
        for index, p in enumerate(self.overdue[:100]):
            background = "#ffffff" if index % 2 == 0 else "#f8fafc"
            tag = (" " + _pill("🔐 Segurança", "#92400e", "#fef3c7")) if p.security else ""
            days = age_days(p.first_seen, self.now)
            old = days > self.overdue_days
            rows.append(
                "<tr><td bgcolor=\"%s\" style=\"background:%s;padding:10px 14px;border-bottom:1px solid #eef2f6;font:700 14px/1.9 %s;"
                "color:#0f172a;\">%s%s</td><td bgcolor=\"%s\" style=\"background:%s;padding:10px 8px;border-bottom:1px solid #eef2f6;"
                "font:400 12px/1.5 %s;color:%s;\">%s</td><td bgcolor=\"%s\" align=\"right\" style=\"background:%s;padding:10px 14px;"
                "border-bottom:1px solid #eef2f6;font:700 12px/1.4 %s;color:%s;white-space:nowrap;\">%s</td></tr>" % (
                    background, background, FONT, _name_html(p.name), tag, background, background, MONO, SLATE,
                    _versions(p.installed, p.candidate), background, background, FONT, RED if old else SLATE,
                    html.escape(age_text(p.first_seen, self.now))))
        table = ("<table role=\"presentation\" width=\"100%%\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" "
                 "style=\"border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;\"><tr>"
                 "<td bgcolor=\"#f1f5f9\" style=\"background:#f1f5f9;padding:9px 14px;font:700 11px/1 %(f)s;color:%(s)s;text-transform:uppercase;"
                 "letter-spacing:.8px;\">Pacote</td><td bgcolor=\"#f1f5f9\" style=\"background:#f1f5f9;padding:9px 8px;font:700 11px/1 %(f)s;"
                 "color:%(s)s;text-transform:uppercase;letter-spacing:.8px;\">Versão</td><td bgcolor=\"#f1f5f9\" align=\"right\" "
                 "style=\"background:#f1f5f9;padding:9px 14px;font:700 11px/1 %(f)s;color:%(s)s;text-transform:uppercase;letter-spacing:.8px;\">"
                 "Aguardando</td></tr>%(rows)s</table>" % {"f": FONT, "s": SLATE, "rows": "".join(rows)})
        if n > 100:
            table += "<div style=\"font:400 12px/1.5 %s;color:%s;padding-top:8px;\">… e mais %d pacote(s).</div>" % (FONT, SLATE, n - 100)
        return callout, _section("Pacotes pendentes (%d)" % n) + table

    def email_html(self):
        esc = html.escape
        count = len(self.runs)
        who = esc(self.admin_name)
        who_text = ("O administrador <strong>%s</strong>" % who) if self.admin_name else "<strong>A equipe técnica</strong>"
        lead_style = "margin:0;font:400 17px/1.6 %s;color:#1e293b;" % FONT

        if self.overdue and self.failed:
            badge = _pill("⚠ ATENÇÃO", "#92400e", "#fef3c7")
        elif self.overdue:
            badge = _pill("⏳ PENDÊNCIAS", "#92400e", "#fef3c7")
        elif self.failed:
            badge = _pill("⚠ %s" % plural(self.failed, "falha", "falhas"), "#92400e", "#fef3c7")
        elif self.total:
            badge = _pill("✓ TUDO CERTO", "#166534", "#dcfce7")
        else:
            badge = _pill("SEM MOVIMENTO", "#334155", "#e2e8f0")

        if self.pending_only:
            lead = "<p class=\"lead\" style=\"%s\">Há <strong>%s</strong> aguardando atualização no servidor <strong>%s</strong>, o mais " \
                   "antigo há <strong>%s</strong>.</p>" % (lead_style, esc(plural(len(self.overdue), "pacote", "pacotes")),
                                                          esc(self.hostname), esc(age_text(self.overdue[0].first_seen, self.now)))
            cards = _stat_cards([(len(self.overdue), "Pendentes", AMBER), (self.overdue_security, "Segurança",
                                 RED if self.overdue_security else "#94a3b8"), (self.oldest_days, "Dias de espera", RED)])
        elif count:
            lead = "<p class=\"lead\" style=\"%s\">%s atualizou o servidor <strong>%s</strong> no dia <strong>%s</strong>.</p>" % (
                lead_style, who_text, plural(count, "vez", "vezes"), esc(self.day_label))
            cards = _stat_cards([(count, "Atualizações", "#7c3aed"), (self.total, "Pacotes", BLUE), (self.ok, "Com sucesso", GREEN),
                                 (self.failed, "Com falha", RED if self.failed else "#94a3b8")])
        else:
            lead = "<p class=\"lead\" style=\"%s\">Nenhuma atualização foi realizada em <strong>%s</strong>.</p>" % (
                lead_style, esc(self.day_label))
            cards = _stat_cards([(0, "Atualizações", "#94a3b8"), (0, "Pacotes", "#94a3b8"), (0, "Com sucesso", "#94a3b8"),
                                 (0, "Com falha", "#94a3b8")])

        timeline = ""
        if self.runs:
            items = []
            for r in self.runs:
                bar = RED if r.failed else GREEN
                chips = _pill("✓ %d com sucesso" % r.ok, "#166534", "#dcfce7")
                if r.failed:
                    chips += "&nbsp;" + _pill("✕ %d com falha" % r.failed, "#991b1b", "#fee2e2")
                items.append(
                    "<tr><td bgcolor=\"#f8fafc\" style=\"background:#f8fafc;border:1px solid #e2e8f0;border-left:5px solid %s;"
                    "border-radius:8px;padding:13px 16px;\"><table role=\"presentation\" width=\"100%%\" cellpadding=\"0\" "
                    "cellspacing=\"0\" border=\"0\"><tr><td valign=\"middle\" style=\"font:800 20px/1 %s;color:#0f172a;"
                    "white-space:nowrap;padding-right:16px;\" width=\"72\">%s</td><td valign=\"middle\">"
                    "<div style=\"font:700 14px/1.3 %s;color:#0f172a;padding-bottom:7px;\">%s</div>%s</td></tr></table></td></tr>"
                    "<tr><td height=\"10\" style=\"font-size:0;line-height:0;\">&nbsp;</td></tr>" % (
                        bar, FONT, esc(self._time(r)), FONT,
                        esc("%s atualizado%s" % (plural(r.ok + r.failed, "pacote", "pacotes"), "" if r.ok + r.failed == 1 else "s")),
                        chips))
            timeline = (_section("Atualizações do dia") + "<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" "
                        "cellspacing=\"0\" border=\"0\">" + "".join(items) + "</table>")

        callouts = ""
        failed_names = [short_name(p.name) for r in self.runs for p in r.packages if not p.ok]
        if failed_names:
            callouts += _callout("❌", "%s com falha" % plural(len(failed_names), "pacote", "pacotes"),
                                 ", ".join("<strong>%s</strong>" % esc(n) for n in failed_names), RED, "#fef2f2", "#fecaca")
        pending_table = ""
        if self.overdue:
            overdue_callout, pending_table = self._pending_section()
            callouts += overdue_callout
        elif self.pending:
            callouts += _callout("⏳", "Ainda aguardam atualização", "%s." % esc(plural(self.pending, "pacote", "pacotes")),
                                 BLUE, "#eff6ff", "#bfdbfe")
        if self.reboot:
            callouts += _callout("🔁", "Reinicialização pendente", "O servidor precisa ser reiniciado para concluir "
                                 "algumas atualizações.", AMBER, "#fffbeb", "#fde68a")
        if self.test:
            callouts = _callout("🧪", "Mensagem de teste", "Os números abaixo são dados de exemplo, só para você ver "
                                "como o relatório diário vai ficar.", "#7c3aed", "#f5f3ff", "#ddd6fe") + callouts

        packages = ""
        if self.package_list and self.runs:
            packages = _section("Pacotes atualizados") + _package_table([p for r in self.runs for p in r.packages])

        bar = "" if self.pending_only else _success_bar(self.ok, self.failed)
        body = lead + _stat_cards_wrap(cards) + bar + callouts + timeline + packages + pending_table
        footer = ("Relatório diário gerado automaticamente pelo <strong>DNF Monitor Plus</strong> · versão %s<br>"
                  "Servidor %s" % (__version__, esc(self.hostname)))
        if self.pending_only:
            preheader = self.overdue_sentence() + " " + self.CONTACT_HINT
            gradient = ("#b45309", "#f59e0b")
        else:
            preheader = "%s: %s, %d com sucesso e %d com falha." % (
                self.day_label, plural(self.total, "pacote atualizado", "pacotes atualizados"), self.ok, self.failed)
            if self.overdue:
                preheader += " Há atualizações pendentes: fale com o administrador."
            gradient = ("#1e3a8a", "#4f46e5")
        return _email_shell(preheader, gradient[0], gradient[1], "Relatório diário · %s" % self.day_label,
                            "Atualizações pendentes" if self.pending_only else "Atualizações do servidor",
                            "<strong>%s</strong> · %s" % (esc(self.hostname), esc(self.day_label)), badge, body, footer)


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
            "Content-Type": "application/json", "User-Agent": PROG + "/" + __version__})
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


def build_client_notifiers(cfg):
    notifiers = []
    if cfg.client_telegram_enabled:
        notifiers.append(TelegramNotifier(cfg.client_bot_token, cfg.client_chat_ids, cfg.client_api_base))
    if cfg.client_email_enabled:
        notifiers.append(EmailNotifier(cfg.client_email))
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
                 ips_func=None, reboot_func=reboot_required, clock=time.time, persist=True,
                 client_notifiers=(), history=None, history_path=DNF_HISTORY_DB, history_reader=None):
        self.cfg = cfg
        self.notifiers = notifiers
        self.client_notifiers = list(client_notifiers)
        self.history = history
        self.history_path = history_path
        self.history_reader = history_reader or (lambda since: read_dnf_history(self.history_path, since))
        self._report_retry_at = 0.0
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

    def run_cycle(self, update_cache=None):
        """Executa uma checagem. Retorna False se algo precisa de nova tentativa."""
        updates = self.collector(self.cfg.update_cache if update_cache is None else update_cache)
        if updates is None:
            log.warning("Não foi possível ler as atualizações do DNF; ciclo ignorado.")
            return False

        now = self.clock()
        if self.history is not None:
            try:
                self.history.sync_pending(updates, now)      # insumo do relatório de pendências antigas
            except sqlite3.Error as exc:
                log.warning("Não foi possível registrar os pacotes pendentes: %s", exc)
        needs_reboot, reboot_pkgs = self.reboot_func()
        action = decide(self.state, updates, now, self.cfg.reminder_days)
        ips = self.ips_func()
        healthy = True
        update_alert_sent = False

        if action in ("new", "reminder"):
            kernel = any(is_kernel_package(u.name) for u in updates)
            log.info("Encontrados %d pacotes pendentes. Crítico (Kernel): %s", len(updates), kernel)
            alert = Alert(action, self.hostname, ips, updates, needs_reboot, reboot_pkgs, now, self.cfg.report_tz)
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
                sent = self._dispatch(Alert("cleared", self.hostname, ips, detected_at=now, tz=self.cfg.report_tz))
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
            if self._dispatch(Alert("reboot", self.hostname, ips, [], True, reboot_pkgs, now, self.cfg.report_tz)):
                self.state.reboot_notified = True
            else:
                healthy = False

        self._commit()
        return healthy


    # ---- Atualizações realizadas pelo administrador -----------------------
    def process_runs(self):
        """Lê o histórico de transações do DNF e grava as que atualizaram pacotes (insumo do relatório)."""
        if self.history is None:
            return True
        now = self.clock()
        try:
            entries = self.history_reader(self.history.since_ts)
        except Exception as exc:        # banco do DNF ausente ou ilegível: segue sem histórico
            log.debug("Histórico do DNF indisponível: %s", exc)
            return True
        for entry in sorted(entries, key=lambda e: e.start_ts):
            if entry.automatic or not entry.upgrades or entry.start_ts < self.history.since_ts:
                continue            # dnf-automatic e afins (sem linha de comando) não são trabalho do administrador
            if self.history.has_run(entry.start_ts, entry.commandline):
                continue
            running = entry.end_ts is None
            if running and now - entry.start_ts < INPROGRESS_GRACE:
                continue            # a transação ainda pode estar em andamento
            results = [PackageResult(n, o, v, bool(ok) and not running and not entry.error)
                       for n, o, v, ok in entry.upgrades]
            if self.history.add_run(entry, results) is not None:
                log.info("Atualização registrada: %d pacote(s), %d com falha.",
                         len(results), sum(1 for r in results if not r.ok))
        return True

    # ---- Relatório diário para o cliente ----------------------------------
    def run_report(self, force=False):
        """Envia o relatório do dia ao cliente (uma vez por dia, a partir do horário configurado)."""
        if self.history is None or not self.client_notifiers:
            return True
        cfg = self.cfg
        now = self.clock()
        if not force and (not report_due(now, cfg.report_tz, cfg.report_time) or now < self._report_retry_at):
            return True
        day = local_day(now, cfg.report_tz)
        done = set() if force else self.history.channels_done(day)
        channels = [n for n in self.client_notifiers if n.name not in done]
        if not channels:
            return True

        runs = self.history.unreported_runs()
        overdue = self.history.overdue(now, cfg.pending_alert_days)
        if not runs and not overdue and not cfg.report_send_empty:
            if force:
                log.info("Não há atualizações novas para reportar.")
            elif self.persist:
                for n in channels:      # dia sem atividade: nada a enviar, e não decidir de novo hoje
                    self.history.mark_channel_done(day, n.name, now, skipped=True)
            return True

        pending = len(self.state.packages) if self.state.packages else None
        report = DailyReport(self.hostname, self.ips_func(), day, runs, cfg.admin_name, cfg.report_tz, pending,
                             self.reboot_func()[0], cfg.report_package_list, overdue=overdue,
                             overdue_days=cfg.pending_alert_days, now=now)
        all_ok = True
        for notifier in channels:
            if notifier.send(report):
                if self.persist:
                    self.history.mark_channel_done(day, notifier.name, now)
            else:
                all_ok = False
        if all_ok:
            if self.persist:
                self.history.mark_reported([r.id for r in runs], day)
            log.info("Relatório diário de %s enviado ao cliente (%d atualização(ões)).", day, len(runs))
            return True
        log.error("O relatório diário não chegou por todos os canais; nova tentativa em breve.")
        self._report_retry_at = now + RETRY_INTERVAL
        return False

    def poll(self):
        """Tarefas leves, executadas a cada POLL_INTERVAL: histórico do DNF e hora do relatório."""
        healthy = self.process_runs()
        return self.run_report() and healthy

    def seconds_to_report(self):
        return next_report_ts(self.clock(), self.cfg.report_tz, self.cfg.report_time) - self.clock()


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

    def build(self, cfg, history=True):
        state = State(cfg.state_file).load()
        dry = self.args.dry_run
        notifiers = [PrintNotifier()] if dry else build_notifiers(cfg)
        client = [PrintNotifier()] if dry else build_client_notifiers(cfg)
        db = None
        if history:
            try:
                db = HistoryDB(":memory:", since_ts=0) if dry else HistoryDB(cfg.db_file)
            except (OSError, sqlite3.Error) as exc:
                log.error("Não foi possível abrir o banco de histórico %s (%s); relatórios desativados.",
                          cfg.db_file, exc)
        def collector(update_cache):
            return collect_updates(update_cache, cfg.skip_broken_repos, cfg.collect_timeout, cfg.load_plugins)

        return Monitor(cfg, notifiers, state, collector=collector, persist=not dry, client_notifiers=client, history=db)

    def run(self):
        cfg = load_config(self.args.config)
        monitor = self.build(cfg)
        signal.signal(signal.SIGTERM, self._on_term)
        signal.signal(signal.SIGINT, self._on_term)
        signal.signal(signal.SIGHUP, self._on_hup)
        log.info("=== Iniciando %s %s em %s ===", PRODUCT, __version__, monitor.hostname)
        log.info("Canais: %s | intervalo: %s hora(s)", ", ".join(cfg.channels()), cfg.interval_hours)
        self._log_report_info(cfg, monitor)

        next_cycle = 0.0
        watched = repo_signature()
        while not self.stop:
            now = time.time()
            if now >= next_cycle:
                healthy = monitor.run_cycle()
                delay = cfg.interval_seconds if healthy else min(cfg.interval_seconds, RETRY_INTERVAL)
                next_cycle = time.time() + delay
                watched = repo_signature()       # o nosso próprio 'dnf makecache' não conta como novidade
            elif repo_changed(watched, repo_signature(), now):
                # Metadados novos (ex.: dnf-makecache.timer) ou pacotes instalados: avisa na hora, sem esperar o intervalo.
                log.info("O DNF mudou os metadados/pacotes; checando atualizações agora.")
                watched = repo_signature()
                if not monitor.run_cycle(update_cache=False):
                    next_cycle = min(next_cycle, time.time() + RETRY_INTERVAL)
            monitor.poll()
            self.wake.clear()
            self.wake.wait(max(1.0, min(POLL_INTERVAL, next_cycle - time.time())))
            if self.reload and not self.stop:
                self.reload = False
                try:
                    cfg = load_config(self.args.config)
                    if monitor.history is not None:
                        monitor.history.close()
                    monitor = self.build(cfg)
                    next_cycle = 0.0
                    log.info("Configuração recarregada (canais: %s).", ", ".join(cfg.channels()))
                    self._log_report_info(cfg, monitor)
                except ConfigProblem as exc:
                    log.error("Recarga ignorada, mantendo a configuração anterior: %s", exc)
        return 0

    @staticmethod
    def _log_report_info(cfg, monitor):
        if cfg.client_channels() and monitor.history is not None:
            log.info("Relatório diário ao cliente (%s) às %02d:%02d (%s).", ", ".join(cfg.client_channels()),
                     cfg.report_time[0], cfg.report_time[1], cfg.report_tz)


def test_notify(cfg):
    alert = Alert("test", socket.gethostname(), get_machine_ips(cfg.ignore_interfaces),
                  [Update("pacote-exemplo", "1.0", "1.1", True)])
    results = {"admin-" + n.name: n.send(alert) for n in build_notifiers(cfg)}
    if cfg.client_channels():
        sample = [RunRecord(0, time.time() - 3600, None, 2, 1, [
            PackageResult("pacote-exemplo", "1.0", "1.1", True), PackageResult("outro-exemplo", "2.0", "2.1", True),
            PackageResult("falha-exemplo", "3.0", "3.1", False)])]
        report = DailyReport(socket.gethostname(), get_machine_ips(cfg.ignore_interfaces),
                             local_day(time.time(), cfg.report_tz), sample, cfg.admin_name, cfg.report_tz, test=True)
        for n in build_client_notifiers(cfg):
            results["cliente-" + n.name] = n.send(report)
    for name, ok in sorted(results.items()):
        log.info("Teste %s: %s", name, "OK" if ok else "FALHOU")
    return 0 if all(results.values()) else 1


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog=PROG, description="DNF Monitor Plus: monitoramento de atualizações DNF com relatório ao cliente.")
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG, help="arquivo de configuração")
    parser.add_argument("--once", action="store_true", help="faz uma única checagem e sai")
    parser.add_argument("--dry-run", action="store_true", help="mostra os alertas sem enviar nem gravar estado")
    parser.add_argument("--test-notify", action="store_true", help="envia uma mensagem de teste e sai")
    parser.add_argument("--report", action="store_true",
                        help="gera e envia agora o relatório diário ao cliente (use com --dry-run para pré-visualizar)")
    parser.add_argument("--check-config", action="store_true", help="valida a configuração e sai")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version="%s %s" % (PROG, __version__))
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

    interactive = args.check_config or args.test_notify or args.once or args.dry_run or args.report
    setup_logging(None if interactive and not os.access(os.path.dirname(cfg.log_file) or ".", os.W_OK)
                  else cfg.log_file, args.verbose)

    if args.check_config:
        log.info("Configuração OK. Canais ativos: %s", ", ".join(cfg.channels()))
        log.info("Relatório ao cliente: %s", ", ".join(cfg.client_channels()) or "desativado")
        return 0
    if args.test_notify:
        return test_notify(cfg)
    if args.report:
        if not cfg.client_channels() and not args.dry_run:
            log.error("Nenhum canal de cliente configurado ([ClientTelegram] e/ou [ClientEmail]).")
            return EX_CONFIG
        monitor = Daemon(args).build(cfg)
        if monitor.history is None:
            return 1
        monitor.process_runs()
        return 0 if monitor.run_report(force=True) else 1
    if args.once or args.dry_run:
        monitor = Daemon(args).build(cfg, history=False)
        return 0 if monitor.run_cycle() else 1
    try:
        return Daemon(args).run()
    except ConfigProblem as exc:
        log.error("%s", exc)
        return EX_CONFIG


if __name__ == "__main__":
    sys.exit(main())
