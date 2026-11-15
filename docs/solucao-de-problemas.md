# Solução de problemas

Comece sempre por:

```bash
sudo dnf-monitor --check-config      # a configuração é válida?
sudo dnf-monitor --test-notify       # os canais entregam?
sudo dnf-monitor --dry-run           # o que seria enviado agora?
sudo journalctl -u dnf-monitor -n 50 # o que o serviço diz?
```

## O serviço não iniciou depois da instalação

Esperado quando a configuração ainda tem os valores de exemplo. O `%post` só inicia o serviço com configuração
válida. Edite `/etc/dnf-monitor/dnf-monitor.conf`, rode `--check-config` e então
`sudo systemctl restart dnf-monitor`.

## `Nenhum canal de notificação válido` / serviço em `failed` com status 78

Nem Telegram nem e-mail estão configurados de verdade (valores como `seu_bot_token_aqui` ou `smtp.seuservidor.com`
são tratados como vazios). Corrija o `.conf`. Depois: `sudo systemctl reset-failed dnf-monitor && sudo systemctl start dnf-monitor`.

## `Falha ao consultar o DNF`

O processo filho que fala com o DNF falhou. O log mostra as últimas linhas do erro. Causas comuns:

* **Repositório inacessível / certificado / proxy:** teste `sudo dnf makecache --refresh`. Com
  `SKIP_BROKEN_REPOS=true` (padrão) um repositório fora do ar é ignorado; se mesmo assim falhar, é outro problema.
* **Lock do DNF:** outro `dnf` em execução. O ciclo é tentado de novo em até 15 minutos.
* **Sem rede/DNS** no momento da checagem.
* **Timeout:** aumente `COLLECT_TIMEOUT_MINUTES`.
* **`python3-dnf` ausente:** `sudo dnf install python3-dnf`.

Enquanto isso acontecer, **nenhum alerta falso de "tudo em dia" é enviado** e o estado não é alterado.

## Instalei o monitor e ele nunca avisa

Rode `sudo dnf-monitor --dry-run`. Se a lista vier vazia, confira o que o próprio DNF faria:
`sudo dnf upgrade --assumeno --nobest --skip-broken`. O monitor lista o que **essa** transação instalaria: pacotes
barrados por `exclude=`, `versionlock` (plugin carregado) ou dependências quebradas não contam (use `dnf check-update` para ver tudo).

> A v1.0 tinha um defeito: lia um atributo (`transaction.upgrade_set`) que não existe no DNF 4.x, então a lista
> ficava sempre vazia e nunca alertava. Corrigido na v1.1.0 — atualize.

## Aparece pacote que eu não consigo atualizar

Verifique `dnf upgrade --assumeno`. Se o DNF recusar (dependências), ele não deve aparecer no monitor; se aparecer, abra
uma issue com a saída de `dnf-monitor --dry-run -v` e de `dnf upgrade --assumeno`.

## Telegram: `HTTP 401` / `HTTP 404`

Token errado ou revogado. Confira com `curl https://api.telegram.org/bot<TOKEN>/getMe`.

## Telegram: `HTTP 400 - chat not found`

O `chat_id` está errado, ou você ainda não enviou nenhuma mensagem ao bot (ou não o adicionou ao grupo).
Veja [telegram.md](telegram.md).

## Telegram: `HTTP 403 - bot was blocked` / `bot was kicked`

O usuário bloqueou o bot ou ele foi removido do grupo. Desbloqueie/readicione.

## E-mail: `o servidor não oferece STARTTLS; recusando enviar credenciais`

O servidor não suporta STARTTLS na porta escolhida. Use a porta/modo corretos (`465` + `ssl` ou `587` + `starttls`).
Só use `SMTP_SECURITY=none` em relays locais confiáveis.

## E-mail: `Authentication failed` / `535`

Senha errada, ou o provedor exige *senha de app* (Gmail) / *SMTP AUTH* habilitado (Microsoft 365).
Veja [email.md](email.md).

## Não recebo o aviso de reinicialização

Ele depende de `needs-restarting -r`, do pacote `dnf-utils`: `sudo dnf install dnf-utils`. Teste manualmente:
`needs-restarting -r; echo $?` (1 = reinicialização necessária).

## Quero um alerta de novo (reset)

```bash
sudo systemctl stop dnf-monitor
sudo rm /var/lib/dnf-monitor/state.json
sudo systemctl start dnf-monitor
```

## O alerta mostra IPs de Docker/Podman/VMs

Ajuste `IGNORE_INTERFACES` (curingas permitidos), por exemplo `IGNORE_INTERFACES=lo,docker*,podman*,br-*,veth*,virbr*,wg*`.

## `Permission denied` ao rodar `dnf-monitor` como usuário comum

O `.conf` é `0600` (root). Use `sudo`.

## Alterei o `.conf` e nada mudou

`sudo systemctl reload dnf-monitor` (recarrega e já faz uma checagem) ou `sudo systemctl restart dnf-monitor`.

## Atualizei da v1.0 e apareceu um `.rpmnew`

O RPM mantém o seu `dnf-monitor.conf` e grava o novo exemplo em `dnf-monitor.conf.rpmnew`. As chaves novas têm valores
padrão; compare os dois se quiser adotar as novidades. O `.conf` passa a ter modo `0600`.

## SELinux

Os testes automatizados rodam em containers, **sem** SELinux em modo *enforcing*. O daemon só lê metadados do DNF e grava
em `/var/lib/dnf-monitor` e `/var/log`, então não deve precisar de ajustes; se você vir `AVC` em
`ausearch -m avc -ts recent`, abra uma issue com a saída.

## Funciona no CentOS 7 / Amazon Linux / Fedora?

O alvo é **EL8, EL9 e EL10** (RHEL, Rocky, AlmaLinux, Oracle Linux, CentOS Stream), onde o DNF 4 é padrão e
`python3-dnf` existe. CentOS 7 usa YUM e não é suportado. Fedora costuma funcionar, mas não é testado.
