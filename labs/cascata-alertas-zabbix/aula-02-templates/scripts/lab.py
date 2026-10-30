#!/usr/bin/env python3
"""Laboratório ISP - Aula 02 (Templates, Macros por Camada, Dependências Internas e Topológicas)."""
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
PROJECT = "nototi-cascata-aula02"
GROUP = "LAB - NOTO TI - Cascata de alertas (Aula 02)"
TPL_GROUP = "LAB - NOTO TI - Templates ISP"
ACTION = "NOTO TI - Notificar laboratorio ISP (Aula 02)"
MEDIA = "NOTO TI - Notificacoes locais (Aula 02)"
MAP_NAME = "NOTO TI - ISP - Templates e Dependencias em 3 niveis"

TPL_BASE = "TPL - NOTO TI - Base ICMP ISP"
ROLE_TEMPLATES = {
    1: {"host": "TPL - NOTO TI - N1 Core", "samples": "#2", "seconds": 10, "camada": "n1-core", "priority": 5},
    2: {"host": "TPL - NOTO TI - N2 Switch POP", "samples": "#6", "seconds": 30, "camada": "n2-distribuicao", "priority": 4},
    3: {"host": "TPL - NOTO TI - N3 OLT", "samples": "#10", "seconds": 50, "camada": "n3-acesso", "priority": 3},
}

ITEM_KEY = "icmpping[,3,200,,500]"
ITEM_LOSS_KEY = "icmppingloss[,3,200,,500]"
ITEM_SEC_KEY = "icmppingsec[,3,200,,500]"

TRG_DOWN_DESC = "{HOST.NAME}: Equipamento inacessivel"
TRG_LOSS_DESC = "{HOST.NAME}: Perda de pacotes alta (>{$ICMP.LOSS.WARN}%)"
TRG_LAT_DESC = "{HOST.NAME}: Latencia alta (>80ms)"

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
BY_SERVICE = {node["service"]: node for node in NODES}


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
    candidates = [requested] if requested else [f"10.{x}" for x in range(232, 250)] + [f"172.{x}" for x in range(24, 30)]
    for prefix in candidates:
        if not re.fullmatch(r"\d{1,3}\.\d{1,3}", prefix or ""):
            raise LabError("LAB_NET_PREFIX deve ter dois octetos, por exemplo 10.232.")
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
        raise LabError(f"Já existe um projeto {PROJECT} sem o .env nesta pasta. Recupere a pasta original para preservar as credenciais.")
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
    web_port = free_port(8190, reserved_ports)
    notify_port = free_port(8191, reserved_ports | {web_port})
    env = {
        "LAB_NET_PREFIX": prefix, "LAB_WEB_PORT": str(web_port), "LAB_NOTIFY_PORT": str(notify_port),
        "LAB_DB_PASSWORD": secrets.token_hex(24), "LAB_ADMIN_PASSWORD": "Noto7!" + secrets.token_urlsafe(18),
        "LAB_NOTIFY_TOKEN": secrets.token_hex(24), "LAB_ZABBIX_TAG": "alpine-7.0-latest",
    }
    fd = os.open(ENV_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write("# Credenciais geradas para esta instalação (Aula 02). Preserve este arquivo.\n")
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
        raise LabError("Execute ./lab.sh up para cadastrar os templates e hosts no Zabbix.")
    return json.loads(STATE_PATH.read_text())


def update_badge(env, state):
    topo = "TOPOLOGIA: ON" if state.get("mode", "on") == "on" else "TOPOLOGIA: OFF"
    internal = "TEMPLATE INTERNO: ON" if state.get("internal_mode", "on") == "on" else "TEMPLATE INTERNO: OFF"
    feed(env, "/mode", {"mode": f"{topo} · {internal}"})


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
        "mediatypeid": mediaid, "sendto": ["laboratorio-local-aula02"], "active": 0,
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
                         "label": f"N{node['level']} | {node['host']}\n({ROLE_TEMPLATES[node['level']]['host'].replace('TPL - NOTO TI - ', '')})",
                         "label_location": 1, "x": x, "y": y})
        if node["parent"]:
            links.append({"selementid1": ids[node["parent"]], "selementid2": elementid, "color": "16B89A", "drawtype": 2})
    return api.call("map.create", {"name": MAP_NAME, "width": 1200, "height": 650,
                                    "label_type": 0, "selements": elements, "links": links})["sysmapids"][0]


def ensure_templates(api):
    tpl_groups = api.call("templategroup.get", {"output": ["groupid"], "filter": {"name": [TPL_GROUP]}})
    tpl_groupid = tpl_groups[0]["groupid"] if tpl_groups else api.call("templategroup.create", {"name": TPL_GROUP})["groupids"][0]

    base_macros = [
        {"macro": "{$ICMP.FALHAS}", "value": "#3", "description": "Amostras consecutivas para indisponibilidade (sobrescrita por camada: #2, #6, #10)"},
        {"macro": "{$ICMP.PERDA.AMOSTRAS}", "value": "#2", "description": "Amostras consecutivas para perda de pacotes (~10s, abre antes da latência)"},
        {"macro": "{$ICMP.LATENCIA.AMOSTRAS}", "value": "#4", "description": "Amostras consecutivas para latência alta (~20s, avalia após perda)"},
        {"macro": "{$ICMP.LOSS.WARN}", "value": "20", "description": "Limite percentual de perda de pacotes ICMP (%)"},
        {"macro": "{$ICMP.LATENCIA.WARN}", "value": "0.08", "description": "Limite de tempo de resposta ICMP em segundos (80ms)"},
    ]
    found_base = api.call("template.get", {"output": ["templateid"], "filter": {"host": [TPL_BASE]}})
    base_params = {
        "host": TPL_BASE,
        "name": TPL_BASE,
        "description": "Template Base único de ICMP (Disponibilidade, Perda e Latência) com dependências internas e macros.",
        "groups": [{"groupid": tpl_groupid}],
        "macros": base_macros,
        "tags": [{"tag": "escopo", "value": "icmp-base"}, {"tag": "lab", "value": "nototi-cascata-aula02"}],
    }
    if found_base:
        base_id = found_base[0]["templateid"]
        api.call("template.update", {**base_params, "templateid": base_id})
    else:
        base_id = api.call("template.create", base_params)["templateids"][0]

    # 3 Itens ICMP no Template Base
    items_spec = [
        {"name": "Disponibilidade ICMP", "key_": ITEM_KEY, "value_type": 3, "units": ""},
        {"name": "Perda de pacotes ICMP", "key_": ITEM_LOSS_KEY, "value_type": 0, "units": "%"},
        {"name": "Tempo de resposta ICMP", "key_": ITEM_SEC_KEY, "value_type": 0, "units": "s"},
    ]
    base_items = {}
    for spec in items_spec:
        found_item = api.call("item.get", {"output": ["itemid"], "hostids": [base_id], "filter": {"key_": [spec["key_"]]}})
        payload = {
            "name": spec["name"], "key_": spec["key_"], "type": 3, "value_type": spec["value_type"],
            "units": spec["units"], "delay": "5s", "history": "1d", "trends": "0", "status": 0,
        }
        if found_item:
            itemid = found_item[0]["itemid"]
            api.call("item.update", {**payload, "itemid": itemid})
        else:
            itemid = api.call("item.create", {**payload, "hostid": base_id})["itemids"][0]
        base_items[spec["key_"]] = itemid

    # 3 Triggers parametrizadas no Template Base
    triggers_spec = [
        {
            "key": "down",
            "description": TRG_DOWN_DESC,
            "expression": f"max(/{TPL_BASE}/{ITEM_KEY},{{$ICMP.FALHAS}})=0",
            "priority": 4,
            "tags": [{"tag": "lab", "value": "nototi-cascata-aula02"}, {"tag": "tipo_alerta", "value": "indisponibilidade"}],
        },
        {
            "key": "loss",
            "description": TRG_LOSS_DESC,
            "expression": (
                f"min(/{TPL_BASE}/{ITEM_LOSS_KEY},{{$ICMP.PERDA.AMOSTRAS}})>{{$ICMP.LOSS.WARN}}"
                f" and max(/{TPL_BASE}/{ITEM_LOSS_KEY},{{$ICMP.PERDA.AMOSTRAS}})<100"
            ),
            "priority": 3,
            "tags": [{"tag": "lab", "value": "nototi-cascata-aula02"}, {"tag": "tipo_alerta", "value": "perda_pacotes"}],
        },
        {
            "key": "latency",
            "description": TRG_LAT_DESC,
            "expression": f"min(/{TPL_BASE}/{ITEM_SEC_KEY},{{$ICMP.LATENCIA.AMOSTRAS}})>{{$ICMP.LATENCIA.WARN}}",
            "priority": 2,
            "tags": [{"tag": "lab", "value": "nototi-cascata-aula02"}, {"tag": "tipo_alerta", "value": "latencia"}],
        },
    ]
    base_triggers = {}
    for spec in triggers_spec:
        found_trg = api.call("trigger.get", {"output": ["triggerid"], "hostids": [base_id], "filter": {"description": [spec["description"]]}})
        payload = {
            "description": spec["description"],
            "expression": spec["expression"],
            "priority": spec["priority"],
            "status": 1,
            "tags": spec["tags"],
        }
        if found_trg:
            triggerid = found_trg[0]["triggerid"]
            api.call("trigger.update", {**payload, "triggerid": triggerid})
        else:
            triggerid = api.call("trigger.create", payload)["triggerids"][0]
        base_triggers[spec["key"]] = triggerid

    # 3 Templates de Papel/Camada (Herança vinculando o Template Base)
    role_ids = {}
    for level, role in ROLE_TEMPLATES.items():
        found_role = api.call("template.get", {"output": ["templateid"], "filter": {"host": [role["host"]]}})
        role_params = {
            "host": role["host"],
            "name": role["host"],
            "description": f"Template da Camada N{level} — herda '{TPL_BASE}' e define {{$ICMP.FALHAS}}={role['samples']}.",
            "groups": [{"groupid": tpl_groupid}],
            "templates": [{"templateid": base_id}],
            "macros": [
                {"macro": "{$ICMP.FALHAS}", "value": role["samples"],
                 "description": f"Janela da camada N{level}: {role['samples']} coletas ({role['seconds']}s)"},
            ],
            "tags": [{"tag": "camada", "value": role["camada"]}, {"tag": "nivel", "value": str(level)}],
        }
        if found_role:
            role_id = found_role[0]["templateid"]
            api.call("template.update", {**role_params, "templateid": role_id})
        else:
            role_id = api.call("template.create", role_params)["templateids"][0]
        role_ids[str(level)] = {"templateid": role_id, "host": role["host"], "samples": role["samples"], "camada": role["camada"]}

    return {
        "tpl_groupid": tpl_groupid,
        "base": {"templateid": base_id, "host": TPL_BASE, "items": base_items, "triggers": base_triggers},
        "roles": role_ids,
    }


def apply_internal_dependencies(api, state, internal_mode):
    base_triggers = state["templates"]["base"]["triggers"]
    if internal_mode == "on":
        changes = [
            {"triggerid": base_triggers["down"], "dependencies": []},
            {"triggerid": base_triggers["loss"], "dependencies": [{"triggerid": base_triggers["down"]}]},
            {"triggerid": base_triggers["latency"], "dependencies": [
                {"triggerid": base_triggers["loss"]},
                {"triggerid": base_triggers["down"]},
            ]},
        ]
    else:
        changes = [
            {"triggerid": base_triggers["down"], "dependencies": []},
            {"triggerid": base_triggers["loss"], "dependencies": []},
            {"triggerid": base_triggers["latency"], "dependencies": []},
        ]
    api.call("trigger.update", changes)
    state["internal_mode"] = internal_mode
    save_state(state)
    update_badge(api.env, state)


def apply_dependencies(api, state, mode):
    """Configura as dependências topológicas lendo a tag 'uplink' cadastrada nos hosts."""
    hosts_in_zbx = api.call("host.get", {
        "output": ["hostid", "host"],
        "groupids": [state["groupid"]],
        "selectTags": ["tag", "value"],
    })
    uplink_by_host = {}
    for h in hosts_in_zbx:
        tags = {t["tag"]: t["value"] for t in h.get("tags", [])}
        uplink = tags.get("uplink")
        uplink_by_host[h["host"]] = uplink if uplink and uplink != "raiz" else None

    changes = []
    for node in NODES:
        host_name = node["host"]
        parent = uplink_by_host.get(host_name, node["parent"])
        parent_trg = state["hosts"][parent]["triggerid"] if parent and mode == "on" else None
        dependencies = [{"triggerid": parent_trg}] if parent_trg else []
        changes.append({"triggerid": state["hosts"][host_name]["triggerid"], "dependencies": dependencies})
    api.call("trigger.update", changes)
    state["mode"] = mode
    save_state(state)
    update_badge(api.env, state)


def bootstrap(api):
    groups = api.call("hostgroup.get", {"output": ["groupid"], "filter": {"name": [GROUP]}})
    groupid = groups[0]["groupid"] if groups else api.call("hostgroup.create", {"name": GROUP})["groupids"][0]
    for action in api.call("action.get", {"output": ["actionid"], "filter": {"name": [ACTION]}}):
        api.call("action.update", {"actionid": action["actionid"], "status": 1})
    for host in api.call("host.get", {"output": ["hostid"], "filter": {"host": ["Zabbix server"]}}):
        api.call("host.update", {"hostid": host["hostid"], "status": 1})

    templates_info = ensure_templates(api)
    state = {"groupid": groupid, "mode": "on", "internal_mode": "on", "templates": templates_info, "hosts": {}}

    for node in NODES:
        ip = f"{api.env['LAB_NET_PREFIX']}.{node['suffix']}"
        role_tpl_id = templates_info["roles"][str(node["level"])]["templateid"]
        role_tpl_name = ROLE_TEMPLATES[node["level"]]["host"]
        uplink_value = node["parent"] or "raiz"
        host_tags = [
            {"tag": "lab", "value": "nototi-cascata-aula02"},
            {"tag": "pop", "value": node["pop"]},
            {"tag": "nivel", "value": str(node["level"])},
            {"tag": "uplink", "value": uplink_value},
        ]
        found = api.call("host.get", {"output": ["hostid"], "filter": {"host": [node["host"]]}})
        if found:
            hostid = found[0]["hostid"]
            api.call("host.update", {
                "hostid": hostid, "status": 0,
                "groups": [{"groupid": groupid}],
                "templates": [{"templateid": role_tpl_id}],
                "tags": host_tags,
            })
            interfaces = api.call("hostinterface.get", {"output": ["interfaceid", "type", "main"], "hostids": [hostid]})
            interfaceid = next(row["interfaceid"] for row in interfaces if row["type"] == "1" and row["main"] == "1")
            api.call("hostinterface.update", {"interfaceid": interfaceid, "ip": ip, "useip": 1})
        else:
            hostid = api.call("host.create", {
                "host": node["host"],
                "groups": [{"groupid": groupid}],
                "templates": [{"templateid": role_tpl_id}],
                "interfaces": [{"type": 1, "main": 1, "useip": 1, "ip": ip, "dns": "", "port": "10050"}],
                "tags": host_tags,
            })["hostids"][0]

        # Localiza os itens e triggers herdados automaticamente do Template no Host
        host_items = api.call("item.get", {"output": ["itemid", "key_"], "hostids": [hostid]})
        items_by_key = {row["key_"]: row["itemid"] for row in host_items}
        host_triggers = api.call("trigger.get", {"output": ["triggerid", "description", "templateid"], "hostids": [hostid]})
        trgs_by_desc = {row["description"]: row["triggerid"] for row in host_triggers}

        down_trg_id = trgs_by_desc[TRG_DOWN_DESC]
        loss_trg_id = trgs_by_desc[TRG_LOSS_DESC]
        lat_trg_id = trgs_by_desc[TRG_LAT_DESC]

        # Ajusta severidade por camada na trigger herdada de indisponibilidade
        api.call("trigger.update", {
            "triggerid": down_trg_id,
            "priority": ROLE_TEMPLATES[node["level"]]["priority"],
        })

        state["hosts"][node["host"]] = {
            "hostid": hostid,
            "ip": ip,
            "template": role_tpl_name,
            "uplink": uplink_value,
            "itemid": items_by_key[ITEM_KEY],
            "loss_itemid": items_by_key[ITEM_LOSS_KEY],
            "sec_itemid": items_by_key[ITEM_SEC_KEY],
            "triggerid": down_trg_id,
            "loss_triggerid": loss_trg_id,
            "lat_triggerid": lat_trg_id,
        }

    state["actionid"] = ensure_notifications(api, groupid)
    state["mapid"] = ensure_map(api, state)
    save_state(state)
    apply_internal_dependencies(api, state, "on")
    apply_dependencies(api, state, "on")
    clear_impairments()
    warmup(api, state)
    # Habilita as triggers no Template Base (propaga para todos os templates filhos e hosts)
    base_trgs = state["templates"]["base"]["triggers"]
    api.call("trigger.update", [{"triggerid": tid, "status": 0} for tid in base_trgs.values()])
    reload_cache()
    wait_healthy(api, state)
    api.call("action.update", {"actionid": state["actionid"], "status": 0})
    reload_cache()
    feed(api.env, "/clear", {})
    say("4 templates (1 Base ICMP + 3 de Camada), 7 hosts herdados, dependências internas e topológicas cadastrados.")
    return state


def reload_cache():
    compose("exec", "-T", "zabbix-server", "zabbix_server", "-R", "config_cache_reload", capture=True)
    time.sleep(7)


def all_tracked_triggers(state):
    mapping = {}
    for name, host in state["hosts"].items():
        mapping[host["triggerid"]] = (name, "DOWN")
        if "loss_triggerid" in host:
            mapping[host["loss_triggerid"]] = (name, "LOSS")
        if "lat_triggerid" in host:
            mapping[host["lat_triggerid"]] = (name, "LATENCY")
    return mapping


def active_problems_detailed(api, state):
    mapping = all_tracked_triggers(state)
    triggers = api.call("trigger.get", {"output": ["triggerid", "value", "state", "error"],
                                        "triggerids": list(mapping.keys())})
    unknown = [row for row in triggers if str(row.get("state", "0")) != "0"]
    if unknown:
        raise LabError(f"Trigger não suportada: {unknown[0].get('error', '')}")
    return [mapping[row["triggerid"]] for row in triggers if row["value"] == "1"]


def active_problems(api, state):
    return {host for host, _ in active_problems_detailed(api, state)}


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
        if not active_problems_detailed(api, state):
            return
        time.sleep(2)
    raise LabError("Ainda há problemas ativos depois da recuperação. Consulte ./lab.sh status.")


def clear_node_impairment(service):
    script = (
        "iptables -F INPUT 2>/dev/null || true; "
        "for d in $(ip -o link show | awk -F': ' '{print $2}' | cut -d@ -f1); do "
        "case \"$d\" in eth*) tc qdisc del dev \"$d\" root 2>/dev/null || true ;; esac; "
        "done"
    )
    compose("exec", "-T", service, "sh", "-c", script, capture=True, check=False)


def clear_impairments():
    for service in SERVICES:
        clear_node_impairment(service)


def impair_node(env, service, loss=True, delay_ms=150):
    node = BY_SERVICE[service]
    ip = f"{env['LAB_NET_PREFIX']}.{node['suffix']}"
    compose("start", service)
    clear_node_impairment(service)
    cmds = []
    if loss:
        cmds.append("iptables -A INPUT -p icmp --icmp-type echo-request -m statistic --mode nth --every 2 --packet 0 -j DROP")
    if delay_ms > 0:
        cmds.append(
            "for d in $(ip -o link show | awk -F': ' '{print $2}' | cut -d@ -f1); do "
            "case \"$d\" in eth*) "
            f"tc qdisc replace dev \"$d\" root handle 1: prio && "
            f"tc qdisc replace dev \"$d\" parent 1:3 handle 30: netem delay {delay_ms}ms && "
            f"tc filter add dev \"$d\" protocol ip parent 1:0 prio 1 u32 match ip src {ip}/32 flowid 1:3 "
            ";; esac; done"
        )
    compose("exec", "-T", service, "sh", "-c", " && ".join(cmds))


def restore(api, state):
    compose("start", *SERVICES)
    clear_impairments()
    warmup(api, state)
    wait_healthy(api, state)
    say("Todos os equipamentos recuperados, degradações limpas e coletas estabilizadas.")


def set_mode(api, state, mode):
    restore(api, state)
    apply_dependencies(api, state, mode)
    reload_cache()
    feed(api.env, "/clear", {})
    say("Modo topológico (entre hosts): " + ("COM DEPENDÊNCIAS" if mode == "on" else "SEM DEPENDÊNCIAS"))


def set_internal_mode(api, state, internal_mode):
    restore(api, state)
    apply_internal_dependencies(api, state, internal_mode)
    reload_cache()
    feed(api.env, "/clear", {})
    say("Modo interno (no Template Base): " + ("COM DEPENDÊNCIAS INTERNAS" if internal_mode == "on" else "SEM DEPENDÊNCIAS INTERNAS"))


def ping_all(env, show=True):
    values = {}
    for node in NODES:
        ip = f"{env['LAB_NET_PREFIX']}.{node['suffix']}"
        result = compose("exec", "-T", "noc", "ping", "-c", "1", "-W", "1", ip, capture=True, check=False)
        values[node["host"]] = result.returncode == 0
        if show:
            say(f"{node['host']:<22} {ip:<17} {'RESPONDE' if result.returncode == 0 else 'SEM RESPOSTA'}")
    return values


def show_templates(state):
    say("=== ARQUITETURA DE TEMPLATES (AULA 02) ===")
    say(f"Grupo de Templates : {TPL_GROUP}")
    say(f"Template Base ICMP : {TPL_BASE}")
    say("  ├─ Itens         : icmpping, icmppingloss (%), icmppingsec (s) [coleta 5s]")
    say("  ├─ Macros Base   : {$ICMP.FALHAS}=#3 | {$ICMP.PERDA.AMOSTRAS}=#2 | {$ICMP.LATENCIA.AMOSTRAS}=#4 | {$ICMP.LOSS.WARN}=20 | {$ICMP.LATENCIA.WARN}=0.08")
    say(f"  └─ Dep. Internas : Latencia alta ──► Perda de pacotes alta ──► Equipamento inacessivel (modo: {state.get('internal_mode', 'on')})\n")
    say(f"{'TEMPLATE DE CAMADA (HERDA O BASE)':<34} {'MACRO {$ICMP.FALHAS}':<22} {'JANELA QUEDA':<15} HOSTS VINCULADOS")
    for level, role in ROLE_TEMPLATES.items():
        hosts = ", ".join(n["host"] for n in NODES if n["level"] == level)
        janela = f"~{role['seconds']}s ({role['samples']})"
        say(f"{role['host']:<34} {role['samples']:<22} {janela:<15} {hosts}")
    say("\n=== AMARRAÇÃO TOPOLÓGICA POR TAG NO HOST ===")
    say(f"{'HOST':<18} {'TEMPLATE VINCULADO':<32} {'TAG uplink':<18} DEPENDE DE (MODO={state.get('mode', 'on')})")
    for node in NODES:
        h = state["hosts"][node["host"]]
        dep = (node["parent"] or "—") if state.get("mode", "on") == "on" else "—"
        say(f"{node['host']:<18} {h.get('template', ROLE_TEMPLATES[node['level']]['host']):<32} {h.get('uplink', node['parent'] or 'raiz'):<18} {dep}")


def status(api, state):
    itemids = []
    for h in state["hosts"].values():
        itemids.extend([h["itemid"], h.get("loss_itemid"), h.get("sec_itemid")])
    itemids = [i for i in itemids if i]
    items = api.call("item.get", {"output": ["itemid", "lastvalue", "lastclock", "state", "error"], "itemids": itemids})
    by_id = {item["itemid"]: item for item in items}
    detailed = active_problems_detailed(api, state)
    problems_by_host = {}
    for host_name, kind in detailed:
        problems_by_host.setdefault(host_name, []).append(kind)

    say(f"Dependências Topológicas (Entre Hosts): {state.get('mode', 'on')} | Dependências Internas (No Template): {state.get('internal_mode', 'on')}")
    say(f"{'EQUIPAMENTO':<17} {'PING':<6} {'LOSS%':<8} {'LAT(ms)':<9} {'TRIGGER(S)':<18} {'TEMPLATE':<28} DEPENDE DE")
    for node in NODES:
        h = state["hosts"][node["host"]]
        ping_item = by_id[h["itemid"]]
        ping_val = "S/D" if int(ping_item.get("lastclock", 0)) < time.time() - 20 else ping_item["lastvalue"]
        loss_val = by_id[h["loss_itemid"]]["lastvalue"] if h.get("loss_itemid") in by_id else "0"
        sec_val = by_id[h["sec_itemid"]]["lastvalue"] if h.get("sec_itemid") in by_id else "0"
        try:
            loss_str = f"{float(loss_val):.0f}%"
        except ValueError:
            loss_str = str(loss_val)
        try:
            lat_str = f"{float(sec_val) * 1000:.1f}"
        except ValueError:
            lat_str = str(sec_val)
        active_kinds = problems_by_host.get(node["host"], [])
        trg_str = ",".join(active_kinds) if active_kinds else "OK"
        tpl_short = ROLE_TEMPLATES[node["level"]]["host"].replace("TPL - NOTO TI - ", "")
        parent = (node["parent"] or "—") if state.get("mode", "on") == "on" else "—"
        say(f"{node['host']:<17} {ping_val:<6} {loss_str:<8} {lat_str:<9} {trg_str:<18} {tpl_short:<28} {parent}")
    say("PING 0 = sem resposta, mesmo quando a trigger dependente permanece OK.")


def show_urls(env, state=None, credentials=False):
    say(f"Zabbix (Aula 02): http://127.0.0.1:{env['LAB_WEB_PORT']}")
    say(f"Notificações (Aula 02): http://127.0.0.1:{env['LAB_NOTIFY_PORT']}")
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
        ("core sem dependências topológicas", "off", ["core"], all_hosts, all_hosts),
        ("core com dependências topológicas", "on", ["core"], all_hosts, {"CORE-ISP"}),
        ("POP Centro com dependências", "on", ["pop-centro"], centro, {"SW-POP-CENTRO"}),
        ("OLT isolada", "on", ["olt-centro-01"], {"OLT-CENTRO-01"}, {"OLT-CENTRO-01"}),
        ("falhas independentes nos dois POPs", "on", ["pop-centro", "olt-norte-01"],
         centro | {"OLT-NORTE-01"}, {"SW-POP-CENTRO", "OLT-NORTE-01"}),
    ]
    original_mode = state.get("mode", "on")
    original_internal = state.get("internal_mode", "on")
    report = {"started_at": dt.datetime.now(dt.timezone.utc).isoformat(), "results": [], "passed": False}
    try:
        apply_internal_dependencies(api, state, "on")
        for label, mode, failed_services, unreachable, expected in cases:
            say(f"\nTESTE: {label}")
            set_mode(api, state, mode)
            start = time.monotonic()
            compose("stop", "-t", "2", *failed_services)
            reached = ping_all(api.env, show=False)
            actual_down = {host for host, responding in reached.items() if not responding}
            if actual_down != unreachable:
                raise LabError(f"Caminho de rede incorreto. Esperado sem resposta: {sorted(unreachable)}; observado: {sorted(actual_down)}")
            for second in range(0, 75, 5):
                if second % 15 == 0:
                    say(f"Aguardando avaliação das três camadas (templates N1/N2/N3): {second}/75 s...")
                time.sleep(5)
            active = active_problems(api, state)
            messages = [row for row in feed(api.env, "/notifications") if row["event_value"] == "1"]
            message_hosts = {row["host"] for row in messages}
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
            apply_internal_dependencies(api, state, original_internal)
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
    parser = argparse.ArgumentParser(description="NOTO TI - Aula 02: Templates, Macros e Dependências no Zabbix")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("up", "down", "stop", "restore", "status", "templates", "ping", "test", "clear", "urls", "credentials", "logs"):
        sub.add_parser(command)
    mode = sub.add_parser("mode")
    mode.add_argument("value", choices=("on", "off"))
    internal = sub.add_parser("internal")
    internal.add_argument("value", choices=("on", "off"))
    for command in ("fail", "recover", "degrade", "latency"):
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
            compose("start", *SERVICES)
            clear_impairments()
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
                say("Pronto. Veja os templates com: ./lab.sh templates")
            finally:
                api.close()
            return
        if args.command in ("down", "stop"):
            compose(args.command)
            say("Laboratório Aula 02 parado. Os volumes e as credenciais foram preservados.")
            return
        if args.command == "logs":
            compose("logs", "--tail", "100", "zabbix-server", "zabbix-web", "noc", "core", "pop-centro", "pop-norte", "notificacoes")
            return
        if args.command in ("urls", "credentials"):
            show_urls(env, read_state(), credentials=args.command == "credentials")
            return
        if args.command == "templates":
            show_templates(read_state())
            return
        if args.command == "ping":
            ping_all(env)
            return
        if args.command == "clear":
            feed(env, "/clear", {})
            say("Lista de notificações limpa; o histórico de eventos do Zabbix permanece disponível.")
            return
        if args.command == "fail":
            clear_node_impairment(args.equipment)
            compose("stop", "-t", "2", args.equipment)
            say(f"{args.equipment}: parado (queda total). Acompanhe ./lab.sh status e a página de notificações.")
            return
        if args.command == "recover":
            compose("start", args.equipment)
            clear_node_impairment(args.equipment)
            say(f"{args.equipment}: recuperado (online e sem degradação). Acompanhe ./lab.sh status e a página de notificações.")
            return
        if args.command == "degrade":
            impair_node(env, args.equipment, loss=True, delay_ms=150)
            say(f"{args.equipment}: degradação aplicada (~50% perda de pacotes + 150ms de latência). Aguarde ~20s e veja ./lab.sh status.")
            return
        if args.command == "latency":
            impair_node(env, args.equipment, loss=False, delay_ms=150)
            say(f"{args.equipment}: latência alta aplicada (150ms, 0% perda). Aguarde ~20s e veja ./lab.sh status.")
            return
        state = read_state()
        api = Api(env).login()
        try:
            if args.command == "restore":
                restore(api, state)
            elif args.command == "mode":
                set_mode(api, state, args.value)
            elif args.command == "internal":
                set_internal_mode(api, state, args.value)
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
