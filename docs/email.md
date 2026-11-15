# Configurando o e-mail (SMTP)

```ini
[Email]
SMTP_SERVER=smtp.exemplo.com
SMTP_PORT=587
SMTP_SECURITY=auto
SMTP_USER=alerta@exemplo.com
SMTP_PASS=senha-ou-token-de-app
SENDER_NAME=DNF Monitor
SENDER_EMAIL=alerta@exemplo.com
RECIPIENT_EMAILS=admin@exemplo.com, suporte@exemplo.com
```

Deixe `SMTP_SERVER` em branco (ou remova a seção) para desativar o e-mail.

## Modos de segurança (`SMTP_SECURITY`)

| Valor | Comportamento |
|---|---|
| `auto` *(padrão)* | Porta **465** → SSL/TLS direto. Outras portas → STARTTLS se o servidor oferecer. Se houver usuário/senha e o servidor **não** oferecer STARTTLS, o envio é **recusado** (senha nunca vai em texto puro). |
| `ssl` | SSL/TLS direto (normalmente porta 465). |
| `starttls` | Exige STARTTLS (normalmente porta 587). |
| `none` | Sem criptografia. Só para relays locais confiáveis (ex.: Postfix em `127.0.0.1:25`). |

O certificado do servidor é verificado (`ca-certificates` do sistema).

## Exemplos

**Gmail** — exige verificação em duas etapas e uma [senha de app](https://myaccount.google.com/apppasswords):

```ini
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=voce@gmail.com
SMTP_PASS=senha-de-app-de-16-letras
SENDER_EMAIL=voce@gmail.com
```

**Microsoft 365** (o administrador precisa habilitar *SMTP AUTH* para a conta):

```ini
SMTP_SERVER=smtp.office365.com
SMTP_PORT=587
SMTP_USER=alerta@empresa.com
SMTP_PASS=...
SENDER_EMAIL=alerta@empresa.com
```

**SSL direto (465):**

```ini
SMTP_SERVER=smtp.exemplo.com
SMTP_PORT=465
```

**Postfix/relay local sem autenticação:**

```ini
SMTP_SERVER=127.0.0.1
SMTP_PORT=25
SMTP_SECURITY=none
SMTP_USER=
SENDER_EMAIL=dnf-monitor@meuservidor.local
RECIPIENT_EMAILS=admin@empresa.com
```

## Detalhes

* Cada destinatário recebe **uma mensagem própria** (ninguém vê os endereços dos outros). Se um endereço for
  recusado, os demais continuam sendo enviados.
* A mensagem é `multipart/alternative`: HTML (tabela com versões e marca de segurança) e texto puro.
* Assunto: `[servidor] 🚨 CRÍTICO: Kernel Update`, `[servidor] 🔐 Atualizações de segurança (N)`,
  `[servidor] Atualizações Disponíveis`, etc.
* Comentários no `.conf` podem ficar no fim da linha (`SMTP_PORT=587 # STARTTLS`). Evite senhas com ` #`
  (espaço seguido de `#`). Senhas com `%` funcionam.
* Para o e-mail não cair em spam, use um `SENDER_EMAIL` do mesmo domínio do servidor SMTP (SPF/DKIM).
