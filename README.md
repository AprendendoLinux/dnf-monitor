<div align="center">

# 📦 DNF Monitor Daemon

**Saiba na hora quando seus servidores RHEL, Rocky, AlmaLinux e Oracle Linux têm atualizações pendentes — sem spam.**

[![CI](https://github.com/AprendendoLinux/dnf-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/AprendendoLinux/dnf-monitor/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/AprendendoLinux/dnf-monitor?label=vers%C3%A3o)](https://github.com/AprendendoLinux/dnf-monitor/releases/latest)
![EL](https://img.shields.io/badge/RHEL%20%2F%20Rocky%20%2F%20Alma%20%2F%20Oracle-8%20%7C%209%20%7C%2010-EE0000?logo=redhat&logoColor=white)
![Python](https://img.shields.io/badge/Python-%E2%89%A5%203.6-3776AB?logo=python&logoColor=white)

</div>

Daemon leve em Python, executado pelo `systemd`, que usa o **solver do DNF** para saber o que realmente seria atualizado
e avisa por **Telegram** e/ou **e-mail (HTML)**. Destaca atualizações de **kernel** e de **segurança**, avisa quando é
preciso **reiniciar** e lembra o que já foi notificado — então você recebe **um alerta por novidade**, não um por
checagem.

> Versão para Debian/Ubuntu: [apt-monitor-daemon](https://github.com/AprendendoLinux/apt-monitor-daemon).

## Índice

- [Por que usar](#-por-que-usar)
- [Início rápido](#-início-rápido)
- [Como são os alertas](#-como-são-os-alertas)
- [Instalação](#-instalação)
- [Configuração](#️-configuração)
- [Linha de comando](#-linha-de-comando)
- [Operação do dia a dia](#-operação-do-dia-a-dia)
- [Segurança](#-segurança)
- [Compatibilidade](#-compatibilidade)
- [Perguntas frequentes](#-perguntas-frequentes)
- [Documentação](#-documentação)
- [Desenvolvimento e contribuição](#-desenvolvimento-e-contribuição)

## 🌟 Por que usar

| | |
|---|---|
| 🧠 **Usa o solver do DNF** | Lista só o que `dnf upgrade` instalaria de fato: respeita `exclude`, `versionlock` e **ignora pacotes com dependências quebradas** (diferente de `check-update`). |
| 🔕 **Sem repetição** | O estado fica em disco (`/var/lib/dnf-monitor`). Reiniciar o serviço, o servidor ou atualizar o pacote **não** reenvia alertas. Só avisa quando surge pacote/versão novo. |
| 🚨 **Kernel crítico** | `kernel`, `kernel-core`, `kernel-modules*`, `kernel-uek*`, `kernel-rt*`… disparam o alerta crítico — `kernel-headers`/`kernel-devel`/`kernel-tools` não. |
| 🔐 **Segurança** | Pacotes ligados a *advisories* `security` (updateinfo) são destacados e contados. |
| 🔁 **Reboot pendente** | Avisa quando `needs-restarting -r` indica que é preciso reiniciar. |
| 📬 **Vários destinos** | Vários chats do Telegram e vários e-mails; a falha de um não afeta os outros. |
| 🛡️ **Resiliente** | Consulta ao DNF em processo isolado com timeout; repositório fora do ar não cega o monitor; falha do DNF **não** vira "tudo em dia"; retentativa se nada foi entregue. |
| 🔒 **Seguro por padrão** | Credenciais nunca aparecem em logs, `.conf` com `0600`, serviço com hardening do systemd, SMTP sempre sob TLS. |
| 🪶 **Leve** | Uma única dependência: `python3-dnf`. Python ≥ 3.6. |

## ⚡ Início rápido

```bash
# 1. instalar (baixe o .rpm em Releases)
sudo dnf install ./dnf-monitor-1.1.0-1.noarch.rpm

# 2. configurar Telegram e/ou e-mail
sudo vi /etc/dnf-monitor/dnf-monitor.conf

# 3. validar e testar
sudo dnf-monitor --check-config
sudo dnf-monitor --test-notify

# 4. iniciar
sudo systemctl restart dnf-monitor
```

## 🚀 Como são os alertas

### Telegram

> 🚨 **CRÍTICO: ATUALIZAÇÃO DE KERNEL**
> 🖥️ **Servidor:** file-server
> 🌐 **IPs:** 192.168.1.50, 2001:db8::50
>
> Temos 5 pacotes prontos para atualizar (2 de segurança).
> 📦 Alguns deles: openssl, openssl-libs, kernel-core, tzdata, firewalld...
> 🔁 **Reinicialização pendente** (kernel, glibc)

| Título | Quando |
|---|---|
| 🚨 CRÍTICO: ATUALIZAÇÃO DE KERNEL | há kernel pendente |
| 🔐 Atualizações de segurança disponíveis | há pacote com advisory de segurança |
| ⚠️ Alerta de Atualização | demais atualizações |
| 🔔 Lembrete: … | `REMINDER_DAYS` venceu e nada mudou |
| ✅ Servidor em dia | tudo atualizado (`NOTIFY_ON_CLEAR=true`) |
| 🔁 Reinicialização necessária | `needs-restarting -r` passou a indicar reboot |

### E-mail

HTML responsivo com tarja colorida pela gravidade (azul, laranja, vermelha), tabela *pacote · versão instalada → nova ·
segurança* e alternativa em texto puro. Cada destinatário recebe a própria mensagem.

## 📥 Instalação

### Pelo `.rpm` (recomendado)

Baixe o arquivo mais recente em **[Releases](https://github.com/AprendendoLinux/dnf-monitor/releases/latest)**
(há também o `SHA256SUMS`). O pacote é `noarch` e serve para **EL8, EL9 e EL10**:

```bash
sha256sum -c SHA256SUMS --ignore-missing
sudo dnf install ./dnf-monitor-1.1.0-1.noarch.rpm
```

O pacote copia os arquivos, cria `/var/lib/dnf-monitor`, protege o `.conf` (`0600`), configura o `logrotate` e
**habilita** o serviço. Ele só **inicia** se a configuração já for válida; caso contrário, mostra o que falta.
Recomenda `dnf-utils` (para o aviso de reinicialização) — instale com `sudo dnf install dnf-utils`.

### Gerando o pacote você mesmo

```bash
git clone https://github.com/AprendendoLinux/dnf-monitor && cd dnf-monitor
./build.sh        # → dist/dnf-monitor-<versão>-1.noarch.rpm (usa rpmbuild local ou um container Rocky 9)
sudo dnf install ./dist/dnf-monitor-*.noarch.rpm
```

### Atualizando

```bash
sudo dnf install ./dnf-monitor-<nova-versão>.noarch.rpm
```

> **Vindo da v1.0?** O seu `dnf-monitor.conf` é mantido (e passa a ter modo `0600`); o novo exemplo é gravado como
> `.rpmnew`. O script antigo em `/opt/dnf-monitor` e a unit em `/etc/systemd/system` são removidos sozinhos e o serviço
> é reiniciado com o código novo.

### Desinstalando

```bash
sudo dnf remove dnf-monitor    # para o serviço, remove estado e log; seu .conf vira .rpmsave
```

## ⚙️ Configuração

Arquivo: `/etc/dnf-monitor/dnf-monitor.conf`. Depois de editar: `sudo systemctl reload dnf-monitor`.

```ini
[Telegram]
BOT_TOKEN=123456789:AAE...               # com ou sem o prefixo "bot"; vazio = desativa o Telegram
CHAT_IDS=12345678, -1001234567890        # vários, separados por vírgula
#API_BASE=https://api.telegram.org

[Monitor]
CHECK_INTERVAL_HOURS=4                   # aceita decimais (0.5 = 30 min); mínimo efetivo: 1 min
REMINDER_DAYS=0                          # repete o alerta a cada N dias enquanto houver pendência (0 = nunca)
NOTIFY_ON_CLEAR=false                    # avisa quando tudo for atualizado
UPDATE_CACHE=true                        # renova os metadados dos repositórios a cada ciclo
SKIP_BROKEN_REPOS=true                   # repositório fora do ar não impede o monitoramento dos demais
LOAD_DNF_PLUGINS=true                    # honra versionlock e outros plugins, como o comando dnf
COLLECT_TIMEOUT_MINUTES=15               # tempo máximo da consulta ao DNF
IGNORE_INTERFACES=lo,docker*,br-*,veth*,virbr*,cni*,flannel*,cali*,tun*,tap*,podman*

[Email]
SMTP_SERVER=smtp.exemplo.com             # vazio = desativa o e-mail
SMTP_PORT=587
SMTP_SECURITY=auto                       # auto | ssl | starttls | none
SMTP_USER=alerta@exemplo.com             # vazio = sem autenticação (relay local)
SMTP_PASS=sua_senha_segura
SENDER_NAME=DNF Monitor
SENDER_EMAIL=alerta@exemplo.com
RECIPIENT_EMAILS=admin@exemplo.com, suporte@exemplo.com
```

| Chave | Padrão | Descrição |
|---|---|---|
| `CHECK_INTERVAL_HOURS` | `4` | Intervalo entre checagens. |
| `REMINDER_DAYS` | `0` | Lembrete periódico enquanto houver pendências. |
| `NOTIFY_ON_CLEAR` | `false` | Envia "Servidor em dia" quando a fila zera. |
| `UPDATE_CACHE` | `true` | Renova os metadados a cada ciclo (como `dnf makecache --refresh`). `false` = só usa o cache. |
| `SKIP_BROKEN_REPOS` | `true` | Ignora repositórios inacessíveis. `false` = qualquer falha de repositório aborta o ciclo. |
| `LOAD_DNF_PLUGINS` | `true` | Carrega os plugins do DNF (essencial para `versionlock`). |
| `COLLECT_TIMEOUT_MINUTES` | `15` | Tempo máximo da consulta ao DNF. |
| `IGNORE_INTERFACES` | `lo,docker*,…` | Interfaces fora da lista de IPs (curingas). |
| `LOG_FILE` / `STATE_FILE` | `/var/log/dnf-monitor.log` / `/var/lib/dnf-monitor/state.json` | Caminhos; `LOG_FILE=` vazio = só stdout/journal. |
| `SMTP_SECURITY` | `auto` | `auto`: 465 → SSL, demais → STARTTLS quando oferecido (recusa enviar senha sem TLS). |

Valores de exemplo (`seu_bot_token_aqui`, `smtp.seuservidor.com`…) contam como "não configurado". Se nenhum canal
for válido, o serviço sai com código 78 e o systemd **não** entra em loop de reinício.

Guias passo a passo: **[Telegram](docs/telegram.md)** · **[E-mail/SMTP](docs/email.md)** (Gmail, Microsoft 365, SSL, relay local).

## 🧰 Linha de comando

| Comando | Função |
|---|---|
| `dnf-monitor --check-config` | Valida a configuração (código 78 se inválida). |
| `dnf-monitor --test-notify` | Envia uma mensagem de teste a cada canal. |
| `dnf-monitor --once` | Faz uma checagem e sai (cron/timer). |
| `dnf-monitor --dry-run` | Mostra os alertas sem enviar nem gravar estado. |
| `dnf-monitor -c ARQUIVO` | Usa outro arquivo de configuração. |
| `dnf-monitor --version` | Mostra a versão. |

Manual: `man dnf-monitor`.

## 🔧 Operação do dia a dia

```bash
systemctl status dnf-monitor                 # estado do serviço
sudo journalctl -u dnf-monitor -f            # logs pelo systemd
sudo tail -f /var/log/dnf-monitor.log        # log próprio (rotação semanal, sem reiniciar o serviço)
sudo systemctl reload dnf-monitor            # recarrega o .conf e faz uma checagem imediata
sudo systemctl restart dnf-monitor           # reinicia
sudo dnf versionlock add <pacote>            # pacotes travados deixam de ser alertados
sudo rm /var/lib/dnf-monitor/state.json      # (com o serviço parado) esquece o que já foi notificado
```

## 🔒 Segurança

- Token do Telegram e senha SMTP são **removidos de qualquer log**, inclusive de mensagens de erro de rede.
- `/etc/dnf-monitor/dnf-monitor.conf` é instalado com modo **0600**.
- Credenciais SMTP só trafegam **sob TLS** (a menos que você force `SMTP_SECURITY=none`).
- Unit do systemd com `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `PrivateDevices`,
  `RestrictAddressFamilies`, entre outros — verifique com `systemd-analyze security dnf-monitor`.
- Todo texto dinâmico é escapado (HTML), evitando injeção por nomes de pacote ou hostname.
- O daemon **não instala nada**: apenas consulta e avisa.

Encontrou uma vulnerabilidade? Veja a [política de segurança](SECURITY.md).

## ✅ Compatibilidade

Testado automaticamente (instalação do `.rpm`, upgrade da v1.0-6, solver real do DNF, alertas, restart, remoção) em
containers, e com **systemd real** (hardening ativo) em Rocky 9 e AlmaLinux 10:

| Distribuição | Python | DNF | Status |
|---|---|---|---|
| Rocky Linux 8 (≈ RHEL 8) | 3.6 | 4.7 | ✅ |
| Rocky Linux 9 (≈ RHEL 9) | 3.9 | 4.14 | ✅ (também com systemd) |
| AlmaLinux 10 (≈ RHEL 10) | 3.12 | 4.20 | ✅ (também com systemd) |
| Oracle Linux 9 | 3.9 | 4.14 | ✅ |

Os testes unitários rodam também em Python 3.6. CentOS Stream 8+ e demais clones EL equivalentes devem funcionar.
**CentOS 7** (YUM) não é suportado. Não foi testado com SELinux em modo *enforcing*.

## ❓ Perguntas frequentes

**Vai atualizar meus pacotes sozinho?** Não. Ele só **avisa**. (O máximo que faz no sistema é renovar o cache de
metadados, e dá para desligar com `UPDATE_CACHE=false`.)

**Por que a lista é menor que a do `dnf check-update`?** Porque o monitor usa o solver: pacotes barrados por
dependências quebradas, `exclude=` ou `versionlock` não entram, pois o `dnf upgrade` também não os instalaria.

**Posso usar só Telegram ou só e-mail?** Sim: deixe o outro canal vazio.

**Recebi o mesmo aviso duas vezes.** Só deve ocorrer se você apagou o `state.json` ou se há versões novas dos pacotes.
Veja [Solução de problemas](docs/solucao-de-problemas.md).

**Posso adicionar Discord/Slack/ntfy?** Sim — crie uma classe com `name` e `send(alert)`; veja
[a arquitetura](docs/arquitetura.md#estrutura-do-código).

## 📚 Documentação

- [Configurando o Telegram](docs/telegram.md)
- [Configurando o e-mail (SMTP)](docs/email.md)
- [Como funciona (solver, estado, segurança)](docs/arquitetura.md)
- [Solução de problemas](docs/solucao-de-problemas.md)
- [Changelog](CHANGELOG.md) · [Contribuindo](CONTRIBUTING.md) · [Segurança](SECURITY.md)

## 🛠️ Desenvolvimento e contribuição

```bash
python3 -m unittest discover -s tests -v   # testes unitários
./build.sh                                  # gera o .rpm em dist/
tests/docker/run-matrix.sh                  # instala o .rpm em containers EL com o DNF real
tests/docker/systemd-test.sh                # idem, com systemd real
```

Veja [CONTRIBUTING.md](CONTRIBUTING.md). Releases são geradas automaticamente ao criar uma tag `vX.Y.Z`.

---

<div align="center">Feito por <a href="https://github.com/AprendendoLinux">AprendendoLinux</a></div>
