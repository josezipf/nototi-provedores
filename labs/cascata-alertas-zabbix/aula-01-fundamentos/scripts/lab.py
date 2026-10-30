#!/usr/bin/env python3
"""Laboratório ISP. Requer somente Python padrão e Docker Compose no host."""
import argparse
import contextlib
import datetime as dt
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
STATE_PATH = ROOT / ".lab-state.json"
PROJECT = "nototi-cascata"
GROUP = "LAB - NOTO TI - Cascata de alertas"
ACTION = "NOTO TI - Notificar laboratorio ISP"
MEDIA = "NOTO TI - Notificacoes locais"
MAP_NAME = "NOTO TI - ISP - Dependencias em 3 niveis"
ITEM_KEY = "icmpping[,3,200,,500]"
NODES = [
    {"service": "core", "host": "CORE-ISP", "suffix": "10.2", "level": 1, "samples": 2, "parent": None, "pop": "core"},
    {"service": "pop-centro", "host": "SW-POP-CENTRO", "suffix": "20.11", "level": 2, "samples": 6, "parent": "CORE-ISP", "pop": "centro"},
    {"service": "pop-norte", "host": "SW-POP-NORTE", "suffix": "20.12", "level": 2, "samples": 6, "parent": "CORE-ISP", "pop": "norte"},
    {"service": "olt-centro-01", "host": "OLT-CENTRO-01", "suffix": "30.11", "level": 3, "samples": 10, "parent": "SW-POP-CENTRO", "pop": "centro"},
    {"service": "olt-centro-02", "host": "OLT-CENTRO-02", "suffix": "30.12", "level": 3, "samples": 10, "parent": "SW-POP-CENTRO", "pop": "centro"},
    {"service": "olt-norte-01", "host": "OLT-NORTE-01", "suffix": "40.11", "level": 3, "samples": 10, "parent": "SW-POP-NORTE", "pop": "norte"},
    {"service": "olt-norte-02", "host": "OLT-NORTE-02", "suffix": "40.12", "level": 3, "samples": 10, "parent": "SW-POP-NORTE", "pop": "norte"},
]
SERVICES = [node["service"] for node in NODES]


class LabError(RuntimeError):
    pass


def say(message):
    print(message, flush=True)


def run(args, capture=False, check=True):
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=capture)
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise LabError(f"Falhou: {' '.join(args[:4])}. {detail}")
    return result


def compose(*args, capture=False, check=True):
    return run(["docker", "compose", "--env-file", str(ENV_PATH), "-p", PROJECT,
                "-f", str(ROOT / "compose.yaml"), *args], capture=capture, check=check)


def require_docker():
    if not shutil.which("docker"):
        raise LabError("Docker não encontrado. Instale Docker Engine e o plugin Docker Compose no Linux.")
    if run(["docker", "info"], capture=True, check=False).returncode:
        raise LabError("Sem acesso ao Docker. Inicie o serviço e use um usuário com acesso ao daemon; se necessário, execute sudo ./lab.sh up.")
    if run(["docker", "compose", "version"], capture=True, check=False).returncode:
        raise LabError("O plugin Docker Compose não está instalado. É necessário o comando docker compose.")


def read_env():
    if not ENV_PATH.exists():
        raise LabError("Laboratório ainda não configurado. Execute ./lab.sh up.")
    return dict(line.split("=", 1) for line in ENV_PATH.read_text().splitlines()
                if line and not line.startswith("#") and "=" in line)


def free_port(preferred, exclude=()):
    for port in range(preferred, preferred + 100):
        if port in exclude:
            continue
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                pass
    raise LabError("Não encontrei uma porta local livre para o laboratório.")


def network_ranges(prefix):
    return [ipaddress.ip_network(f"{prefix}.{part}.0/24") for part in (0, 10, 20, 30, 40)]


def choose_prefix(occupied, requested=None):
    candidates = [requested] if requested else [f"10.{x}" for x in range(231, 250)] + [f"172.{x}" for x in range(24, 30)]
    for prefix in candidates:
        if not re.fullmatch(r"\d{1,3}\.\d{1,3}", prefix or ""):
            raise LabError("LAB_NET_PREFIX deve ter dois octetos, por exemplo 10.231.")
        try:
            networks = network_ranges(prefix)
        except ValueError as error:
            raise LabError("LAB_NET_PREFIX inválido.") from error
        if not all(net.is_private and not net.is_loopback for net in networks):
            raise LabError("Use endereçamento IPv4 privado para o laboratório.")
        if not any(net.overlaps(other) for net in networks for other in occupied if other.version == 4):
            return prefix
    raise LabError("Redes do laboratório conflitam com rotas ou redes existentes. Defina LAB_NET_PREFIX com dois octetos privados livres e execute novamente.")


def initialize():
    if ENV_PATH.exists():
        return read_env()
    existing = run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}"], capture=True).stdout.strip()
    existing_volumes = run(["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}"], capture=True).stdout.strip()
    if existing or existing_volumes:
        raise LabError("Já existe um projeto nototi-cascata sem o .env nesta pasta. Recupere a pasta original para preservar as credenciais.")
    occupied = []
    ids = run(["docker", "network", "ls", "-q"], capture=True).stdout.split()
    if ids:
        for network in json.loads(run(["docker", "network", "inspect", *ids], capture=True).stdout):
            for conf in network.get("IPAM", {}).get("Config") or []:
                if conf.get("Subnet"):
                    occupied.append(ipaddress.ip_network(conf["Subnet"], strict=False))
    if shutil.which("ip"):
        result = run(["ip", "-j", "-4", "route", "show", "table", "all"], capture=True, check=False)
        if result.returncode == 0:
            for route in json.loads(result.stdout):
                if route.get("dst", "default") != "default":
                    occupied.append(ipaddress.ip_network(route["dst"], strict=False))
    prefix = choose_prefix(occupied, os.environ.get("LAB_NET_PREFIX"))
    published = run(["docker", "ps", "--format", "{{.Ports}}"], capture=True).stdout
    reserved_ports = {int(port) for port in re.findall(r":(\d+)->", published)}
    web_port = free_port(8188, reserved_ports)
    notify_port = free_port(8189, reserved_ports | {web_port})
    env = {
        "LAB_NET_PREFIX": prefix, "LAB_WEB_PORT": str(web_port), "LAB_NOTIFY_PORT": str(notify_port),
        "LAB_DB_PASSWORD": secrets.token_hex(24), "LAB_ADMIN_PASSWORD": "Noto7!" + secrets.token_urlsafe(18),
        "LAB_NOTIFY_TOKEN": secrets.token_hex(24), "LAB_ZABBIX_TAG": "alpine-7.0-latest",
    }
    fd = os.open(ENV_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write("# Credenciais geradas para esta instalação. Preserve este arquivo.\n")
        stream.write("\n".join(f"{key}={value}" for key, value in env.items()) + "\n")
    say(f"Configuração criada: redes {prefix}.x.0/24; portas {web_port} e {notify_port}.")
    return env


class Api:
    def __init__(self, env):
        self.url = f"http://127.0.0.1:{env['LAB_WEB_PORT']}/api_jsonrpc.php"
        self.env = env
        self.token = None
        self.number = 0

    def call(self, method, params=None, authenticated=True):
        self.number += 1
        headers = {"Content-Type": "application/json-rpc"}
        if authenticated:
            if not self.token:
                raise LabError("Sessão da API não iniciada.")
            headers["Authorization"] = f"Bearer {self.token}"
        body = json.dumps({"jsonrpc": "2.0", "id": self.number, "method": method,
                           "params": {} if params is None else params}).encode()
        request = urllib.request.Request(self.url, body, headers)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                result = json.load(response)
        except (OSError, ValueError) as error:
            raise LabError(f"API indisponível em {self.url}: {type(error).__name__}") from error
        if "error" in result:
            error = result["error"]
            raise LabError(f"API {method}: {error.get('message', '')} {error.get('data', '')}")
        return result["result"]

    def login(self, password=None):
        self.token = self.call("user.login", {"username": "Admin", "password": password or self.env["LAB_ADMIN_PASSWORD"]}, authenticated=False)
        return self

    def close(self):
        if self.token:
            with contextlib.suppress(LabError):
                self.call("user.logout", [])
            self.token = None


def wait_for_api(env):
    deadline = time.monotonic() + 600
    last_note = 0
    api = Api(env)
    while time.monotonic() < deadline:
        try:
            version = api.call("apiinfo.version", authenticated=False)
            if not version.startswith("7.0."):
                raise LabError(f"Este laboratório foi preparado para Zabbix 7.0; encontrado {version}.")
            break
        except LabError as error:
            if "foi preparado" in str(error):
                raise
            if time.monotonic() - last_note >= 15:
                say("Aguardando criação do banco e inicialização do Zabbix...")
                last_note = time.monotonic()
            time.sleep(3)
    else:
        raise LabError("Zabbix não ficou pronto em 10 minutos. Consulte ./lab.sh logs.")
    try:
        api.login()
    except LabError:
        if STATE_PATH.exists():
            raise LabError("A senha do Admin difere do .env. Atualize LAB_ADMIN_PASSWORD com a senha desta instalação.")
        api.login("zabbix")
        admin = api.call("user.get", {"output": ["userid"], "filter": {"username": ["Admin"]}})[0]
        api.call("user.update", {"userid": admin["userid"], "current_passwd": "zabbix", "passwd": env["LAB_ADMIN_PASSWORD"]})
        api.token = None
        api.login()
    say(f"Zabbix {version} disponível.")
    return api


def feed(env, path, data=None):
    request = urllib.request.Request(
        f"http://127.0.0.1:{env['LAB_NOTIFY_PORT']}{path}",
        None if data is None else json.dumps(data).encode(),
        {"Content-Type": "application/json", "X-Lab-Token": env["LAB_NOTIFY_TOKEN"]})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except (OSError, ValueError) as error:
        raise LabError(f"Página de notificações indisponível: {type(error).__name__}") from error


def save_state(state):
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    temporary.replace(STATE_PATH)


def read_state():
    if not STATE_PATH.exists():
        raise LabError("Execute ./lab.sh up para cadastrar os hosts no Zabbix.")
    return json.loads(STATE_PATH.read_text())


def ensure_notifications(api, groupid):
    admin = api.call("user.get", {"output": ["userid"], "filter": {"username": ["Admin"]}})[0]["userid"]
    script = """var p = JSON.parse(value);
var r = new HttpRequest();
r.addHeader('Content-Type: application/json');
r.addHeader('X-Lab-Token: ' + p.token);
r.post(p.url, JSON.stringify({event_id:p.event_id,event_value:p.event_value,host:p.host,problem:p.problem}));
if (r.getStatus() < 200 || r.getStatus() >= 300) { throw 'Notificador HTTP: ' + r.getStatus(); }
return 'OK';"""
    params = {"name": MEDIA, "type": 4, "status": 0, "script": script, "timeout": "10s",
              "parameters": [{"name": key, "value": value} for key, value in {
                  "url": "http://notificacoes:8080/notify", "token": api.env["LAB_NOTIFY_TOKEN"],
                  "event_id": "{EVENT.ID}", "event_value": "{EVENT.VALUE}",
                  "host": "{HOST.HOST}", "problem": "{EVENT.NAME}"}.items()],
              "message_templates": [{"eventsource": 0, "recovery": recovery,
                                     "subject": "{EVENT.NAME}", "message": "{HOST.HOST}: {EVENT.NAME}"}
                                    for recovery in (0, 1)]}
    found = api.call("mediatype.get", {"output": ["mediatypeid"], "filter": {"name": [MEDIA]}})
    if found:
        mediaid = found[0]["mediatypeid"]
        api.call("mediatype.update", {**params, "mediatypeid": mediaid})
    else:
        mediaid = api.call("mediatype.create", params)["mediatypeids"][0]
    api.call("user.update", {"userid": admin, "theme": "dark-theme", "medias": [{
        "mediatypeid": mediaid, "sendto": ["laboratorio-local"], "active": 0,
        "severity": 63, "period": "1-7,00:00-24:00"}]})
    message = {"operationtype": 0, "opmessage": {"default_msg": 1, "mediatypeid": mediaid},
               "opmessage_usr": [{"userid": admin}]}
    action = {"name": ACTION, "eventsource": 0, "status": 1,
              "filter": {"evaltype": 0, "conditions": [{"conditiontype": 0, "operator": 0, "value": groupid}]},
              "operations": [{**message, "esc_step_from": 1, "esc_step_to": 1}],
              "recovery_operations": [message]}
    found = api.call("action.get", {"output": ["actionid"], "filter": {"name": [ACTION]}})
    if found:
        actionid = found[0]["actionid"]
        # eventsource is immutable when updating an existing action.
        action.pop("eventsource")
        api.call("action.update", {**action, "actionid": actionid})
    else:
        actionid = api.call("action.create", action)["actionids"][0]
    return actionid


def ensure_map(api, state):
    found = api.call("map.get", {"output": ["sysmapid"], "filter": {"name": [MAP_NAME]}})
    if found:
        return found[0]["sysmapid"]
    icons = api.call("image.get", {"output": ["imageid", "name"], "filter": {"imagetype": 1}})
    if not icons:
        return None
    def icon(kind):
        return next((img["imageid"] for img in icons if kind in img["name"].lower() and "64" in img["name"]), icons[0]["imageid"])
    positions = [(550, 60), (270, 260), (830, 260), (130, 480), (410, 480), (690, 480), (970, 480)]
    elements, links, ids = [], [], {}
    for i, (node, (x, y)) in enumerate(zip(NODES, positions), 1):
        elementid = str(i)
        ids[node["host"]] = elementid
        elements.append({"selementid": elementid, "elementtype": 0,
                         "elements": [{"hostid": state["hosts"][node["host"]]["hostid"]}],
                         "iconid_off": icon("router" if node["level"] == 1 else "switch"),
                         "label": f"N{node['level']} | {node['host']}", "label_location": 1, "x": x, "y": y})
        if node["parent"]:
            links.append({"selementid1": ids[node["parent"]], "selementid2": elementid, "color": "16B89A", "drawtype": 2})
    return api.call("map.create", {"name": MAP_NAME, "width": 1200, "height": 650,
                                    "label_type": 0, "selements": elements, "links": links})["sysmapids"][0]


def bootstrap(api):
    groups = api.call("hostgroup.get", {"output": ["groupid"], "filter": {"name": [GROUP]}})
    groupid = groups[0]["groupid"] if groups else api.call("hostgroup.create", {"name": GROUP})["groupids"][0]
    for action in api.call("action.get", {"output": ["actionid"], "filter": {"name": [ACTION]}}):
        api.call("action.update", {"actionid": action["actionid"], "status": 1})
    for host in api.call("host.get", {"output": ["hostid"], "filter": {"host": ["Zabbix server"]}}):
        api.call("host.update", {"hostid": host["hostid"], "status": 1})
    state = {"groupid": groupid, "mode": "on", "hosts": {}}
    for node in NODES:
        ip = f"{api.env['LAB_NET_PREFIX']}.{node['suffix']}"
        found = api.call("host.get", {"output": ["hostid"], "filter": {"host": [node["host"]]}})
        if found:
            hostid = found[0]["hostid"]
            api.call("host.update", {"hostid": hostid, "status": 0})
        else:
            hostid = api.call("host.create", {
                "host": node["host"], "groups": [{"groupid": groupid}],
                "interfaces": [{"type": 1, "main": 1, "useip": 1, "ip": ip, "dns": "", "port": "10050"}],
                "tags": [{"tag": "lab", "value": "nototi-cascata"}, {"tag": "pop", "value": node["pop"]}],
            })["hostids"][0]
        interfaces = api.call("hostinterface.get", {"output": ["interfaceid", "type", "main"], "hostids": [hostid]})
        interfaceid = next(row["interfaceid"] for row in interfaces if row["type"] == "1" and row["main"] == "1")
        api.call("hostinterface.update", {"interfaceid": interfaceid, "ip": ip, "useip": 1})
        found = api.call("item.get", {"output": ["itemid"], "hostids": [hostid], "filter": {"key_": ITEM_KEY}})
        item = {"name": "Disponibilidade ICMP", "key_": ITEM_KEY, "type": 3, "value_type": 3,
                "interfaceid": interfaceid, "delay": "5s", "history": "1d", "trends": "0", "status": 0}
        if found:
            itemid = found[0]["itemid"]
            api.call("item.update", {**item, "itemid": itemid})
        else:
            itemid = api.call("item.create", {**item, "hostid": hostid})["itemids"][0]
        description = f"{node['host']}: Equipamento inacessivel"
        found = api.call("trigger.get", {"output": ["triggerid"], "hostids": [hostid], "filter": {"description": [description]}})
        trigger = {"description": description, "expression": f"max(/{node['host']}/{ITEM_KEY},#{node['samples']})=0",
                   "priority": {1: 5, 2: 4, 3: 3}[node["level"]], "status": 1,
                   "tags": [{"tag": "lab", "value": "nototi-cascata"}, {"tag": "level", "value": str(node["level"])}]}
        if found:
            triggerid = found[0]["triggerid"]
            api.call("trigger.update", {**trigger, "triggerid": triggerid})
        else:
            triggerid = api.call("trigger.create", trigger)["triggerids"][0]
        state["hosts"][node["host"]] = {"hostid": hostid, "itemid": itemid, "triggerid": triggerid, "ip": ip}
    state["actionid"] = ensure_notifications(api, groupid)
    state["mapid"] = ensure_map(api, state)
    save_state(state)
    apply_dependencies(api, state, "on")
    warmup(api, state)
    api.call("trigger.update", [{"triggerid": host["triggerid"], "status": 0} for host in state["hosts"].values()])
    reload_cache()
    wait_healthy(api, state)
    api.call("action.update", {"actionid": state["actionid"], "status": 0})
    reload_cache()
    feed(api.env, "/clear", {})
    say("Sete hosts, sete itens ICMP, seis dependências, mapa e ação cadastrados.")
    return state


def reload_cache():
    compose("exec", "-T", "zabbix-server", "zabbix_server", "-R", "config_cache_reload", capture=True)
    # The request is asynchronous; CacheUpdateFrequency is also configured as 5s.
    time.sleep(7)


def active_problems(api, state):
    triggers = api.call("trigger.get", {"output": ["triggerid", "value", "state", "error"],
                                        "triggerids": [h["triggerid"] for h in state["hosts"].values()]})
    reverse = {h["triggerid"]: name for name, h in state["hosts"].items()}
    unknown = [row for row in triggers if str(row.get("state", "0")) != "0"]
    if unknown:
        raise LabError(f"Trigger não suportada: {unknown[0].get('error', '')}")
    return {reverse[row["triggerid"]] for row in triggers if row["value"] == "1"}


def warmup(api, state, timeout=150):
    say("Aguardando dez coletas saudáveis por equipamento (cerca de 50 segundos)...")
    deadline, last_note = time.monotonic() + timeout, 0
    while time.monotonic() < deadline:
        pending = []
        for name, host in state["hosts"].items():
            history = api.call("history.get", {"output": ["value", "clock"], "history": 3,
                                               "itemids": [host["itemid"]], "sortfield": "clock", "sortorder": "DESC", "limit": 10})
            if len(history) < 10 or any(row["value"] != "1" for row in history) or int(history[0]["clock"]) < time.time() - 20:
                pending.append(name)
        if not pending:
            return
        if time.monotonic() - last_note > 15:
            say("Coletando: " + ", ".join(pending))
            last_note = time.monotonic()
        time.sleep(3)
    items = api.call("item.get", {"output": ["name", "state", "error", "lastvalue"], "itemids": [h["itemid"] for h in state["hosts"].values()]})
    raise LabError("ICMP não ficou saudável. Verifique ./lab.sh ping e ./lab.sh logs. Itens: " + json.dumps(items, ensure_ascii=False))


def wait_healthy(api, state):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if not active_problems(api, state):
            return
        time.sleep(2)
    raise LabError("Ainda há problemas ativos depois da recuperação. Consulte ./lab.sh status.")


def apply_dependencies(api, state, mode):
    changes = []
    for node in NODES:
        parent = node["parent"]
        dependencies = [{"triggerid": state["hosts"][parent]["triggerid"]}] if parent and mode == "on" else []
        changes.append({"triggerid": state["hosts"][node["host"]]["triggerid"], "dependencies": dependencies})
    api.call("trigger.update", changes)
    state["mode"] = mode
    save_state(state)
    feed(api.env, "/mode", {"mode": "COM DEPENDÊNCIAS" if mode == "on" else "SEM DEPENDÊNCIAS"})


def restore(api, state):
    compose("start", *SERVICES)
    warmup(api, state)
    wait_healthy(api, state)
    say("Todos os equipamentos recuperados e coletas estabilizadas.")


def set_mode(api, state, mode):
    restore(api, state)
    apply_dependencies(api, state, mode)
    reload_cache()
    feed(api.env, "/clear", {})
    say("Modo: " + ("COM DEPENDÊNCIAS" if mode == "on" else "SEM DEPENDÊNCIAS"))


def ping_all(env, show=True):
    values = {}
    for node in NODES:
        ip = f"{env['LAB_NET_PREFIX']}.{node['suffix']}"
        result = compose("exec", "-T", "noc", "ping", "-c", "1", "-W", "1", ip, capture=True, check=False)
        values[node["host"]] = result.returncode == 0
        if show:
            say(f"{node['host']:<22} {ip:<17} {'RESPONDE' if result.returncode == 0 else 'SEM RESPOSTA'}")
    return values


def status(api, state):
    items = api.call("item.get", {"output": ["itemid", "lastvalue", "lastclock", "state", "error"],
                                  "itemids": [h["itemid"] for h in state["hosts"].values()]})
    by_id = {item["itemid"]: item for item in items}
    problems = active_problems(api, state)
    say(f"Dependências: {state['mode']}")
    say(f"{'EQUIPAMENTO':<23} {'PING':<10} {'TRIGGER':<12} DEPENDE DE")
    for node in NODES:
        item = by_id[state["hosts"][node["host"]]["itemid"]]
        value = "SEM DADOS" if int(item.get("lastclock", 0)) < time.time() - 20 else item["lastvalue"]
        if item.get("state") == "1":
            value = "ERRO"
        parent = (node["parent"] or "—") if state["mode"] == "on" else "—"
        say(f"{node['host']:<23} {value:<10} {('PROBLEM' if node['host'] in problems else 'OK'):<12} {parent}")
    say("PING 0 = sem resposta, mesmo quando a trigger dependente permanece OK.")


def show_urls(env, state=None, credentials=False):
    say(f"Zabbix: http://127.0.0.1:{env['LAB_WEB_PORT']}")
    say(f"Notificações: http://127.0.0.1:{env['LAB_NOTIFY_PORT']}")
    if state and state.get("mapid"):
        say(f"Mapa: http://127.0.0.1:{env['LAB_WEB_PORT']}/zabbix.php?action=map.view&sysmapid={state['mapid']}")
    if credentials:
        say("Usuário: Admin")
        say(f"Senha: {env['LAB_ADMIN_PASSWORD']}")
        say("Credenciais preservadas no arquivo .env desta pasta.")


def test_lab(api, state):
    all_hosts = {node["host"] for node in NODES}
    centro = {"SW-POP-CENTRO", "OLT-CENTRO-01", "OLT-CENTRO-02"}
    cases = [
        ("core sem dependências", "off", ["core"], all_hosts, all_hosts),
        ("core com dependências", "on", ["core"], all_hosts, {"CORE-ISP"}),
        ("POP Centro com dependências", "on", ["pop-centro"], centro, {"SW-POP-CENTRO"}),
        ("OLT isolada", "on", ["olt-centro-01"], {"OLT-CENTRO-01"}, {"OLT-CENTRO-01"}),
        ("falhas independentes nos dois POPs", "on", ["pop-centro", "olt-norte-01"],
         centro | {"OLT-NORTE-01"}, {"SW-POP-CENTRO", "OLT-NORTE-01"}),
    ]
    original_mode = state["mode"]
    report = {"started_at": dt.datetime.now(dt.timezone.utc).isoformat(), "results": [], "passed": False}
    try:
        for label, mode, failed_services, unreachable, expected in cases:
            say(f"\nTESTE: {label}")
            set_mode(api, state, mode)
            start = time.monotonic()
            compose("stop", "-t", "2", *failed_services)
            reached = ping_all(api.env, show=False)
            actual_down = {host for host, responding in reached.items() if not responding}
            if actual_down != unreachable:
                raise LabError(f"Caminho de rede incorreto. Esperado sem resposta: {sorted(unreachable)}; observado: {sorted(actual_down)}")
            # Hold beyond all ten-sample windows, even if the parent problem appears first.
            for second in range(0, 75, 5):
                if second % 15 == 0:
                    say(f"Aguardando avaliação das três camadas: {second}/75 s...")
                time.sleep(5)
            active = active_problems(api, state)
            messages = [row for row in feed(api.env, "/notifications") if row["event_value"] == "1"]
            message_hosts = {row["host"] for row in messages}
            # Allow delivery lag, while preserving the requirement that exactly one message per incident arrived.
            deadline = time.monotonic() + 30
            while message_hosts != expected and time.monotonic() < deadline and not (message_hosts - expected):
                time.sleep(2)
                messages = [row for row in feed(api.env, "/notifications") if row["event_value"] == "1"]
                message_hosts = {row["host"] for row in messages}
            result = {"case": label, "mode": mode, "network_unreachable": sorted(actual_down),
                      "expected_problems": sorted(expected), "active_problems": sorted(active),
                      "notification_hosts": sorted(message_hosts), "notification_count": len(messages),
                      "elapsed_seconds": round(time.monotonic() - start, 1)}
            result["passed"] = active == expected and message_hosts == expected and len(messages) == len(expected)
            report["results"].append(result)
            if not result["passed"]:
                raise LabError("Resultado inesperado: " + json.dumps(result, ensure_ascii=False))
            say(f"PASSOU: {len(messages)} notificação(ões), hosts: {', '.join(sorted(expected))}.")
        report["passed"] = True
    except BaseException as error:
        report["error"] = str(error) or type(error).__name__
        raise
    finally:
        say("Restaurando os equipamentos e o modo original...")
        already_failing = sys.exc_info()[0] is not None
        try:
            set_mode(api, state, original_mode)
            report["cleanup"] = "restored"
        except BaseException as error:
            report["passed"] = False
            report["cleanup_error"] = str(error) or type(error).__name__
            if not already_failing:
                raise
            say("A recuperação automática também falhou. Execute ./lab.sh restore.")
        finally:
            report["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            (ROOT / "test-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    say("Cinco cenários passaram. Relatório salvo em test-report.json.")


def main():
    parser = argparse.ArgumentParser(description="NOTO TI - Laboratório ISP de dependências no Zabbix")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("up", "down", "stop", "restore", "status", "ping", "test", "clear", "urls", "credentials", "logs"):
        sub.add_parser(command)
    mode = sub.add_parser("mode")
    mode.add_argument("value", choices=("on", "off"))
    for command in ("fail", "recover"):
        cmd = sub.add_parser(command)
        cmd.add_argument("equipment", choices=SERVICES)
    args = parser.parse_args()
    require_docker()
    with (ROOT / ".lab.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LabError("Outro comando deste laboratório ainda está em execução.") from error
        env = initialize() if args.command == "up" else read_env()
        if args.command == "up":
            compose("config", "--quiet")
            compose("up", "-d", "--build")
            # Restart equipment intentionally stopped during a prior demonstration.
            compose("start", *SERVICES)
            say("Verificando acesso aos sete equipamentos a partir do NOC...")
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if all(ping_all(env, show=False).values()):
                    break
                time.sleep(2)
            else:
                ping_all(env)
                raise LabError("A topologia não respondeu. Consulte ./lab.sh logs antes de continuar.")
            api = wait_for_api(env)
            try:
                state = bootstrap(api)
                show_urls(env, state, credentials=True)
                say("Pronto. Para mostrar a cascata: ./lab.sh mode off e depois ./lab.sh fail core")
            finally:
                api.close()
            return
        if args.command in ("down", "stop"):
            compose(args.command)
            say("Laboratório parado. Os volumes e as credenciais foram preservados.")
            return
        if args.command == "logs":
            compose("logs", "--tail", "100", "zabbix-server", "zabbix-web", "noc", "core", "pop-centro", "pop-norte", "notificacoes")
            return
        if args.command in ("urls", "credentials"):
            show_urls(env, read_state(), credentials=args.command == "credentials")
            return
        if args.command == "ping":
            ping_all(env)
            return
        if args.command == "clear":
            feed(env, "/clear", {})
            say("Lista de notificações limpa; o histórico de eventos do Zabbix permanece disponível.")
            return
        if args.command in ("fail", "recover"):
            compose(*(["stop", "-t", "2"] if args.command == "fail" else ["start"]), args.equipment)
            say(f"{args.equipment}: {'parado' if args.command == 'fail' else 'iniciado'}. Acompanhe ./lab.sh status e a página de notificações.")
            return
        state = read_state()
        api = Api(env).login()
        try:
            if args.command == "restore":
                restore(api, state)
            elif args.command == "mode":
                set_mode(api, state, args.value)
            elif args.command == "status":
                status(api, state)
            elif args.command == "test":
                test_lab(api, state)
        finally:
            api.close()


if __name__ == "__main__":
    try:
        main()
    except LabError as error:
        print(f"ERRO: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrompido. Use ./lab.sh restore para recuperar os equipamentos.", file=sys.stderr)
        sys.exit(130)
    except OSError as error:
        print(f"ERRO de sistema: {error}", file=sys.stderr)
        sys.exit(1)
