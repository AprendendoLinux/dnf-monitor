# Contribuindo

Obrigado por ajudar! Contribuições de código, testes e documentação são bem-vindas.

## Ambiente

Só é preciso Python 3 e (para os testes de integração e para gerar o RPM) Docker ou Podman. Não há dependências de
desenvolvimento. O RPM é gerado dentro de um container Rocky Linux 9 se o `rpmbuild` não estiver instalado.

```bash
python3 -m unittest discover -s tests -v   # unitários (segundos)
./build.sh                                  # gera dist/dnf-monitor-<versão>-1.noarch.rpm
tests/docker/run-matrix.sh [imagem...]      # instala o .rpm em containers EL e exercita o fluxo com o DNF real
tests/docker/systemd-test.sh [imagem...]    # idem, com systemd real (container --privileged)
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
src/                 código do daemon (um único arquivo)
packaging/           spec, unit, conf, logrotate e man page do RPM
build.sh             monta e gera o .rpm
tests/               unitários e testes de integração (tests/docker/)
docs/                documentação
.github/workflows/   CI e release
```

## Como lançar uma versão

1. Atualize `__version__` em `src/dnf_monitor.py`, o `CHANGELOG.md` e o `%changelog` de `packaging/dnf-monitor.spec`.
2. Abra o PR e espere o CI ficar verde; faça o merge.
3. Crie a tag: `git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`. O workflow *Release* gera o `.rpm` e o
   `SHA256SUMS` e publica em *Releases*.

## Commits e PRs

Mensagens objetivas em português ou inglês, explicando o *porquê*. PRs pequenos e focados são revisados mais rápido.
