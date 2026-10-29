#!/usr/bin/env python3
"""Week 3 assignment: build a routed network of your own design from network.yaml.

Run it inside the lab container, from your netlab folder:
    python3 build_routed.py network.yaml

It checks the description first: the addresses, links and next hops (supplied checks), and
loops and missing routes (your check_paths()). Only if everything passes does it build the
network in Mininet, switch forwarding on and apply your routes, printing each command.

Each LAN has one router and one host: the router is .1 on the LAN and the host is .10.
On each router-to-router link, the first router named is .1 and the second is .2.
"""
import ipaddress
import json
import sys

import yaml
from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import Node


def load(path):
    with open(path) as f:
        return json.load(f) if path.endswith(".json") else yaml.safe_load(f)


def plan(spec):
    """Every node's addresses, worked out from the LANs and links: {node: [IPv4 interface, ...]}."""
    out = {r: [] for r in spec["routers"]}
    for lan in spec["lans"]:
        net = ipaddress.ip_network(lan["network"])
        out[lan["router"]].append(ipaddress.ip_interface(f"{net.network_address + 1}/{net.prefixlen}"))
        out[lan["host"]] = [ipaddress.ip_interface(f"{net.network_address + 10}/{net.prefixlen}")]
    for a, b, network in spec["links"]:
        net = ipaddress.ip_network(network)
        out[a].append(ipaddress.ip_interface(f"{net.network_address + 1}/{net.prefixlen}"))
        out[b].append(ipaddress.ip_interface(f"{net.network_address + 2}/{net.prefixlen}"))
    return out


def hosts(spec):
    """{host: (its address, its router)} for every LAN."""
    return {
        lan["host"]: (ipaddress.ip_network(lan["network"]).network_address + 10, lan["router"]) for lan in spec["lans"]
    }


def owner(spec, address):
    """The node that has this address, or None."""
    for node, intfs in plan(spec).items():
        if any(i.ip == address for i in intfs):
            return node
    return None


def lookup(spec, router, destination):
    """What the router's forwarding table does with a destination: the longest matching prefix wins.

    Returns ("connected", None), ("via", next_hop) or ("none", None) — as in routers.py.
    """
    candidates = [(i.network, None) for i in plan(spec)[router]]
    candidates += [
        (ipaddress.ip_network(r["to"]), ipaddress.ip_address(r["via"])) for r in (spec.get("routes") or {}).get(router) or []
    ]
    matches = [(net, via) for net, via in candidates if destination in net]
    if not matches:
        return ("none", None)
    _, via = max(matches, key=lambda m: m[0].prefixlen)
    return ("connected", None) if via is None else ("via", via)


def check_spec(spec):
    """Supplied: is the description complete and consistent? Returns a list of problems."""
    problems = []
    last4 = str(spec.get("student_id_last4", ""))
    if len(last4) != 4 or not last4.isdigit():
        return ['student_id_last4 must be the last 4 digits of your student ID, in quotes, e.g. "4207"']
    block = ipaddress.ip_network(f"10.{int(last4[2:])}.0.0/16")
    routers = set(spec.get("routers") or [])
    networks = []  # (name, network) for the overlap check

    def network(text, where, max_prefix):
        try:
            net = ipaddress.ip_network(text)
        except (TypeError, ValueError):
            problems.append(f"{where}: {text} isn't a valid network (still NN in it?)")
            return None
        if not net.subnet_of(block):
            problems.append(f"{where}: {net} isn't in your block {block}")
        if net.prefixlen > max_prefix:
            problems.append(f"{where}: {net} is too small (needs /{max_prefix} or bigger)")
        return net

    seen_hosts = set()
    for lan in spec.get("lans") or []:
        where = f"LAN {lan.get('name', '?')}"
        if lan.get("router") not in routers:
            problems.append(f"{where}: router {lan.get('router')} isn't in routers")
        if lan.get("host") in seen_hosts or lan.get("host") in routers:
            problems.append(f"{where}: host name {lan.get('host')} is already used")
        seen_hosts.add(lan.get("host"))
        net = network(lan.get("network"), where, 28)
        if net:
            networks.append((where, net))
    for link in spec.get("links") or []:
        a, b, text = (list(link) + [None, None, None])[:3]
        where = f"link {a}–{b}"
        for end in (a, b):
            if end not in routers:
                problems.append(f"{where}: {end} isn't in routers")
        net = network(text, where, 30)
        if net:
            networks.append((where, net))
    for i, (w1, n1) in enumerate(networks):
        for w2, n2 in networks[i + 1 :]:
            if n1.overlaps(n2):
                problems.append(f"{w1} ({n1}) overlaps {w2} ({n2})")
    if problems:
        return problems

    addresses = plan(spec)
    for router, entries in (spec.get("routes") or {}).items():
        if router not in routers:
            problems.append(f"routes: {router} isn't in routers")
            continue
        for r in entries or []:
            try:
                to, via = ipaddress.ip_network(r["to"]), ipaddress.ip_address(r["via"])
            except (KeyError, TypeError, ValueError):
                problems.append(f"{router}: {r} isn't a valid route (still NN in it?)")
                continue
            if not to.subnet_of(block):
                problems.append(f"{router}: {to} isn't in your block {block}")
            if not any(via in i.network and via != i.ip for i in addresses[router]):
                problems.append(f"{router}: next hop {via} for {to} isn't a neighbour on a network {router} is connected to")
    return problems


def check_paths(spec):
    """TODO (assignment): the same job as check_paths() in routers.py, for any network.

    For every pair of different hosts, follow the packet from the source host's router,
    using lookup(spec, router, destination) at each router and owner(spec, next_hop) to move
    on. Report a router with no route, and a loop (a router visited twice on one path).
    hosts(spec) gives you each host's address and router.

    Start from your check_paths() in routers.py: the logic is the same; only the way you
    find hosts, routers and next hops changes.
    """
    problems = []
    # TODO: your code here
    return problems


class Router(Node):
    """A Linux host acting as a router: forwarding on, ICMP errors not rate-limited."""

    def config(self, **params):
        super().config(**params)
        self.cmd("sysctl -qw net.ipv4.ip_forward=1 net.ipv4.icmp_ratelimit=0")


def build(spec):
    net = Mininet(controller=None)
    for r in spec["routers"]:
        net.addHost(r, cls=Router, ip=None)
    for lan in spec["lans"]:
        net.addHost(lan["host"], ip=None)
    for lan in spec["lans"]:
        n = ipaddress.ip_network(lan["network"])
        net.addLink(lan["router"], lan["host"], params1={"ip": f"{n.network_address + 1}/{n.prefixlen}"},
                    params2={"ip": f"{n.network_address + 10}/{n.prefixlen}"})
    for a, b, network in spec["links"]:
        n = ipaddress.ip_network(network)
        net.addLink(a, b, params1={"ip": f"{n.network_address + 1}/{n.prefixlen}"}, params2={"ip": f"{n.network_address + 2}/{n.prefixlen}"})
    net.start()
    for lan in spec["lans"]:
        n = ipaddress.ip_network(lan["network"])
        net.get(lan["host"]).cmd(f"ip route add default via {n.network_address + 1}")
    print("Applying routes:")
    for router, entries in (spec.get("routes") or {}).items():
        for r in entries or []:
            command = f"ip route add {r['to']} via {r['via']}"
            print(f"  {router} {command}")
            out = net.get(router).cmd(command).strip()
            if out:
                print(f"      {out}")
    return net


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 build_routed.py <network.yaml>")
    spec = load(sys.argv[1])
    problems = check_spec(spec)
    if not problems:
        problems = check_paths(spec)
    if problems:
        for p in problems:
            print("PROBLEM:", p)
        sys.exit("Fix the description and try again: nothing was built.")

    setLogLevel("info")
    net = build(spec)
    print("\nHosts:", ", ".join(f"{h} {a} (via {r})" for h, (a, r) in hosts(spec).items()), "\n")
    CLI(net)
    net.stop()
