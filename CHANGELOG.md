# Changelog

## 1.1.1
- Adiciona a licença **GPL-3.0-or-later** (`LICENSE`), o identificador SPDX no código e o `%license` no pacote RPM.

## 1.1.0

### Correções
- **O monitor passa a funcionar de verdade no DNF 4.x (EL8/EL9/EL10):** o código anterior lia
  `base.transaction.upgrade_set`, atributo que **não existe** nessas versões; o erro era engolido e a lista
  ficava sempre vazia (nenhum alerta). Agora usa `install_set` (que já inclui upgrades e kernels, que são
  "installonly").
- Falha do DNF (rede, repositório, lock) não é mais interpretada como "nenhuma atualização".
- Token do Telegram e senha SMTP nunca mais aparecem nos logs (filtro de redação).
- A URL da API do Telegram agora inclui `bot` automaticamente (antes era preciso digitar `bot123:...` no token).
- Estado persistente (`/var/lib/dnf-monitor/state.json`): restart, reboot, upgrade do pacote e rotação de log
  não reenviam mais o mesmo alerta (antes o `logrotate` reiniciava o serviço toda semana, repetindo o alerta).
- Deduplicação por pacote/versão (e não pela contagem); alerta só quando surge algo novo.
- Alerta que falhou em todos os canais é retentado (não é mais dado como enviado).
- Timeouts e novas tentativas no Telegram e no SMTP; falha em um destinatário não afeta os demais.
- Parser de configuração aceita comentários no fim da linha (o exemplo do README quebrava) e senhas com `%`.
- Telegram em HTML com escape; e-mail com escape, `Date`, `Message-ID` e parte `text/plain`.
- "Crítico" só para o kernel em si (`kernel`, `kernel-core`, `kernel-modules*`, `kernel-uek*`, `kernel-rt*`);
  `kernel-headers`, `kernel-devel`, `kernel-tools` etc. não disparam mais.
- RPM: o `%preun` usava `\$1` (literal) e nunca parava nem desabilitava o serviço na remoção; corrigido.
- RPM: o serviço só é iniciado se a configuração for válida (antes subia com token vazio e ficava errando).
- `%changelog` do spec com datas válidas e em ordem.

### Novidades
- Consulta ao DNF em **processo filho isolado** com timeout (a libdnf vazava memória num daemon de longa vida e
  o DNF podia travar em mirrors lentos).
- Carrega os plugins do DNF (`LOAD_DNF_PLUGINS`): `versionlock` e afins passam a ser respeitados (a API não os carrega sozinha).
- Um repositório fora do ar não cega mais o monitor (`SKIP_BROKEN_REPOS`); se o solver falhar, usa a lista
  simples de upgrades.
- Destaque de atualizações de **segurança** (advisories `security` do updateinfo).
- Aviso de **reinicialização pendente** via `needs-restarting -r` (pacote `dnf-utils`).
- `REMINDER_DAYS`, `NOTIFY_ON_CLEAR`, `UPDATE_CACHE`, `SKIP_BROKEN_REPOS`, `COLLECT_TIMEOUT_MINUTES`,
  `IGNORE_INTERFACES`, `SMTP_SECURITY`, `API_BASE`.
- Recarga da configuração com `systemctl reload` (SIGHUP).
- CLI: `--check-config`, `--test-notify`, `--once`, `--dry-run`.
- Sem dependência de `python3-requests` (usa só a biblioteca padrão).
- Script movido para `/usr/libexec/dnf-monitor`, comando `dnf-monitor` em `/usr/sbin`, unit em
  `/usr/lib/systemd/system` com hardening; `.conf` com modo `0600`.
- Build reproduzível (`build.sh`), testes automatizados, CI e documentação completa (`docs/`).

## 1.0-6
- Solver nativo do DNF (`upgrade_all()` + `resolve()`).

## 1.0-5
- Correção de pacotes duplicados nas notificações.
