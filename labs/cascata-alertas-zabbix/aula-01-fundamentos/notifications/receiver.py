#!/usr/bin/env python3
"""Recebe notificações reais do Zabbix somente dentro deste laboratório."""
import datetime as dt
import hmac
import html
import json
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = Path(os.environ.get("LAB_NOTIFY_DATA", "/data"))
TOKEN = os.environ.get("LAB_NOTIFY_TOKEN", "")
LOCK = threading.Lock()


def entries():
    path = DATA / "notifications.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def page(rows, mode):
    problems = sum(row.get("event_value") == "1" for row in rows)
    recoveries = sum(row.get("event_value") == "0" for row in rows)
    body = "".join(
        '<tr><td>{time}</td><td><span class="{cls}">{kind}</span></td>'
        '<td>{host}</td><td>{problem}</td><td>{event}</td></tr>'.format(
            time=html.escape(row.get("received_local", "")),
            cls="bad" if row.get("event_value") == "1" else "good",
            kind="PROBLEMA" if row.get("event_value") == "1" else "RECUPERADO",
            host=html.escape(str(row.get("host", ""))),
            problem=html.escape(str(row.get("problem", ""))),
            event=html.escape(str(row.get("event_id", ""))),
        ) for row in reversed(rows[-200:])
    ) or '<tr><td colspan="5" class="empty">Aguardando notificações do Zabbix.</td></tr>'
    return ("""<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="2"><title>NOTO TI · Alertas do laboratório</title>
<style>*{box-sizing:border-box}body{margin:0;background:#0b1220;color:#e7eef6;font:16px system-ui,sans-serif}
main{max-width:1380px;margin:auto;padding:48px 32px}header{display:flex;justify-content:space-between;align-items:center}
.brand{font-size:22px;font-weight:800;letter-spacing:2px;color:#16b89a}.tag{padding:10px 16px;border:1px solid #334155;border-radius:30px;color:#c7d6e5}
h1{font-size:42px;margin:30px 0 8px}p{color:#9fb1c5;line-height:1.6}.cards{display:flex;gap:18px;margin:30px 0}
.card{background:#111f30;border:1px solid #26394e;border-radius:14px;padding:22px 30px;min-width:220px}
.card strong{display:block;font-size:48px;margin-top:4px}.card small{color:#9fb1c5}
table{width:100%;border-collapse:collapse;background:#101c2b;border-radius:14px;overflow:hidden;font-size:15px}
th,td{text-align:left;padding:17px 16px;border-bottom:1px solid #203147}th{color:#9fb1c5;font-weight:500}
.bad{color:#fb7185;font-size:12px;font-weight:800}.good{color:#16b89a;font-size:12px;font-weight:800}
.empty{text-align:center;color:#9fb1c5;padding:60px}footer{margin-top:22px;color:#7b91a9;font-size:13px}
@media(max-width:700px){main{padding:24px 12px}h1{font-size:28px}.cards{flex-wrap:wrap}.card{min-width:150px}table{font-size:12px}td,th{padding:12px 7px}}</style>
<main><header><span class="brand">NOTO TI</span><span class="tag">MODE_LABEL</span></header>
<h1>Uma falha. Quantas notificações?</h1><p>Laboratório ISP · Core / POP / OLT · Mensagens enviadas pela ação do Zabbix.</p>
<div class="cards"><div class="card"><small>NOTIFICAÇÕES DE PROBLEMA</small><strong>PROBLEM_COUNT</strong></div>
<div class="card"><small>RECUPERAÇÕES</small><strong>RECOVERY_COUNT</strong></div></div>
<table><thead><tr><th>Recebido em Brasília</th><th>Tipo</th><th>Equipamento</th><th>Evento</th><th>ID</th></tr></thead>
<tbody>TABLE_ROWS</tbody></table><footer>Atualiza a cada 2 segundos. Os contadores representam as mensagens recebidas desde a última limpeza.</footer></main></html>""")\
        .replace("MODE_LABEL", html.escape(mode))\
        .replace("PROBLEM_COUNT", str(problems))\
        .replace("RECOVERY_COUNT", str(recoveries))\
        .replace("TABLE_ROWS", body)


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, payload, content_type="application/json; charset=utf-8"):
        data = payload.encode() if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"ok": True})
        if self.path not in ("/", "/notifications"):
            return self.reply(404, {"error": "not found"})
        with LOCK:
            rows = entries()
            mode_file = DATA / "mode.txt"
            mode = mode_file.read_text() if mode_file.exists() else "Preparando laboratório"
        if self.path == "/notifications":
            return self.reply(200, rows)
        return self.reply(200, page(rows, mode), "text/html; charset=utf-8")

    def do_POST(self):
        if not TOKEN or not hmac.compare_digest(self.headers.get("X-Lab-Token", ""), TOKEN):
            return self.reply(403, {"error": "forbidden"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 32768:
                return self.reply(413, {"error": "body too large"})
            value = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(value, dict):
                raise ValueError("object required")
        except (ValueError, json.JSONDecodeError):
            return self.reply(400, {"error": "invalid JSON"})
        with LOCK:
            if self.path == "/clear":
                (DATA / "notifications.jsonl").write_text("")
            elif self.path == "/mode":
                (DATA / "mode.txt").write_text(str(value.get("mode", ""))[:100])
            elif self.path == "/notify":
                required = ("event_id", "event_value", "host", "problem")
                if any(key not in value for key in required) or str(value["event_value"]) not in ("0", "1"):
                    return self.reply(400, {"error": "invalid notification"})
                row = {key: str(value[key]) for key in required}
                row["received_local"] = dt.datetime.now(dt.timezone(dt.timedelta(hours=-3))).strftime("%H:%M:%S")
                row["received_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
                with (DATA / "notifications.jsonl").open("a") as stream:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(json.dumps(row, ensure_ascii=False), flush=True)
            else:
                return self.reply(404, {"error": "not found"})
        return self.reply(200, {"ok": True})

    def log_message(self, fmt, *args):
        if self.path != "/health":
            super().log_message(fmt, *args)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("LAB_NOTIFY_TOKEN ausente")
    DATA.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
