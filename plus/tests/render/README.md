# Conferência visual dos e-mails

`./run.sh` gera os e-mails de exemplo (`gen.py`), abre cada um num Chromium dentro de um contêiner
(`Dockerfile`, Playwright) e salva as capturas em `out/` (`*.desktop.png` e `*.mobile.png`). Rode depois de
qualquer mudança no visual dos e-mails. A pasta `out/` não é versionada.
