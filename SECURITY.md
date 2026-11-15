# Política de segurança

Se você encontrar uma vulnerabilidade, **não abra uma issue pública**. Use *Security → Report a vulnerability*
neste repositório (relato privado) ou escreva para henrique@henrique.tec.br com os passos para reproduzir.

Boas práticas ao usar o DNF Monitor:

* mantenha `/etc/dnf-monitor/dnf-monitor.conf` com modo `0600`;
* use TLS no SMTP (`SMTP_SECURITY=auto`, `ssl` ou `starttls`);
* revogue o token no BotFather caso ele vaze;
* prefira uma conta SMTP dedicada, com permissão apenas de envio.
