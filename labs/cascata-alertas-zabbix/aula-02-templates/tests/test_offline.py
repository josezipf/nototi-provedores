"""Verificações offline da Aula 02 (Templates + Dependências Internas e Topológicas)."""
import concurrent.futures
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lab = load_module("lab", ROOT / "scripts/lab.py")
receiver = load_module("receiver", ROOT / "notifications/receiver.py")


class RoutingModel:
    """Interpreta as rotas emitidas pelo entrypoint e as interfaces do Compose."""
    def __init__(self):
        source = (ROOT / "compose.yaml").read_text().replace("${LAB_NET_PREFIX}", "10.232")
        self.compose = yaml.safe_load(source)
        self.interfaces, self.routes = {}, {}
        prefix = ipaddress.ip_address
        for name in ["noc", *lab.SERVICES]:
            service = self.compose["services"][name]
            self.interfaces[name] = [ipaddress.ip_interface(net["ipv4_address"] + "/24") for net in service["networks"].values()]
            self.routes[name] = [(iface.network, None) for iface in self.interfaces[name]]
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                (folder / "ip").write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$ROUTE_LOG"\n')
                (folder / "tail").write_text("#!/bin/sh\nexit 0\n")
                (folder / "touch").write_text("#!/bin/sh\nexit 0\n")
                for executable in ("ip", "tail", "touch"):
                    (folder / executable).chmod(0o755)
                env = dict(os.environ, PATH=tmp + ":" + os.environ["PATH"], LAB_NET_PREFIX="10.232",
                           LAB_ROLE=service["environment"]["LAB_ROLE"], ROUTE_LOG=str(folder / "routes"))
                subprocess.run(["sh", str(ROOT / "devices/entrypoint.sh")], env=env, check=True)
                for line in (folder / "routes").read_text().splitlines():
                    _, _, dest, _, gateway = line.split()
                    dest = ipaddress.ip_network("0.0.0.0/0" if dest == "default" else dest)
                    self.routes[name].append((dest, prefix(gateway)))
        self.owners = {iface.ip: name for name, interfaces in self.interfaces.items() for iface in interfaces}

    def route(self, origin, destination, stopped):
        current = origin
        for _ in range(16):
            if current in stopped:
                return False
            if self.owners.get(destination) == current:
                return True
            choices = [(net, gateway) for net, gateway in self.routes[current] if destination in net]
            if not choices:
                return False
            net, gateway = max(choices, key=lambda route: route[0].prefixlen)
            next_ip = gateway or destination
            if not any(next_ip in interface.network for interface in self.interfaces[current]):
                return False
            current = self.owners.get(next_ip)
            if current is None:
                return False
            if current != self.owners.get(destination):
                forward = self.compose["services"][current]["sysctls"]["net.ipv4.ip_forward"]
                if str(forward) != "1":
                    return False
        return False

    def inaccessible(self, stopped):
        source = ipaddress.ip_address("10.232.10.10")
        down = set()
        for node in lab.NODES:
            dest = ipaddress.ip_address("10.232." + node["suffix"])
            if not self.route("noc", dest, stopped) or not self.route(node["service"], source, stopped):
                down.add(node["host"])
        return down


class NetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = RoutingModel()

    def test_all_targets_have_return_path(self):
        self.assertEqual(self.model.inaccessible(set()), set())

    def test_core_is_the_single_path_to_every_pop(self):
        self.assertEqual(self.model.inaccessible({"core"}), {node["host"] for node in lab.NODES})

    def test_each_pop_failure_is_restricted_to_its_branch(self):
        for pop in ("centro", "norte"):
            with self.subTest(pop=pop):
                expected = {node["host"] for node in lab.NODES if node["pop"] == pop}
                self.assertEqual(self.model.inaccessible({"pop-" + pop}), expected)

    def test_independent_leaf_failures_are_not_hidden_by_other_pop(self):
        expected = {"SW-POP-CENTRO", "OLT-CENTRO-01", "OLT-CENTRO-02", "OLT-NORTE-01"}
        self.assertEqual(self.model.inaccessible({"pop-centro", "olt-norte-01"}), expected)

    def test_leaf_failure_does_not_affect_its_sibling(self):
        for node in lab.NODES:
            if node["level"] == 3:
                self.assertEqual(self.model.inaccessible({node["service"]}), {node["host"]})

    def test_zabbix_shares_noc_and_has_no_shortcut_network(self):
        service = self.model.compose["services"]["zabbix-server"]
        self.assertEqual(service["network_mode"], "service:noc")
        self.assertNotIn("networks", service)
        for node in lab.NODES:
            self.assertNotIn("backend", self.model.compose["services"][node["service"]]["networks"])

    def test_public_ports_are_loopback_only(self):
        for name, service in self.model.compose["services"].items():
            for port in service.get("ports", []):
                self.assertTrue(port.startswith("127.0.0.1:"), name)


class ConfigurationTests(unittest.TestCase):
    def test_network_selection_skips_real_conflicts(self):
        occupied = [ipaddress.ip_network("10.232.20.0/24"), ipaddress.ip_network("10.233.0.0/16")]
        self.assertEqual(lab.choose_prefix(occupied), "10.234")
        with self.assertRaises(lab.LabError):
            lab.choose_prefix(occupied, "10.232")
        with self.assertRaises(lab.LabError):
            lab.choose_prefix([], "8.8")

    def test_topological_dependencies_use_uplink_tags(self):
        state = {
            "groupid": "10",
            "hosts": {node["host"]: {"triggerid": str(i)} for i, node in enumerate(lab.NODES, 1)},
        }
        api = mock.Mock()
        api.env = {}
        api.call.side_effect = lambda method, params=None: (
            [{"hostid": str(i), "host": n["host"], "tags": [{"tag": "uplink", "value": n["parent"] or "raiz"}]}
             for i, n in enumerate(lab.NODES, 1)]
            if method == "host.get" else None
        )
        with mock.patch.object(lab, "save_state"), mock.patch.object(lab, "update_badge"):
            lab.apply_dependencies(api, state, "on")
            changes = api.call.call_args.args[1]
            self.assertEqual(sum(len(row["dependencies"]) for row in changes), 6)
            parent_by_id = {row["triggerid"]: row["dependencies"] for row in changes}
            self.assertEqual(parent_by_id["4"], [{"triggerid": "2"}])
            self.assertEqual(parent_by_id["6"], [{"triggerid": "3"}])
            lab.apply_dependencies(api, state, "off")
            self.assertTrue(all(row["dependencies"] == [] for row in api.call.call_args.args[1]))

    def test_internal_template_dependencies_can_be_toggled(self):
        state = {
            "templates": {"base": {"triggers": {"down": "101", "loss": "102", "latency": "103"}}},
        }
        api = mock.Mock()
        api.env = {}
        with mock.patch.object(lab, "save_state"), mock.patch.object(lab, "update_badge"):
            lab.apply_internal_dependencies(api, state, "on")
            changes = {row["triggerid"]: row["dependencies"] for row in api.call.call_args.args[1]}
            self.assertEqual(changes["101"], [])
            self.assertEqual(changes["102"], [{"triggerid": "101"}])
            self.assertEqual(changes["103"], [{"triggerid": "102"}, {"triggerid": "101"}])
            lab.apply_internal_dependencies(api, state, "off")
            changes_off = {row["triggerid"]: row["dependencies"] for row in api.call.call_args.args[1]}
            self.assertTrue(all(deps == [] for deps in changes_off.values()))


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(receiver, "DATA", Path(self.tmp.name)), mock.patch.object(receiver, "TOKEN", "test-token")]
        for patch in self.patches:
            patch.start()
        self.server = receiver.ThreadingHTTPServer(("127.0.0.1", 0), receiver.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def request(self, path, data=None, token="test-token"):
        req = urllib.request.Request(self.base + path, None if data is None else json.dumps(data).encode(),
                                     {"X-Lab-Token": token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, response.read().decode()

    def notify(self, event="1", host="CORE-ISP"):
        return self.request("/notify", {"event_id": "42", "event_value": event, "host": host, "problem": "Indisponível"})

    def test_authentication_and_json_validation(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/clear", {}, token="wrong-token")
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/notify", {"event_value": "1"})
        self.assertEqual(error.exception.code, 400)

    def test_counts_include_repeats_and_distinguish_recovery(self):
        self.notify()
        self.notify()
        self.notify("0")
        rows = json.loads(self.request("/notifications")[1])
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["event_value"] for r in rows], ["1", "1", "0"])
        self.request("/mode", {"mode": "TOPOLOGIA: ON · TEMPLATE INTERNO: ON"})
        page = self.request("/")[1]
        self.assertIn("TOPOLOGIA: ON", page)
        self.assertIn("<strong>2</strong>", page)
        self.assertIn("<strong>1</strong>", page)
        self.request("/clear", {})
        self.assertEqual(json.loads(self.request("/notifications")[1]), [])

    def test_event_text_is_escaped(self):
        self.notify(host='<script>alert("test")</script>')
        page = self.request("/")[1]
        self.assertNotIn('<script>alert("test")</script>', page)
        self.assertIn("&lt;script&gt;", page)

    def test_concurrent_notifications_are_persisted(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:
            list(pool.map(lambda i: self.notify(host="OLT-" + str(i)), range(14)))
        rows = json.loads(self.request("/notifications")[1])
        self.assertEqual(len(rows), 14)
        self.assertEqual(len({row["host"] for row in rows}), 14)


if __name__ == "__main__":
    unittest.main(verbosity=2)
