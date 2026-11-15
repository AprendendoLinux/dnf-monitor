"""Telegram (HTTP :8081) + SMTP (:2525) falsos para o teste de integração em container."""
import email
import email.header
import http.server
import json
import os
import socketserver
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import test_dnf_monitor as t  # noqa: E402

tg = http.server.HTTPServer(("127.0.0.1", 8081), t.TelegramHandler)
tg.requests, tg.responses = [], []
socketserver.ThreadingTCPServer.allow_reuse_address = True
smtp = socketserver.ThreadingTCPServer(("127.0.0.1", 2525), t.SMTPHandler)
smtp.daemon_threads = True
smtp.auth, smtp.rcpts, smtp.messages = [], [], []
for srv in (tg, smtp):
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def parse(raw):
    msg = email.message_from_string(raw)
    subject = "".join(p.decode(c or "utf-8") if isinstance(p, bytes) else p
                      for p, c in email.header.decode_header(msg["Subject"]))
    parts = {part.get_content_type(): part.get_payload(decode=True).decode("utf-8")
             for part in msg.walk() if not part.is_multipart()}
    return {"subject": subject, "to": msg["To"], "date": msg["Date"], "message_id": msg["Message-ID"], **parts}


while True:
    data = {"telegram": [{"path": p, "body": b} for p, b in tg.requests],
            "email": [parse(m) for m in smtp.messages], "auth": smtp.auth}
    with open("/tmp/mock.json.tmp", "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    os.replace("/tmp/mock.json.tmp", "/tmp/mock.json")
    time.sleep(0.2)
