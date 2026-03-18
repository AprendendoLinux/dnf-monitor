# DNF Monitor Daemon 📦

Um daemon leve e independente, escrito em Python, que roda via `systemd` para monitorar atualizações de pacotes via DNF em servidores do ecossistema RHEL (CentOS, AlmaLinux, Rocky Linux, Oracle Linux). Quando há pacotes disponíveis para atualização, ele envia alertas automáticos via Telegram e relatórios detalhados em HTML por e-mail.

## 🌟 Funcionalidades

* **Monitoramento Contínuo:** Roda em background como um serviço nativo do sistema operacional.
* **Alertas Inteligentes:** Avisa apenas uma vez por lote de atualizações, evitando repetições desnecessárias.
* **Múltiplos Destinatários:** Suporta o envio de alertas para várias IDs de usuários/grupos no Telegram e múltiplos endereços de e-mail.
* **Alertas Críticos de Kernel:** Detecta automaticamente pacotes de kernel (`kernel`, `kernel-core`, etc.) e destaca o aviso como urgente (🚨).
* **Relatórios por E-mail:** Envia um resumo em HTML das atualizações, com suporte a SMTP via STARTTLS (porta 587) ou SSL nativo (porta 465).
* **Informações de Rede:** Captura o nome do servidor (hostname) e os IPs (IPv4 e IPv6) de forma segura em qualquer versão do Python 3, ignorando interfaces de loopback.
* **Proteção de Configurações:** Utiliza a diretiva `%config(noreplace)` no pacote RPM, garantindo que suas credenciais não sejam sobrescritas ao atualizar a versão do daemon.
* **Logs e Auditoria:** Registra todas as atividades em `/var/log/dnf-monitor.log`, com rotação semanal automática via `logrotate`.

## 🚀 Como funcionam os Alertas

### Telegram
Quando houver atualizações padrão, o bot enviará uma mensagem parecida com esta:

> ⚠️ **Alerta de Atualização**
> 🖥️ **Servidor:** file-server
> 🌐 **IPs:** 192.168.1.50, 10.0.0.5
>
> Temos 5 pacotes prontos para atualizar.
> 📦 Alguns deles: curl, nginx, python3, firewalld, tzdata...

Se houver uma **atualização de Kernel**, o alerta muda para o modo crítico:

> 🚨 **CRÍTICO: ATUALIZAÇÃO DE KERNEL**
> 🖥️ **Servidor:** file-server
> ...

### E-mail
Um relatório em HTML é gerado detalhando a quantidade de pacotes e listando o nome de cada um deles, com uma tarja visual azul (informativa) ou vermelha (crítica, caso envolva o Kernel).

---

## ⚙️ Configuração

O daemon lê as configurações a partir do arquivo `/etc/dnf-monitor/dnf-monitor.conf`. A estrutura do arquivo é gerada automaticamente na instalação:

```
[Telegram]
BOT_TOKEN=seu_bot_token_aqui
# Insira os Chat IDs separados por vírgula
CHAT_IDS=12345678, 87654321

[Monitor]
# Tempo de rotação e verificação em horas (aceita decimais, ex: 0.5 para 30 min)
CHECK_INTERVAL_HOURS=4

[Email]
# Deixe em branco ou comente as linhas se não quiser usar e-mail neste servidor
SMTP_SERVER=smtp.seuservidor.com
SMTP_PORT=587 # Use 587 para STARTTLS ou 465 para SSL
SMTP_USER=alerta@seuservidor.com
SMTP_PASS=sua_senha_segura
SENDER_EMAIL=alerta@seuservidor.com
# Insira os e-mails destinatários separados por vírgula
RECIPIENT_EMAILS=admin@seuservidor.com, suporte@seuservidor.com
```
## 🛠️ Como Compilar o Pacote (.rpm)

O empacotamento é feito utilizando a estrutura padrão do `rpmbuild`. O projeto utiliza um único arquivo `.spec` para gerenciar a instalação, permissões e criação de diretórios.

1. Instale os pré-requisitos:
```bash
sudo dnf install rpm-build rpmdevtools python3-dnf python3-requests -y
rpmdev-setuptree
```
2. Coloque o script `dnf_monitor.py` na pasta `~/rpmbuild/SOURCES/`.
3. Coloque o arquivo `dnf-monitor.spec` na pasta `~/rpmbuild/SPECS/`.
4. Compile o pacote:
```bash
rpmbuild -ba ~/rpmbuild/SPECS/dnf-monitor.spec
```

Isso gerará o pacote instalável na pasta `~/rpmbuild/RPMS/noarch/`.

## 📦 Instalação e Uso

Transfira o arquivo `.rpm` gerado para o servidor de destino e instale-o utilizando o DNF (para que ele resolva as dependências do Python automaticamente):

```bash
sudo dnf localinstall dnf-monitor-1.0-2.el9.noarch.rpm -y
```
Ao instalar, o pacote automaticamente:
1. Copia o script para `/opt/dnf-monitor/`.
2. Cria o arquivo `.conf` (protegido contra sobrescritas em atualizações futuras).
3. Configura o arquivo no `/etc/logrotate.d/`.
4. Habilita e inicia o daemon no systemd.

### Comandos Úteis do Serviço

Para acompanhar o funcionamento em tempo real pelo log:
```bash
tail -f /var/log/dnf-monitor.log
```

Para verificar pelo systemd:
```bash
sudo journalctl -u dnf-monitor -f
```

Para reiniciar o serviço (necessário após alterar o arquivo `.conf`):
```bash
sudo systemctl restart dnf-monitor
```
## 🗑️ Desinstalação

Para remover o daemon do sistema de forma totalmente limpa:

```bash
sudo dnf remove dnf-monitor
```
*(As instruções do arquivo `.spec` cuidarão de parar o serviço, desativá-lo no systemd e limpar os arquivos de log antigos automaticamente).*