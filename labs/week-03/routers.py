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
"""
import argparse

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


def print_plan(nn, nnnn):
    print(f"\nYour networks (student ID ending {nnnn}):")
    for name, third, plen, members in NETWORKS:
        who = ", ".join(f"{n} .{h} / ::{h}" for n, h in members)
        print(f"  {name:<11} 10.{nn}.{third}.0/{plen:<3}  fd00:{nnnn}:{third}::/64   {who}")
    print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Week 3: three Linux routers on your own addresses.")
    ap.add_argument("--id", required=True, metavar="NNNN", help="the last 4 digits of your student ID")
    nnnn = ap.parse_args().id
    if len(nnnn) != 4 or not nnnn.isdigit():
        ap.error("--id must be the last 4 digits of your student ID, e.g. --id 4207")
    nn = int(nnnn[2:])

    setLogLevel("info")
    net = build(nn, nnnn)
    print_plan(nn, nnnn)
    CLI(net)
    net.stop()
