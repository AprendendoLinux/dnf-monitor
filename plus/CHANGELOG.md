# Changelog — DNF Monitor Plus

O Plus tem **versões próprias**, independentes do DNF Monitor clássico (`CHANGELOG.md` na raiz do repositório).
Tags de release: `plus-vX.Y.Z`.

## 1.0.0

Primeira versão da edição **Plus**. Ela parte do núcleo do DNF Monitor clássico 1.1.1 (solver do DNF, alertas de
atualização, kernel, segurança, reinício pendente, estado persistente, hardening) e acrescenta:

### Para os administradores
- **Alerta imediato**: além do intervalo (`CHECK_INTERVAL_HOURS`), o daemon observa os metadados dos repositórios
  (`/var/cache/dnf/*/repodata/repomd.xml`) e o banco do rpm (`rpmdb.sqlite`/`Packages`) e refaz a checagem em cerca de
  1 minuto quando o DNF baixa metadados novos ou instala pacotes, sem baixar os metadados de novo. A atualização que o
  próprio daemon faz não dispara essa checagem.
- **E-mails de alerta novos**: cabeçalho por gravidade (crítico, segurança, comum), cartões de totais, selos de
  **Kernel** e **Segurança** por pacote, comando sugerido (`sudo dnf upgrade --refresh`), horário da detecção e layout
  para celular. Sem imagens externas.

### Para o cliente (novo)
- **Relatório diário** por um segundo bot do Telegram (`[ClientTelegram]`) e/ou por e-mail HTML (`[ClientEmail]`): quantas
  vezes o administrador atualizou o servidor e o total de pacotes, com sucesso e com falha.
- Sai **uma vez por dia**, a partir de `REPORT_TIME` (padrão 22:30) no fuso `REPORT_TIMEZONE` (padrão
  `America/Sao_Paulo`). Dia sem novidade não gera mensagem (`SEND_EMPTY=true` muda isso). Cada canal é controlado à
  parte; falhas são retentadas sem reenviar aos canais que já receberam.
- **Pendências antigas**: se algum pacote espera instalação há **mais de** `PENDING_ALERT_DAYS` dias (padrão 3), o
  relatório lista **todos** os pendentes, com a idade de cada um, e sugere entrar em contato com o administrador (sem
  citar nomes). Vale também para dias sem atualização e se repete diariamente até a instalação.
- `ADMIN_NAME`: "O administrador Fulano atualizou o servidor…" (em branco: "A equipe técnica").
- O cliente **nunca** recebe IPs nem alertas dos administradores. `PACKAGE_LIST=false` oculta a tabela de pacotes.

### Histórico
- O daemon lê o **histórico de transações do próprio DNF** (`/var/lib/dnf/history.sqlite`, somente leitura) e registra
  cada transação com **atualização de pacotes**. O kernel novo conta (o DNF o instala em paralelo, como *installonly*);
  instalar/remover pacotes comuns não conta.
- Transações **sem linha de comando** (`dnf-automatic`, PackageKit e outras ferramentas que usam a API do DNF) são
  ignoradas.
- **Sucesso ou falha por pacote**, vindo do estado que o próprio DNF guarda na transação.
- Banco SQLite em `/var/lib/dnf-monitor-plus/history.db` (`0600`); só registra o que acontece **depois** da criação do
  banco.

### Comandos e empacotamento
- `--report` (envia agora; com `--dry-run`, só pré-visualiza). `--test-notify` também testa os canais do cliente e
  `--check-config` mostra os canais de administrador e de cliente.
- Pacote **`dnf-monitor-plus`**, com caminhos próprios (`/etc/dnf-monitor-plus`, `/var/lib/dnf-monitor-plus`,
  `dnf-monitor-plus.service`) e o atalho `dnf-monitor`. RPM `noarch`, para EL8, EL9 e EL10.
- **Substitui o `dnf-monitor` clássico** (`Obsoletes`) e **migra sozinho** a configuração e o estado dele (veja
  `docs/migracao.md`).
- Depende de `tzdata` (validação do fuso do relatório).
- Documentação completa em `plus/docs/`: referência de configuração, guia de uso, configuração do cliente, regras do
  relatório, galeria de e-mails, migração, arquitetura e solução de problemas.
- E-mails conferidos num navegador de verdade (Chromium em contêiner), no desktop e no celular
  (`plus/tests/render/`).
