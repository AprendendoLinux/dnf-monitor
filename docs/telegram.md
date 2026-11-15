# Configurando o Telegram

## 1. Criar o bot

1. No Telegram, abra uma conversa com o [@BotFather](https://t.me/BotFather).
2. Envie `/newbot` e siga as perguntas (nome e nome de usuário terminado em `bot`).
3. O BotFather devolve o **token**, algo como `123456789:AAE-abc...`. Guarde-o: quem tem o token controla o bot.

## 2. Descobrir o `chat_id`

**Conversa privada com você**

1. Abra o seu bot e envie qualquer mensagem (ex.: `/start`). *O bot só pode escrever depois de receber uma mensagem.*
2. Consulte as mensagens recebidas:

   ```bash
   curl -s "https://api.telegram.org/bot<SEU_TOKEN>/getUpdates"
   ```

3. Procure `"chat":{"id":12345678,...}` — esse número é o `chat_id`.

**Grupo ou canal**

1. Adicione o bot ao grupo (em canais, como administrador) e envie uma mensagem no grupo.
2. Rode o mesmo `getUpdates`. O `id` de grupos é **negativo** (ex.: `-1001234567890` para supergrupos).

> Dica: se `getUpdates` voltar vazio, envie outra mensagem ao bot e tente de novo.

## 3. Configurar

```ini
[Telegram]
BOT_TOKEN=123456789:AAE-abc...
CHAT_IDS=12345678, -1001234567890
```

O prefixo `bot` no token é opcional. Para enviar a vários destinos, separe os IDs por vírgula.

## 4. Testar

```bash
sudo dnf-monitor --test-notify
```

Você deve receber "🧪 Teste do DNF Monitor".

## Observações

* **Segurança:** o token fica em `/etc/dnf-monitor/dnf-monitor.conf` (modo `0600`) e é removido de qualquer log.
  Se ele vazar, gere outro com `/revoke` no BotFather.
* **Limites:** o Telegram limita envios (HTTP 429). O daemon respeita o `retry_after` e tenta de novo.
* **Bot API própria:** se você roda um servidor local da Bot API, use `API_BASE=http://host:8081` em `[Telegram]`.
* **Proxy:** variáveis `HTTPS_PROXY`/`https_proxy` no ambiente do serviço são respeitadas
  (`sudo systemctl edit dnf-monitor` → `[Service]` → `Environment=HTTPS_PROXY=...`).
