# Contribuindo

Obrigado por ajudar! Contribuições de código, testes e documentação são bem-vindas.

## Duas edições, um repositório

| | Clássico | Plus |
|---|---|---|
| Código | `src/` | `plus/src/` |
| Empacotamento | `packaging/`, `build.sh` | `plus/packaging/`, `plus/build.sh` |
| Testes | `tests/` | `plus/tests/` |
| Documentação | `docs/`, `README.md` | `plus/docs/`, `plus/README.md` |
| Changelog | `CHANGELOG.md` | `plus/CHANGELOG.md` |
| Tag de release | `vX.Y.Z` | `plus-vX.Y.Z` |
| Pacote | `dnf-monitor` | `dnf-monitor-plus` |

Cada edição é **autocontida** (um único arquivo Python, sem módulo compartilhado). O custo disso é que **uma correção
no núcleo (coletor do DNF, classificação kernel/segurança, envio por Telegram/SMTP, configuração) precisa ser portada
para as duas**. Por isso:

* Ao abrir um PR que corrige algo no núcleo, diga no PR se vale também para a **outra edição** e, se sim, faça o port
  no mesmo PR ou abra uma issue com o rótulo `also-plus` / `also-classic`.
* Funcionalidades **só do Plus** (relatório ao cliente, histórico, detecção imediata, e-mails novos) ficam em `plus/` e
  não entram no clássico, salvo decisão explícita.
* Os guias de **Telegram** e **SMTP** (`docs/telegram.md`, `docs/email.md`) são compartilhados: o Plus aponta para eles.
* Os dois pacotes **não coexistem** (o Plus declara `Obsoletes: dnf-monitor`): o Plus substitui o clássico e migra a
  configuração (`plus/docs/migracao.md`). Mudanças em caminhos, nomes de serviço ou nos scriptlets do Plus exigem rodar
  também a migração nos testes de integração.
* O projeto irmão para Debian/Ubuntu, [apt-monitor-daemon](https://github.com/AprendendoLinux/apt-monitor-daemon),
  tem a mesma estrutura e a mesma edição Plus: correções de núcleo que não sejam específicas do DNF valem lá também.

## Ambiente

Só é preciso Python 3 e (para os testes de integração e para gerar o RPM) Docker ou Podman. Não há dependências de
desenvolvimento. O RPM é gerado dentro de um container Rocky Linux 9 se o `rpmbuild` não estiver instalado.

```bash
# Clássico
python3 -m unittest discover -s tests -v   # unitários (segundos)
./build.sh                                  # gera dist/dnf-monitor-<versão>-1.noarch.rpm
tests/docker/run-matrix.sh [imagem...]      # instala o .rpm em containers EL e exercita o fluxo com o DNF real
tests/docker/systemd-test.sh [imagem...]    # idem, com systemd real (container --privileged)

# Plus
python3 -m unittest discover -s plus/tests -v   # unitários
plus/build.sh                                    # gera plus/dist/dnf-monitor-plus-<versão>-1.noarch.rpm
plus/tests/docker/run-matrix.sh [imagem...]      # idem, incluindo a migração clássico -> Plus e o relatório
plus/tests/docker/systemd-test.sh [imagem...]    # idem, com systemd real (detecção imediata, relatório no horário)
plus/tests/render/run.sh                         # renderiza os e-mails num Chromium (contêiner) e salva capturas
```

Imagens da matriz: `rockylinux:8 rockylinux:9 almalinux:10 oraclelinux:9`.

## Regras do projeto

* **Compatibilidade:** o código precisa rodar em Python 3.6+ (EL8): sem `dataclasses`, sem `:=`, sem
  `subprocess.run(capture_output=...)`/`text=True`.
* **Sem dependências novas:** além de `python3-dnf`, só biblioteca padrão.
* **Segredos:** nunca logue token, senha ou URLs completas da API do Telegram.
* **Scriptlets do RPM** são `sh` POSIX e não dependem de macros do systemd (que mudam entre EL8/9/10).
* **Testes:** todo bug corrigido ou funcionalidade nova vem com teste. O CI roda unitários, lint, a matriz de
  distros e o teste com systemd.

## Estrutura

```
src/  packaging/  build.sh  tests/  docs/     DNF Monitor clássico
plus/                                         DNF Monitor Plus
  src/  packaging/  build.sh  tests/  docs/
  tests/render/                               capturas dos e-mails (Chromium em contêiner)
.github/workflows/                            CI e releases (um workflow de release por edição)
```

## Como lançar uma versão

**Clássico**

1. Atualize `__version__` em `src/dnf_monitor.py`, o `CHANGELOG.md` e o `%changelog` de `packaging/dnf-monitor.spec`.
2. Abra o PR e espere o CI ficar verde; faça o merge.
3. Crie a tag: `git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`. O workflow *Release* gera o `.rpm` e o
   `SHA256SUMS` e publica em *Releases* (sem tomar o selo "Latest", que é do Plus).

**Plus**

1. Atualize `__version__` em `plus/src/dnf_monitor.py`, o `plus/CHANGELOG.md` e o `%changelog` de
   `plus/packaging/dnf-monitor-plus.spec`.
2. Abra o PR e espere o CI ficar verde; faça o merge.
3. Crie a tag: `git tag -a plus-vX.Y.Z -m "..." && git push origin plus-vX.Y.Z`. O workflow *Release Plus* confere que a
   tag bate com a versão do código, gera o `dnf-monitor-plus-*.rpm` e o `SHA256SUMS` e publica em *Releases* como **Latest**.

## Commits e PRs

Mensagens objetivas em português ou inglês, explicando o *porquê*. PRs pequenos e focados são revisados mais rápido.
