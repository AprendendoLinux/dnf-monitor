# Configurando o lado do cliente

O que o **cliente** recebe é um relatório diário: quantas vezes o administrador atualizou o servidor, quantos pacotes
foram atualizados (com sucesso e com falha) e, se houver pendência antiga, um aviso sugerindo falar com o
administrador. Este guia leva do zero ao primeiro relatório. Para as regras detalhadas, veja
[Relatório diário](relatorio.md); para todas as chaves, a [Referência de configuração](configuracao.md).

## Índice

1. [Antes de começar](#1-antes-de-começar)
2. [Telegram do cliente](#2-telegram-do-cliente)
3. [E-mail do cliente](#3-e-mail-do-cliente)
4. [Nome do administrador, horário e fuso](#4-nome-do-administrador-horário-e-fuso)
5. [Teste antes de valer](#5-teste-antes-de-valer)
6. [O primeiro relatório real](#6-o-primeiro-relatório-real)
7. [O que o cliente vê (e o que não vê)](#7-o-que-o-cliente-vê-e-o-que-não-vê)
8. [Boas práticas](#8-boas-práticas)

---

## 1. Antes de começar

Você precisa de:

- O Plus **instalado e funcionando para os administradores** ([Guia de uso](uso.md)): o cliente é um acréscimo.
- Decidir **por qual canal** o cliente recebe: Telegram, e-mail ou os dois. Dá para usar só um.

O cliente usa **configurações próprias**, separadas das dos administradores:

| Administradores | Cliente |
|---|---|
| `[Telegram]` | `[ClientTelegram]` |
| `[Email]` | `[ClientEmail]` |

Edite o arquivo:

```bash
sudo nano /etc/dnf-monitor-plus/dnf-monitor.conf
```

No fim dele já existem as seções `[Report]`, `[ClientTelegram]` e `[ClientEmail]`, com tudo comentado (`#`). Basta
**descomentar e preencher** o que for usar.

## 2. Telegram do cliente

### 2.1. Crie um **segundo** bot

O bot do cliente deve ser **diferente** do dos administradores, para um lado nunca ver as mensagens do outro.

1. No Telegram, abra o [@BotFather](https://t.me/BotFather) e envie `/newbot`.
2. Escolha um nome amigável para o cliente (ex.: "Relatório do Servidor – Empresa X") e um usuário terminado em `bot`.
3. Guarde o **token** (algo como `987654321:AAF-xyz…`).

### 2.2. Descubra o `chat_id` do cliente

Escolha **um** destes caminhos.

**a) Conversa direta com uma pessoa do cliente**

1. Peça à pessoa para abrir o bot e enviar `/start` (um bot só pode escrever depois de receber uma mensagem).
2. Consulte as mensagens recebidas:

   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN_DO_BOT_DO_CLIENTE>/getUpdates"
   ```

3. Procure `"chat":{"id":123456789,…` — esse número é o `chat_id` (positivo).

**b) Grupo (recomendado para mais de uma pessoa)**

1. Crie um grupo com as pessoas do cliente e **adicione o bot** a ele.
2. Envie uma mensagem no grupo e repita o `getUpdates`.
3. O `id` de grupo é **negativo** (supergrupos: `-100…`).

> Se `getUpdates` voltar vazio, envie outra mensagem ao bot (ou no grupo) e tente de novo. Se o bot não aparece em
> grupos, em @BotFather use `/setprivacy` → *Disable*.

### 2.3. Configure

```ini
[ClientTelegram]
BOT_TOKEN=987654321:AAF-xyz...
CHAT_IDS=-1009876543210
```

Vários destinos: `CHAT_IDS=123456789, -1009876543210`. Um destino com erro não atrapalha os demais.

## 3. E-mail do cliente

Use as **mesmas chaves** do `[Email]`. Pode reaproveitar o servidor SMTP dos administradores (repita os dados) e só
mudar os destinatários.

```ini
[ClientEmail]
SMTP_SERVER=smtp.exemplo.com
SMTP_PORT=587
SMTP_USER=alerta@exemplo.com
SMTP_PASS=sua-senha
SENDER_NAME=Suporte Técnico
SENDER_EMAIL=alerta@exemplo.com
RECIPIENT_EMAILS=responsavel@empresa.com.br, financeiro@empresa.com.br
```

- **`SENDER_NAME`** é o nome que o cliente vê na caixa de entrada. Escolha algo que ele reconheça ("Suporte Técnico",
  o nome da sua empresa…). Por padrão é "DNF Monitor".
- **Cada destinatário recebe a sua própria mensagem.** Um endereço inválido não atrapalha os outros.
- Dicas de SMTP (Gmail, Microsoft 365, SSL, relay local): [guia de e-mail](../../docs/email.md).
- Para não ir para o spam, envie por um servidor/domínio legítimo (SPF/DKIM do remetente).

## 4. Nome do administrador, horário e fuso

```ini
[Report]
ADMIN_NAME=Henrique Fagundes
REPORT_TIME=22:30
REPORT_TIMEZONE=America/Sao_Paulo
```

- **`ADMIN_NAME`**: aparece como "O administrador **Henrique Fagundes** atualizou o servidor…". Você trabalha como
  `root`, e o histórico do DNF só guarda o usuário (`root`), por isso o nome vem desta chave. **Em branco**, o texto fica "A equipe técnica
  atualizou o servidor…".
- **`REPORT_TIME`** e **`REPORT_TIMEZONE`**: quando o relatório sai. Padrão: **22:30**, fuso de **São Paulo**. Mude para
  o horário que faça sentido para o cliente.
- **`PENDING_ALERT_DAYS`** (padrão `3`): depois de quantos dias parado um pacote pendente entra no relatório como
  pendência. `0` desliga esse aviso.

> O texto de **pendência antiga** nunca cita nomes: diz apenas "entrar em contato com o administrador do servidor".

> 📄 Para ver **como tudo isso fica junto em um arquivo só**, veja o [exemplo de configuração completa](exemplo-configuracao.md).

## 5. Teste antes de valer

Aplique a configuração e **teste, sem esperar a noite**:

```bash
sudo dnf-monitor-plus --check-config        # deve mostrar "Relatório ao cliente: telegram, email" (ou o que você usa)
sudo systemctl reload dnf-monitor-plus

sudo dnf-monitor-plus --test-notify         # manda um teste a TODOS os canais (admin e cliente)
sudo dnf-monitor-plus --report --dry-run    # só mostra o relatório com os dados reais desta máquina, sem enviar
```

O `--test-notify` entrega ao cliente um **relatório de exemplo**, marcado como teste, com dados fictícios. Confira
com o cliente como ele aparece no celular e no e-mail.

Quando quiser enviar um relatório **de verdade** antes do horário:

```bash
sudo dnf-monitor-plus --report
```

## 6. O primeiro relatório real

- O histórico começa a valer **a partir do momento em que o Plus foi instalado**: ele não reenvia atualizações antigas.
- No horário `REPORT_TIME`, se tiver havido atualização desde o último relatório (ou houver pendência antiga), o cliente
  recebe. Dia sem novidade: **nada** é enviado (use `SEND_EMPTY=true` se quiser mensagem todo dia).
- A contagem de "pendente há N dias" começa quando o Plus **vê** o pacote pendente pela primeira vez; então a primeira
  pendência antiga só pode aparecer **depois de `PENDING_ALERT_DAYS` dias de funcionamento**.

## 7. O que o cliente vê (e o que não vê)

| | Telegram | E-mail |
|---|---|---|
| Nome do servidor (hostname) | ✅ | ✅ |
| Nome do administrador (`ADMIN_NAME`) | ✅ | ✅ |
| Quantas vezes houve atualização, totais de sucesso e falha | ✅ | ✅ |
| Horário de cada atualização | — | ✅ |
| Lista de pacotes atualizados (nome e versões) | — | ✅ (`PACKAGE_LIST=true`) |
| Pacotes **com falha** | — | ✅ |
| Pacotes **pendentes** há muito tempo, com a idade | ✅ | ✅ |
| Reinicialização pendente | ✅ | ✅ |
| **Endereços IP** do servidor | ❌ nunca | ❌ nunca |
| Alertas dos administradores (kernel, segurança…) | ❌ nunca | ❌ nunca |

## 8. Boas práticas

- **Use um bot e um remetente de e-mail dedicados ao cliente.** Nada dos administradores deve passar por ali.
- **Prefira um grupo** no Telegram quando houver mais de uma pessoa do lado do cliente.
- **Se o e-mail do cliente for muito detalhado**, ou o cliente não precisar ver nomes de pacotes, use
  `PACKAGE_LIST=false`: o e-mail mostra só os totais e a linha do tempo.
- **Combine com o cliente** o que significa "pendência": são atualizações disponíveis que ainda não foram instaladas.
  Muitas vezes há motivo (janela de manutenção, teste), e o aviso é só um convite para conversar.
- **Revise após mudar de servidor SMTP ou de bot**: rode `--test-notify` de novo.
