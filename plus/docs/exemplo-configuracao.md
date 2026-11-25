# Exemplo de configuração completa

Este é um arquivo de configuração **real, de um servidor em produção, com todos os dados trocados por valores
fictícios**. Ele mostra o Plus configurado de ponta a ponta:

| Público | Telegram | E-mail |
|---|---|---|
| 🛠️ **Analista** (alertas na hora) | `[Telegram]` | `[Email]` |
| 🧑‍💼 **Cliente** (relatório diário) | `[ClientTelegram]` | `[ClientEmail]` |

É o ponto de partida mais prático: copie, troque os valores marcados na tabela abaixo e pronto. Para o significado de
**cada chave**, veja a [Referência de configuração](configuracao.md).

## O arquivo

Caminho: `/etc/dnf-monitor-plus/dnf-monitor.conf` (modo `0600`, dono `root`).

```ini
[Telegram]
BOT_TOKEN=123456789:AAExExemploDeTokenDoBotDoAnalista01
# Insira os IDs separados por vírgula
CHAT_IDS=12345678

[Monitor]
# Tempo de rotação e verificação em horas (pode usar decimais, ex: 0.5 para 30 min)
CHECK_INTERVAL_HOURS=1

[Email]
# E-mail do(s) analista(s): recebem os alertas de atualização disponível.
SMTP_SERVER=smtp.exemplo.com
SMTP_PORT=2525
SMTP_SECURITY=none
SENDER_NAME=DNF Monitor Plus
SENDER_EMAIL=monitor@exemplo.com
RECIPIENT_EMAILS=analista@exemplo.com


# >>> DNF Monitor Plus >>> (daqui para baixo: relatório diário ao CLIENTE) >>>
#
# O CLIENTE não recebe alertas: recebe UM relatório por dia dizendo quantas vezes o
# administrador atualizou o servidor e o total de pacotes (com sucesso e com falha).
# Atualizações do dnf-automatic não entram. Para ativar, preencha pelo menos um
# canal do cliente ([ClientTelegram] e/ou [ClientEmail]) com um bot/endereços DIFERENTES
# dos dos administradores. Sem nenhum canal de cliente, nada é enviado a ele.

# ----------------------------------------------------------------------------
#  [Report] — quando e o que vai no relatório
# ----------------------------------------------------------------------------
[Report]
# Nome que aparece no relatório: "O administrador <ADMIN_NAME> atualizou o servidor...".
# Em branco: "A equipe técnica atualizou o servidor...".
ADMIN_NAME=Maria Souza
# Horário (HH:MM, 24 h) e fuso em que o relatório sai. Padrões: 22:30 e America/Sao_Paulo.
# O fuso é um nome da base tz (ls /usr/share/zoneinfo). Ex.: America/Manaus, UTC.
REPORT_TIME=22:30
REPORT_TIMEZONE=America/Sao_Paulo
# Pendência antiga: se algum pacote espera instalação há MAIS de N dias, o relatório do dia
# lista todos os pendentes e sugere falar com o administrador (sem citar nomes), mesmo em dia
# sem nenhuma atualização. Repete todo dia até serem instalados. 0 = desativa. Padrão: 3.
PENDING_ALERT_DAYS=3
# true: envia relatório até em dia sem atualização e sem pendência antiga. Padrão: false.
SEND_EMPTY=false
# true: o relatório por e-mail traz a tabela de pacotes (nome e versões). Padrão: true.
PACKAGE_LIST=true
# Banco de histórico (SQLite). Padrão abaixo.
DB_FILE=/var/lib/dnf-monitor-plus/history.db

# ----------------------------------------------------------------------------
#  [ClientTelegram] — bot do CLIENTE (diferente do bot dos administradores)
# ----------------------------------------------------------------------------
[ClientTelegram]
BOT_TOKEN=987654321:AAFxExemploDeTokenDoBotDoCliente02
CHAT_IDS=-1009876543210
API_BASE=https://api.telegram.org

# ----------------------------------------------------------------------------
#  [ClientEmail] — e-mail do CLIENTE (mesmas chaves de [Email])
# ----------------------------------------------------------------------------
[ClientEmail]
SMTP_SERVER=smtp.exemplo.com
SMTP_PORT=2525
SMTP_SECURITY=none
#SMTP_USER=alerta@exemplo.com
#SMTP_PASS=sua-senha
SENDER_NAME=DNF Monitor Plus
SENDER_EMAIL=monitor@exemplo.com
RECIPIENT_EMAILS=cliente@empresa.exemplo.com
```

> Este exemplo é **testado automaticamente**: o CI carrega o bloco acima com o próprio leitor de configuração do Plus e
> confere que os quatro canais (Telegram e e-mail, de analista e de cliente) ficam ativos. Se você copiar e colar, o
> arquivo é válido (só não vai entregar nada até você trocar os valores fictícios pelos seus).

## O que trocar

| Valor fictício | Troque por | Onde conseguir |
|---|---|---|
| `123456789:AAExExemplo…01` | O **token do bot do analista** | [@BotFather](https://t.me/BotFather) → `/newbot` ([guia](../../docs/telegram.md)) |
| `12345678` | O **chat ID do analista** (positivo = conversa; negativo = grupo) | `getUpdates`, depois de mandar `/start` ao bot |
| `987654321:AAFxExemplo…02` | O **token do bot do cliente** (um bot **diferente** do do analista) | @BotFather |
| `-1009876543210` | O **chat ID do cliente** (aqui, um grupo) | `getUpdates`, depois de mandar uma mensagem ao grupo |
| `smtp.exemplo.com` e a porta | O seu servidor/relay SMTP | Sua hospedagem de e-mail ([guia](../../docs/email.md)) |
| `monitor@exemplo.com` | O e-mail do **remetente** | Uma caixa do seu domínio |
| `analista@exemplo.com` | O(s) e-mail(s) do analista, separados por vírgula | — |
| `cliente@empresa.exemplo.com` | O(s) e-mail(s) do cliente, separados por vírgula | — |
| `Maria Souza` | O nome que o cliente deve ver como administrador | — |

## Como ler cada bloco

- **`[Telegram]` e `[Email]` → o analista.** Recebem o **alerta de atualização disponível**, na hora (cerca de 1
  minuto depois que o DNF detecta). `CHAT_IDS` e `RECIPIENT_EMAILS` aceitam vários valores separados por vírgula.
- **`[Monitor]`.** `CHECK_INTERVAL_HOURS=1` é a checagem completa de segurança a cada hora; a detecção imediata
  acontece além dela.
- **`[Report]` → quando e o que vai no relatório do cliente.** Neste exemplo: todo dia às **22:30** (fuso de São
  Paulo), com o nome **Maria Souza**; pacote pendente há **mais de 3 dias** entra no relatório; dia sem novidade não
  gera mensagem (`SEND_EMPTY=false`); o e-mail traz a tabela de pacotes (`PACKAGE_LIST=true`).
- **`[ClientTelegram]` e `[ClientEmail]` → o cliente.** Recebem **somente** o relatório diário. Repare que o bot e os
  destinos são **diferentes** dos do analista: assim um lado nunca vê as mensagens do outro.

### Detalhes que valem atenção

1. **O comentário `# >>> DNF Monitor Plus >>>` é um marcador.** Se o servidor vem do DNF Monitor clássico, o instalador
   usa essa linha para acrescentar as seções do Plus (veja a [migração](migracao.md)). Pode manter ou apagar.
2. **Servidor SMTP com senha.** O exemplo usa um relay interno, **sem autenticação e sem criptografia**
   (`SMTP_SECURITY=none`), que só serve em rede confiável. Para um servidor da internet, descomente usuário e senha e
   use `587` com `auto`:

   ```ini
   SMTP_PORT=587
   SMTP_SECURITY=auto
   SMTP_USER=alerta@exemplo.com
   SMTP_PASS=sua-senha
   ```

3. **Testar o cliente com você mesmo.** Antes de apontar o cliente de verdade, é comum colocar o **seu próprio chat ID**
   em `[ClientTelegram]` e o seu e-mail em `[ClientEmail]` e rodar `dnf-monitor-plus --test-notify`: você vê o relatório
   exatamente como o cliente verá. Depois, troque pelos dados dele.
4. **Chat ID em branco desativa o canal.** Um canal do Telegram só fica ativo com **token e chat ID** preenchidos;
   comente a linha `CHAT_IDS` do cliente e o Telegram do cliente é simplesmente ignorado (o e-mail continua).
5. **Segredos.** O arquivo tem tokens (e, se houver, senha). Mantenha modo `0600`, **não** o publique em repositório e,
   se um token vazar, revogue-o no @BotFather (`/revoke`).

## Aplicar e testar

```bash
sudo nano /etc/dnf-monitor-plus/dnf-monitor.conf       # cole o exemplo e troque os valores
sudo dnf-monitor-plus --check-config                   # deve mostrar:
                                                       #   Canais ativos: telegram, email
                                                       #   Relatório ao cliente: telegram, email
sudo dnf-monitor-plus --test-notify                    # alerta de teste ao analista + relatório de exemplo ao cliente
sudo systemctl reload dnf-monitor-plus
```

Veja também: [Guia de uso](uso.md) · [Configuração do cliente](cliente.md) · [Referência de configuração](configuracao.md).
