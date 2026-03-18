import dnf
import requests
import time
import socket
import subprocess
import configparser
import os
import sys
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

CONFIG_FILE = "/etc/dnf-monitor/dnf-monitor.conf"
LOG_FILE = "/var/log/dnf-monitor.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
console.setFormatter(formatter)
logging.getLogger('').addHandler(console)

def load_config():
    config = configparser.ConfigParser()
    if not os.path.exists(CONFIG_FILE):
        logging.error(f"Arquivo de configuração {CONFIG_FILE} não encontrado.")
        sys.exit(1)
    
    config.read(CONFIG_FILE)
    
    try:
        bot_token = config.get("Telegram", "BOT_TOKEN").strip()
        chat_ids = [cid.strip() for cid in config.get("Telegram", "CHAT_IDS").split(",") if cid.strip()]
        horas_rotacao = config.getfloat("Monitor", "CHECK_INTERVAL_HOURS", fallback=4.0)
    except (configparser.NoOptionError, configparser.NoSectionError) as e:
        logging.error(f"Erro na estrutura base do conf: {e}")
        sys.exit(1)
        
    email_config = None
    if config.has_section("Email"):
        try:
            email_config = {
                "server": config.get("Email", "SMTP_SERVER").strip(),
                "port": config.getint("Email", "SMTP_PORT"),
                "user": config.get("Email", "SMTP_USER").strip(),
                "pass": config.get("Email", "SMTP_PASS").strip(),
                "sender_name": config.get("Email", "SENDER_NAME", fallback="DNF Monitor").strip(),
                "sender_email": config.get("Email", "SENDER_EMAIL").strip(),
                "recipients": [e.strip() for e in config.get("Email", "RECIPIENT_EMAILS").split(",") if e.strip()]
            }
        except Exception as e:
            logging.warning(f"Configuração de e-mail incompleta ou ausente: {e}")

    return bot_token, chat_ids, horas_rotacao, email_config

def get_machine_ips():
    try:
        output = subprocess.check_output(['hostname', '-I']).decode('utf-8').strip()
        ips = output.split()
        return ", ".join(ips) if ips else "Nenhum IP detectado"
    except Exception as e:
        logging.error(f"Erro ao obter IPs: {e}")
        return "Erro ao obter IPs"

def get_upgradable_packages():
    try:
        base = dnf.Base()
        base.read_all_repos()
        base.fill_sack()
        upgrades = base.sack.query().upgrades()
        return [pkg.name for pkg in upgrades]
    except Exception as e:
        logging.error(f"Erro ao consultar o DNF: {e}")
        return []

def check_kernel_update(packages):
    kernel_prefixes = ('kernel', 'kernel-core', 'kernel-modules', 'kernel-uek')
    for pkg in packages:
        if pkg.startswith(kernel_prefixes):
            return True
    return False

def send_telegram_alert(server_name, packages, ips, bot_token, chat_ids, is_critical):
    qtd = len(packages)
    exemplos = ", ".join(packages[:5])
    mais_info = "..." if qtd > 5 else ""
    
    alerta_icone = "🚨 *CRÍTICO: ATUALIZAÇÃO DE KERNEL*" if is_critical else "⚠️ *Alerta de Atualização*"
    
    mensagem = (
        f"{alerta_icone}\n"
        f"🖥️ *Servidor:* {server_name}\n"
        f"🌐 *IPs:* {ips}\n\n"
        f"Temos {qtd} pacotes prontos para atualizar.\n"
        f"📦 Alguns deles: {exemplos}{mais_info}"
    )
    
    url = f"https://api.telegram.org/{bot_token}/sendMessage"
    for chat_id in chat_ids:
        try:
            requests.post(url, json={"chat_id": chat_id, "text": mensagem, "parse_mode": "Markdown"})
            logging.info(f"Telegram enviado com sucesso para Chat ID: {chat_id}")
        except Exception as e:
            logging.error(f"Falha ao enviar Telegram para Chat ID {chat_id}: {e}")

def send_email_alert(server_name, packages, ips, email_config, is_critical):
    if not email_config or not email_config.get("server"):
        return

    qtd = len(packages)
    lista_pacotes_html = "".join([f"<li>{pkg}</li>" for pkg in packages])
    cor_destaque = "#d9534f" if is_critical else "#0275d8"
    titulo_email = "🚨 URGENTE: ATUALIZAÇÃO CRÍTICA DE KERNEL" if is_critical else "ℹ️ Relatório de Atualizações DNF"
    assunto = f"[{server_name}] {'🚨 CRÍTICO: Kernel Update' if is_critical else 'Atualizações Disponíveis'}"

    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
        <h2 style="color: {cor_destaque}; border-bottom: 2px solid {cor_destaque}; padding-bottom: 10px;">{titulo_email}</h2>
        <p><strong>Servidor:</strong> {server_name}</p>
        <p><strong>Endereços IP:</strong> {ips}</p>
        <div style="background-color: #f9f9f9; padding: 15px; border-left: 4px solid {cor_destaque}; margin: 20px 0;">
            <p style="margin: 0; font-size: 16px;">Existem <strong>{qtd}</strong> pacotes aguardando instalação.</p>
        </div>
        <h3>Lista de Pacotes:</h3>
        <ul style="background-color: #f1f1f1; padding: 15px 35px; border-radius: 5px; max-height: 300px; overflow-y: auto;">
            {lista_pacotes_html}
        </ul>
    </body>
    </html>
    """

    try:
        # Abre a ligação SMTP apenas uma vez
        if email_config["port"] == 465:
            server = smtplib.SMTP_SSL(email_config["server"], email_config["port"])
        else:
            server = smtplib.SMTP(email_config["server"], email_config["port"])
            server.starttls()
            
        with server:
            server.login(email_config["user"], email_config["pass"])
            
            # Itera sobre a lista de e-mails para enviar cópias individuais
            for recipient in email_config["recipients"]:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = assunto
                msg["From"] = formataddr((email_config["sender_name"], email_config["sender_email"]))
                # O destinatário no cabeçalho é apenas a pessoa atual do loop
                msg["To"] = recipient
                msg.attach(MIMEText(html_content, "html"))
                
                # Envia o e-mail
                server.sendmail(email_config["sender_email"], [recipient], msg.as_string())
                logging.info(f"E-mail enviado com sucesso individualmente para: {recipient}")
                
    except Exception as e:
        logging.error(f"Falha ao enviar E-mail via {email_config['server']}:{email_config['port']} - {e}")

def main():
    bot_token, chat_ids, horas_rotacao, email_config = load_config()
    check_interval_seconds = int(horas_rotacao * 3600)
    server_name = socket.gethostname()
    last_notified_count = -1
    
    logging.info(f"=== Iniciando DNF Monitor em {server_name} ===")
    
    while True:
        pacotes = get_upgradable_packages()
        qtd_atual = len(pacotes)
        
        if qtd_atual > 0 and qtd_atual != last_notified_count:
            ips = get_machine_ips()
            is_critical = check_kernel_update(pacotes)
            
            logging.info(f"Encontrados {qtd_atual} pacotes pendentes. Crítico (Kernel): {is_critical}")
            
            send_telegram_alert(server_name, pacotes, ips, bot_token, chat_ids, is_critical)
            send_email_alert(server_name, pacotes, ips, email_config, is_critical)
            
            last_notified_count = qtd_atual
        elif qtd_atual == 0 and last_notified_count != 0:
            logging.info("Todos pacotes atualizados. Fila zerada.")
            last_notified_count = 0
            
        time.sleep(check_interval_seconds)

if __name__ == "__main__":
    main()
