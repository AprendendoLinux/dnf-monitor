# Migração: do DNF Monitor clássico para o Plus

O **Plus substitui** o clássico no servidor: instala-se o pacote `dnf-monitor-plus`, e o `dnf-monitor` é removido
automaticamente (o Plus declara `Obsoletes: dnf-monitor`). A sua **configuração e o estado são migrados sozinhos**,
então os alertas continuam como estavam, sem repetir o que já foi avisado.

## Índice

- [Resumo](#resumo)
- [Passo a passo](#passo-a-passo)
- [O que acontece por baixo](#o-que-acontece-por-baixo)
- [Como conferir](#como-conferir)
- [O que mudou entre as edições](#o-que-mudou-entre-as-edições)
- [Voltar para o clássico](#voltar-para-o-clássico)
- [Limpar o que sobrou do clássico](#limpar-o-que-sobrou-do-clássico)
- [Perguntas frequentes](#perguntas-frequentes)

---

## Resumo

| | |
|---|---|
| Comando | `sudo dnf install ./dnf-monitor-plus-1.0.0-1.noarch.rpm` |
| O clássico | É **removido** pelo próprio `dnf` (o Plus o substitui via `Obsoletes`). |
| Configuração | **Copiada** de `/etc/dnf-monitor/dnf-monitor.conf` para `/etc/dnf-monitor-plus/dnf-monitor.conf`. |
| Estado dos alertas | **Copiado** de `/var/lib/dnf-monitor/state.json`: nada é reenviado. |
| Serviço | `dnf-monitor.service` é parado e desabilitado; `dnf-monitor-plus.service` é habilitado e iniciado. |
| Tempo fora do ar | Segundos. |
| Suporte | RHEL, Rocky, AlmaLinux, Oracle Linux e CentOS Stream 8, 9 e 10 |

## Passo a passo

```bash
# 1. baixe o pacote do Plus (página de Releases, tags "plus-v…")
wget https://github.com/AprendendoLinux/dnf-monitor/releases/download/plus-v1.0.0/dnf-monitor-plus-1.0.0-1.noarch.rpm

# 2. instale (o dnf remove o dnf-monitor clássico e instala o Plus)
sudo dnf install ./dnf-monitor-plus-1.0.0-1.noarch.rpm
```

O `dnf` mostra que vai **remover `dnf-monitor`** (como "Removing/Obsoleting"): é o esperado. Durante a instalação você verá:

```text
dnf-monitor-plus: configuração do dnf-monitor migrada para /etc/dnf-monitor-plus/dnf-monitor.conf.
dnf-monitor-plus: estado anterior migrado (alertas já enviados não se repetem).
```

Depois, confira (veja [Como conferir](#como-conferir)) e, se quiser, ative o relatório do cliente
([guia do cliente](cliente.md)).

## O que acontece por baixo

1. O `rpm` instala o Plus **antes** de remover o clássico. Por isso, no momento em que o `%post` do Plus roda, a
   configuração e o estado do clássico **ainda existem** e podem ser migrados.
2. O Plus usa **caminhos próprios**:

   | | Clássico | Plus |
   |---|---|---|
   | Pacote | `dnf-monitor` | `dnf-monitor-plus` |
   | Serviço | `dnf-monitor.service` | `dnf-monitor-plus.service` |
   | Configuração | `/etc/dnf-monitor/dnf-monitor.conf` | `/etc/dnf-monitor-plus/dnf-monitor.conf` |
   | Estado | `/var/lib/dnf-monitor/state.json` | `/var/lib/dnf-monitor-plus/state.json` |
   | Histórico | — | `/var/lib/dnf-monitor-plus/history.db` |
   | Log | `/var/log/dnf-monitor.log` | `/var/log/dnf-monitor-plus.log` |
   | Comando | `dnf-monitor` | `dnf-monitor-plus` (e o atalho `dnf-monitor`) |
   | Manual | `man dnf-monitor` | `man dnf-monitor-plus` |

3. O `%post` do Plus, **apenas na primeira instalação** e **só se** o `.conf` do Plus ainda for o modelo de exemplo:
   - copia o seu `.conf` do clássico para o do Plus (modo `0600`) e **acrescenta**, comentadas, as seções novas do
     Plus (`[Report]`, `[ClientTelegram]`, `[ClientEmail]`);
   - copia o `state.json` (se existir), para não reenviar alertas já avisados;
   - habilita e inicia o serviço do Plus.
4. Em seguida o `rpm` remove o clássico: o `%preun` dele **para e desabilita** o serviço antigo, e o `%postun` apaga o
   `/var/lib/dnf-monitor` e o log antigo. A configuração antiga, **se você a tinha alterado**, é guardada pelo `rpm`
   como **`/etc/dnf-monitor/dnf-monitor.conf.rpmsave`**.

> **Por que caminhos separados?** Para cada pacote só mexer nos seus arquivos. Assim, remover ou limpar o clássico nunca
> apaga o histórico nem o estado do Plus.

## Como conferir

```bash
systemctl status dnf-monitor-plus --no-pager | head -5   # active (running)
systemctl status dnf-monitor 2>&1 | head -2              # unit não existe mais
dnf-monitor-plus --version                               # dnf-monitor-plus 1.0.0
sudo dnf-monitor-plus --check-config                     # Configuração OK + canais ativos
sudo dnf-monitor-plus --test-notify                      # recebe a mensagem de teste?
rpm -q dnf-monitor-plus dnf-monitor                      # o segundo deve dizer "não está instalado"
ls -l /var/lib/dnf-monitor-plus/                         # state.json (e history.db depois que o serviço rodar)
```

Se o `.conf` migrado ainda estiver com valores de exemplo, o serviço não inicia e o instalador explica o que falta.

## O que mudou entre as edições

| | Clássico | **Plus** |
|---|---|---|
| Alertas aos administradores (Telegram e e-mail) | ✅ | ✅ |
| **Alerta imediato** quando o DNF/rpm muda (≈ 1 min), sem esperar o intervalo | — | ✅ |
| **E-mails** dos alertas | Simples | Novos: cabeçalho por gravidade, cartões, selos, comando sugerido, celular |
| **Relatório diário ao cliente** (Telegram e/ou e-mail) | — | ✅ |
| **Aviso de pendência antiga** (mais de N dias) ao cliente | — | ✅ |
| Histórico do que foi atualizado, com sucesso/falha por pacote | — | ✅ (do histórico do DNF + SQLite) |
| `--report` | — | ✅ |
| Dependências | `python3-dnf` | `python3-dnf` + `tzdata` |

As chaves de `[Telegram]`, `[Monitor]` e `[Email]` **são as mesmas**; o Plus apenas acrescenta seções e mantém os
mesmos padrões. Quem não configurar o cliente tem o comportamento de alertas de sempre, com e-mails mais bonitos
e a detecção mais rápida.

## Voltar para o clássico

Possível a qualquer momento, **removendo o Plus primeiro**:

```bash
sudo dnf remove dnf-monitor-plus
wget https://github.com/AprendendoLinux/dnf-monitor/releases/download/v1.1.1/dnf-monitor-1.1.1-1.noarch.rpm
sudo dnf install ./dnf-monitor-1.1.1-1.noarch.rpm
```

> O Plus declara que **obsoleta** o clássico, então o `dnf` não aceita instalar o `dnf-monitor` enquanto o Plus
> estiver instalado. Remova o Plus antes.

- A configuração do clássico guardada na migração está em `/etc/dnf-monitor/dnf-monitor.conf.rpmsave`. Para reutilizá-la:

  ```bash
  sudo cp -p /etc/dnf-monitor/dnf-monitor.conf.rpmsave /etc/dnf-monitor/dnf-monitor.conf
  ```

  Se você quiser levar também o que mudou depois no `.conf` do Plus, copie dele
  (`/etc/dnf-monitor-plus/dnf-monitor.conf`, **antes** de remover o Plus): o clássico simplesmente **ignora** as seções
  que não conhece (`[Report]`, `[ClientTelegram]`, `[ClientEmail]`).
- Ao remover o Plus, o `rpm` apaga o estado, o histórico e o log dele. Faça uma cópia do
  `/var/lib/dnf-monitor-plus/history.db` antes, se quiser guardá-lo.

## Limpar o que sobrou do clássico

Depois de migrar, restam apenas arquivos guardados pelo `rpm`. Quando você confirmar que o Plus funciona, pode apagar:

```bash
sudo rm -rf /etc/dnf-monitor            # o .rpmsave da configuração antiga (tem o token do bot)
```

Isso **não** toca nos arquivos do Plus.

## Perguntas frequentes

**Posso ter os dois instalados na mesma máquina?** Não: o Plus obsoleta o clássico, e os dois mandariam cada um o seu
alerta. Em **máquinas diferentes** você pode usar edições diferentes, sem problema.

**Vou receber de novo o alerta que já recebi?** Não: o estado foi copiado. Se não houver `state.json` para migrar,
o Plus avisa as pendências atuais uma vez, como numa instalação nova.

**E se eu já tinha o `/etc/dnf-monitor-plus/dnf-monitor.conf` configurado?** A migração só copia o do clássico quando o
do Plus ainda é o modelo de exemplo; um `.conf` do Plus já preenchido nunca é sobrescrito.

**Preciso mudar scripts que chamam `dnf-monitor`?** Não: o atalho `/usr/sbin/dnf-monitor` continua existindo e aponta
para o Plus. Referências ao serviço `dnf-monitor.service` (por exemplo, em monitoramento) devem passar a usar
`dnf-monitor-plus.service`.

**Vem da versão 1.0 do clássico (a que ficava em `/opt`)?** Funciona do mesmo jeito: o `.conf` e o estado ficavam nos
mesmos caminhos de hoje, e o Plus os migra. O `/opt/dnf-monitor` é limpo pela remoção do pacote antigo.
