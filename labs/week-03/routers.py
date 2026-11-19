#!/usr/bin/env python3
r"""Week 3 topology: three Linux routers and three hosts, on your own addresses.

    h1 ---- r1 ------------ r2 ---- h2
              \            /  \
               `---- r3 --'    `--- h3

NN = the last 2 digits of your student ID (no leading zero), NNNN = the last 4.

Network            IPv4              IPv6                Addresses
h1's LAN           10.NN.1.0/24      fd00:NNNN:1::/64    h1 .10 / ::10, r1 .1 / ::1
r1-r2 link         10.NN.12.0/30     fd00:NNNN:12::/64   r1 .1 / ::1,   r2 .2 / ::2
r1-r3 link         10.NN.13.0/30     fd00:NNNN:13::/64   r1 .1 / ::1,   r3 .2 / ::2
r3-r2 link         10.NN.23.0/30     fd00:NNNN:23::/64   r3 .1 / ::1,   r2 .2 / ::2
h2's LAN           10.NN.32.0/24     fd00:NNNN:32::/64   r2 .1 / ::1,   h2 .10 / ::10
h3's LAN           10.NN.33.0/24     fd00:NNNN:33::/64   r2 .1 / ::1,   h3 .10 / ::10

Hosts are ready to go (each has default gateways). The routers are not: forwarding
is switched off and they only know their directly connected networks.

Run it inside the lab container, with the last 4 digits of your student ID:
    python3 routers.py --id 4207

Step 10 configures the routers from data instead of by hand:
    python3 routers.py --id 4207 --routes routes.yaml
checks every route first, and only then switches forwarding on and applies them.
"""
import argparse
import ipaddress

from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import Node

# (network name, third IPv4 octet / IPv6 group, prefix length, [(node, host part), ...])
NETWORKS = [
    ("h1's LAN", 1, 24, [("h1", 10), ("r1", 1)]),
    ("r1-r2 link", 12, 30, [("r1", 1), ("r2", 2)]),
    ("r1-r3 link", 13, 30, [("r1", 1), ("r3", 2)]),
    ("r3-r2 link", 23, 30, [("r3", 1), ("r2", 2)]),
    ("h2's LAN", 32, 24, [("r2", 1), ("h2", 10)]),
    ("h3's LAN", 33, 24, [("r2", 1), ("h3", 10)]),
]
GATEWAYS = {"h1": 1, "h2": 32, "h3": 33}  # each host's LAN; its router is .1 / ::1


class Router(Node):
    """A Linux host that will route once forwarding is switched on."""

    def config(self, **params):
        super().config(**params)
        self.cmd("sysctl -qw net.ipv4.ip_forward=0 net.ipv6.conf.all.forwarding=0")
        # Linux normally rate-limits ICMP error replies; switch that off so
        # traceroute shows every hop instead of gaps of "*".
        self.cmd("sysctl -qw net.ipv4.icmp_ratelimit=0 net.ipv6.icmp.ratelimit=0")


def build(nn, nnnn):
    v4 = lambda net, host: f"10.{nn}.{net}.{host}"
    v6 = lambda net, host: f"fd00:{nnnn}:{net}::{host}"

    net = Mininet(controller=None)
    for name in ("r1", "r2", "r3"):
        net.addHost(name, cls=Router, ip=None)
    for name in ("h1", "h2", "h3"):
        net.addHost(name, ip=None)

    v6_plan = []  # IPv6 addresses go on after the network starts: Mininet only sets IPv4
    for _, third, plen, (a, b) in NETWORKS:
        link = net.addLink(a[0], b[0], params1={"ip": f"{v4(third, a[1])}/{plen}"}, params2={"ip": f"{v4(third, b[1])}/{plen}"})
        v6_plan += [(a[0], link.intf1.name, v6(third, a[1])), (b[0], link.intf2.name, v6(third, b[1]))]

    net.start()
    for node, intf, address in v6_plan:
        net.get(node).cmd(f"ip -6 addr add {address}/64 dev {intf} nodad")
    for host, third in GATEWAYS.items():
        net.get(host).cmd(f"ip route add default via {v4(third, 1)}")
        net.get(host).cmd(f"ip -6 route add default via {v6(third, 1)}")
    return net


# ---------- Step 10: routes as data ----------------------------------------------------------

ROUTERS = ("r1", "r2", "r3")


def addresses(nn):
    """Every interface address in the network: {node: [IPv4 interface, ...]}, from NETWORKS."""
    out = {}
    for _, third, plen, members in NETWORKS:
        for node, host in members:
            out.setdefault(node, []).append(ipaddress.ip_interface(f"10.{nn}.{third}.{host}/{plen}"))
    return out


def owner(address, nn):
    """The node that has this IPv4 address, or None."""
    for node, intfs in addresses(nn).items():
        if any(i.ip == address for i in intfs):
            return node
    return None


def load_routes(path):
    import yaml  # in the lab container

    with open(path) as f:
        return yaml.safe_load(f) or {}


def check_routes(routes, nn):
    """Supplied: each route belongs to a router, uses your addresses, and has a next hop the
    router can reach directly. A route that fails these would be refused, or would never work."""
    problems = []
    block = ipaddress.ip_network(f"10.{nn}.0.0/16")
    for router, entries in routes.items():
        if router not in ROUTERS:
            problems.append(f"{router} isn't one of the routers {', '.join(ROUTERS)}")
            continue
        for e in entries or []:
            try:
                to, via = ipaddress.ip_network(e["to"]), ipaddress.ip_address(e["via"])
            except (KeyError, TypeError, ValueError):
                problems.append(f"{router}: {e} isn't a valid route (still NN in it?)")
                continue
            if not to.subnet_of(block):
                problems.append(f"{router}: {to} isn't in your block {block}")
            if not any(via in i.network for i in addresses(nn)[router]):
                problems.append(f"{router}: next hop {via} for {to} isn't on a network {router} is connected to")
    return problems


def lookup(router, destination, routes, nn):
    """What `ip route get` does: of every route that matches, the longest prefix wins.

    Returns ("connected", None) if the destination is on one of the router's own networks,
    ("via", next_hop) if a route sends it on, or ("none", None) if nothing matches.
    """
    candidates = [(i.network, None) for i in addresses(nn)[router]]
    candidates += [(ipaddress.ip_network(e["to"]), ipaddress.ip_address(e["via"])) for e in routes.get(router) or []]
    matches = [(net, via) for net, via in candidates if destination in net]
    if not matches:
        return ("none", None)
    _, via = max(matches, key=lambda m: m[0].prefixlen)
    return ("connected", None) if via is None else ("via", via)


def check_paths(routes, nn):
    """TODO (Step 10): follow each packet's path, router by router, and report what goes wrong.

    For every pair of different hosts (h1 to h2, h2 to h1, h1 to h3, ...):
      - the destination is   ipaddress.ip_address(f"10.{nn}.{GATEWAYS[dst]}.10")
      - start at the source host's router: owner(ipaddress.ip_address(f"10.{nn}.{GATEWAYS[src]}.1"), nn)
      - then repeat, asking lookup(router, destination, routes, nn) what the router would do:
          "connected": the packet is delivered. This pair is fine.
          "none":      PROBLEM, e.g. "h2 to h1: r2 has no route to 10.7.1.10"
          "via":       move on to owner(next_hop, nn)
        If you reach a router you've already visited on this path, the packet is looping:
        PROBLEM, e.g. "h1 to h3: loop r1 -> r3 -> r1"
    Return the list of problem messages, like check() in Week 2.
    """
    problems = []
    # TODO: your code here
    return problems


def apply_routes(net, routes):
    """Switch forwarding on and add every route, printing each command as it runs."""
    for router in ROUTERS:
        net.get(router).cmd("sysctl -qw net.ipv4.ip_forward=1")
        print(f"{router} sysctl -w net.ipv4.ip_forward=1")
        for e in routes.get(router) or []:
            command = f"ip route add {e['to']} via {e['via']}"
            print(f"{router} {command}")
            out = net.get(router).cmd(command).strip()
            if out:
                print(f"    {out}")


def print_plan(nn, nnnn):
    print(f"\nYour networks (student ID ending {nnnn}):")
    for name, third, plen, members in NETWORKS:
        who = ", ".join(f"{n} .{h} / ::{h}" for n, h in members)
        print(f"  {name:<11} 10.{nn}.{third}.0/{plen:<3}  fd00:{nnnn}:{third}::/64   {who}")
    print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Week 3: three Linux routers on your own addresses.")
    ap.add_argument("--id", required=True, metavar="NNNN", help="the last 4 digits of your student ID")
    ap.add_argument("--routes", metavar="FILE", help="Step 10: configure the routers from this routes file")
    args = ap.parse_args()
    nnnn = args.id
    if len(nnnn) != 4 or not nnnn.isdigit():
        ap.error("--id must be the last 4 digits of your student ID, e.g. --id 4207")
    nn = int(nnnn[2:])

    routes = None
    if args.routes:
        # Check the data before any router is touched, as build_topo.py did in Week 2.
        routes = load_routes(args.routes)
        problems = check_routes(routes, nn)
        if not problems:
            problems = check_paths(routes, nn)
        if problems:
            for p in problems:
                print("PROBLEM:", p)
            raise SystemExit("Fix the routes and try again: nothing was configured.")

    setLogLevel("info")
    net = build(nn, nnnn)
    print_plan(nn, nnnn)
    if routes is not None:
        print(f"Applying {args.routes}:")
        apply_routes(net, routes)
        print()
    CLI(net)
    net.stop()
