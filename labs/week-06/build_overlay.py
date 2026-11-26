#!/usr/bin/env python3
"""Week 6 assignment: build a multi-tenant VXLAN overlay of your own design from overlay.yaml.

Run it in the course container, from your netlab folder:
    python3 build_overlay.py overlay.yaml
    python3 build_overlay.py blackhole.yaml --force     # build even if check_mtu() objects

It checks the design first: the size, addresses and VNIs (supplied checks) and the MTU plan
(your check_mtu()). Only if everything passes does it build the overlay in Mininet:

  - an underlay switch, s1, joining the hypervisors on the underlay network;
  - each hypervisor as a Mininet host, at .1, .2, .3 … on the underlay, in the order listed;
  - each VM as a Mininet host, plugged into its hypervisor by its own cable;
  - on every hypervisor, for every tenant it hosts, the commands from your tenant_commands():
    printed as they run.

Two things the builder also does, so your evidence shows only what you sent:
  - it turns off segmentation offload on each VM's interface, so every TCP segment is a real
    packet, as on a physical wire (otherwise Linux can carry oversized TCP data past an MTU);
  - it turns off multicast on each bridge your commands create, so the bridges' own IGMP
    messages don't fill the VTEP tables before you send anything.
And it switches IPv6 off on every node: this assignment is IPv4 only.

Two parts are yours to write: tenant_commands() and check_mtu(). Look for TODO.

VM addresses: a tenant's VMs get .10, .11, .12 … of the tenant's network, in the order listed.
VM MAC addresses are 02:VV:VV:VV:00:XX — the VNI in the middle, the address's last byte at the
end — so you can tell from a MAC which tenant and which VM it belongs to.
"""
import ipaddress
import json
import re
import shlex
import sys

import yaml
from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.nodelib import LinuxBridge

# Turns off TCP and generic segmentation offload on the interfaces named on the command line
# (what `ethtool -K <if> tso off gso off` does; ethtool isn't in the lab container).
NO_OFFLOAD = (
    "import array,fcntl,socket,struct,sys;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);"
    "[fcntl.ioctl(s,0x8946,struct.pack('16sP',n.encode(),b.buffer_info()[0])) for n in sys.argv[1:]"
    " for c in (0x1f,0x24) for b in [array.array('B',struct.pack('II',c,0))]]"
)

VXLAN_OVERHEAD = 50  # inner Ethernet 14 + VXLAN 8 + UDP 8 + outer IPv4 20
NAME = re.compile(r"^[a-z][a-z0-9]{0,7}$")


def load(path):
    with open(path) as f:
        return json.load(f) if path.endswith(".json") else yaml.safe_load(f)


def plan(spec):
    """Every address the design implies.

    Returns {"hypervisors": {hv: underlay IP}, "vms": {vm: {"tenant", "vni", "hv", "ip", "prefix", "mac"}}}.
    """
    underlay = ipaddress.ip_network(spec["underlay"]["network"])
    hvs = {hv: str(underlay.network_address + 1 + i) for i, hv in enumerate(spec["hypervisors"])}
    vms = {}
    for t in spec["tenants"]:
        net = ipaddress.ip_network(t["network"])
        for k, (vm, hv) in enumerate(t["vms"]):
            ip = net.network_address + 10 + k
            vni = int(t["vni"])
            mac = f"02:{(vni >> 16) & 255:02x}:{(vni >> 8) & 255:02x}:{vni & 255:02x}:00:{int(ip) & 255:02x}"
            vms[vm] = {"tenant": t["name"], "vni": vni, "hv": hv, "ip": str(ip), "prefix": net.prefixlen, "mac": mac}
    return {"hypervisors": hvs, "vms": vms}


def vm_ports(spec, hv):
    """{vm: the hypervisor's interface that the VM is plugged into}, for one hypervisor.

    hvN-eth0 goes to the underlay switch; each VM on hvN gets the next port, in the order the
    VMs appear in overlay.yaml (tenant by tenant).
    """
    ports, n = {}, 1
    for t in spec["tenants"]:
        for vm, where in t["vms"]:
            if where == hv:
                ports[vm] = f"{hv}-eth{n}"
                n += 1
    return ports


def check_spec(spec):
    """The supplied checks: shape, names, your own addresses, VNIs and the size requirements."""
    problems = []
    try:
        last4 = str(spec["student_id_last4"])
        nn = int(last4[2:])
        block = ipaddress.ip_network(f"10.{nn}.0.0/16")
        underlay = ipaddress.ip_network(spec["underlay"]["network"])
        int(spec["underlay"]["mtu"])
        hvs = list(spec["hypervisors"])
        tenants = list(spec["tenants"])
    except (KeyError, TypeError, ValueError) as e:
        return [f"overlay.yaml is missing something or can't be read: {e}"]

    if not underlay.subnet_of(block):
        problems.append(f"underlay {underlay} is outside your block {block}")
    if len(hvs) < 3:
        problems.append(f"at least 3 hypervisors are needed; there are {len(hvs)}")
    if len(hvs) > underlay.num_addresses - 2:
        problems.append("too many hypervisors for the underlay network")

    names, vnis, nets = set(), {}, {}
    for name in hvs:
        if not NAME.match(str(name)) or name in names:
            problems.append(f"hypervisor name {name!r}: use a-z and 0-9, start with a letter, at most 8 characters, no repeats")
        names.add(name)
    if len(tenants) < 2:
        problems.append(f"at least 2 tenants are needed; there are {len(tenants)}")

    spans = {}
    for t in tenants:
        try:
            name, vni, net, mtu, vms = t["name"], int(t["vni"]), ipaddress.ip_network(t["network"]), int(t["mtu"]), t["vms"]
        except (KeyError, TypeError, ValueError) as e:
            problems.append(f"tenant {t.get('name', '?')}: missing or unreadable field ({e})")
            continue
        if not 1 <= vni <= 16_777_215:
            problems.append(f"tenant {name}: VNI {vni} must be between 1 and 16777215")
        if vni in vnis:
            problems.append(f"tenants {vnis[vni]} and {name} both use VNI {vni}: each tenant needs its own")
        vnis[vni] = name
        if not net.subnet_of(block):
            problems.append(f"tenant {name}: {net} is outside your block {block}")
        if net.overlaps(underlay):
            problems.append(f"tenant {name}: {net} overlaps the underlay {underlay}")
        if not 576 <= mtu <= 9000:
            problems.append(f"tenant {name}: MTU {mtu} should be between 576 and 9000")
        nets.setdefault(net, []).append(name)
        if len(vms) > net.num_addresses - 11:
            problems.append(f"tenant {name}: too many VMs for {net}")
        where = set()
        for entry in vms:
            try:
                vm, hv = entry
            except (TypeError, ValueError):
                problems.append(f"tenant {name}: each VM is written [name, hypervisor]; got {entry!r}")
                continue
            if not NAME.match(str(vm)) or vm in names:
                problems.append(f"VM name {vm!r}: use a-z and 0-9, start with a letter, at most 8 characters, unique across the design")
            names.add(vm)
            if hv not in hvs:
                problems.append(f"VM {vm}: hypervisor {hv!r} isn't in the hypervisors list")
            where.add(hv)
        spans[name] = where
        if len(where) < 2:
            problems.append(f"tenant {name}: its VMs must be on at least 2 hypervisors, so its traffic crosses the overlay")

    if not any(len(owners) >= 2 for owners in nets.values()):
        problems.append("no two tenants share a network: at least two tenants must use the same addresses")
    if spans and max(len(w) for w in spans.values()) < 3:
        problems.append("no tenant spans 3 hypervisors: at least one must, so you can watch flooding")
    if not any(sum(hv in w for w in spans.values()) >= 2 for hv in hvs):
        problems.append("no hypervisor hosts two tenants: at least one must")
    return problems


def check_mtu(spec):
    """TODO (assignment): every tenant's packets must fit through the tunnel.

    A tenant's VMs send IP packets up to the tenant's MTU. On the underlay, each one also
    carries VXLAN_OVERHEAD (50) bytes of headers, and the underlay carries IP packets up to its
    own MTU (spec["underlay"]["mtu"]).

    Return a list of problems, one string per tenant whose packets wouldn't fit, saying which
    tenant and by how much — or [] if every tenant fits. Like check_paths() in Week 3, it runs
    before anything is built.
    """
    problems = []
    # TODO: your code here
    return problems


def tenant_commands(spec, hv, tenant):
    """TODO (assignment): the commands that give one hypervisor one tenant, as a list of strings.

    `hv` is a hypervisor's name; `tenant` is one entry of spec["tenants"]. Every hypervisor that
    hosts the tenant needs, in this order:
      - a bridge for the tenant, br<VNI>, up;
      - a VXLAN device, vxlan<VNI>: id <VNI>, local <this hypervisor's underlay IP>,
        dstport 4789, dev <the hypervisor's underlay interface, e.g. hv1-eth0> — and no remote:
        with three or more hypervisors, there's no single other end;
      - the VXLAN device in the bridge, up;
      - the ports of this tenant's VMs on this hypervisor in the bridge, up;
      - for every OTHER hypervisor that hosts this tenant, one flood entry:
            bridge fdb append 00:00:00:00:00:00 dev vxlan<VNI> dst <its underlay IP>
        so a broadcast, or a frame for a MAC the VTEP hasn't learned yet, is copied to each of
        them: head-end replication.

    The lab's Part 2 and Part 3 commands are your starting point. plan(spec) gives you every
    hypervisor's underlay IP; vm_ports(spec, hv) tells you which port each VM is plugged into.
    """
    cmds = []
    # TODO: your code here
    return cmds


def all_commands(spec):
    """{hypervisor: [commands]} for the whole design, from tenant_commands()."""
    out = {}
    for hv in spec["hypervisors"]:
        out[hv] = []
        for t in spec["tenants"]:
            if any(where == hv for _, where in t["vms"]):
                out[hv] += tenant_commands(spec, hv, t) or []
    return out


def build(spec):
    p = plan(spec)
    underlay = ipaddress.ip_network(spec["underlay"]["network"])
    umtu = int(spec["underlay"]["mtu"])
    net = Mininet(controller=None, switch=LinuxBridge)
    s1 = net.addSwitch("s1")
    for hv in spec["hypervisors"]:
        net.addHost(hv, ip=None)
    for vm in p["vms"]:
        net.addHost(vm, ip=None)
    for hv in spec["hypervisors"]:
        net.addLink(hv, s1, params1={"ip": f"{p['hypervisors'][hv]}/{underlay.prefixlen}"})
    mtu_of = {}
    for t in spec["tenants"]:
        for vm, hv in t["vms"]:
            v = p["vms"][vm]
            net.addLink(hv, vm, params2={"ip": f"{v['ip']}/{v['prefix']}"})
            mtu_of[vm] = int(t["mtu"])
    net.start()

    # Keep IPv6 housekeeping out of the overlay, so the evidence shows only what you send.
    for node in [*spec["hypervisors"], *p["vms"]]:
        net.get(node).cmd("sysctl -qw net.ipv6.conf.all.disable_ipv6=1 net.ipv6.conf.default.disable_ipv6=1")
    for hv in spec["hypervisors"]:
        net.get(hv).cmd(f"ip link set {hv}-eth0 mtu {umtu}")
    for intf in s1.intfList():
        if intf.name != "lo":
            s1.cmd(f"ip link set {intf.name} mtu {umtu}")
    for hv in spec["hypervisors"]:
        for vm, port in vm_ports(spec, hv).items():
            net.get(hv).cmd(f"ip link set {port} mtu {mtu_of[vm]}")
            v = p["vms"][vm]
            net.get(vm).cmd(f"ip link set {vm}-eth0 down; ip link set {vm}-eth0 address {v['mac']} mtu {mtu_of[vm]}; ip link set {vm}-eth0 up")
            err = net.get(vm).cmd(f"python3 -c {shlex.quote(NO_OFFLOAD)} {vm}-eth0").strip()
            if err:
                print(f"WARNING: couldn't turn off offload on {vm}: {err}")

    commands = all_commands(spec)
    for hv, cmds in commands.items():
        print(f"VTEP commands on {hv} ({p['hypervisors'][hv]}):")
        for c in cmds:
            print(f"  {c}")
            out = net.get(hv).cmd(c).strip()
            if out:
                print(f"      {out}")
            made = re.match(r"ip link add (\S+) type bridge", c)
            if made:
                net.get(hv).cmd(f"ip link set {made.group(1)} type bridge mcast_snooping 0; ip link set {made.group(1)} multicast off")
    return net, commands


def problems_for(spec, force):
    problems = check_spec(spec)
    if not problems and not force:
        problems = check_mtu(spec)
    return problems


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv[1:]
    if len(args) != 1:
        sys.exit("usage: python3 build_overlay.py <overlay.yaml> [--force]")
    spec = load(args[0])
    problems = problems_for(spec, force)
    if problems:
        for pr in problems:
            print("PROBLEM:", pr)
        sys.exit("Fix the design and try again: nothing was built.")
    if force and check_mtu(spec):
        print("WARNING: --force: building despite check_mtu():")
        for pr in check_mtu(spec):
            print("  ", pr)

    if not any(all_commands(spec).values()):
        sys.exit("tenant_commands() returned no commands: write it first (look for TODO in build_overlay.py).")

    setLogLevel("info")
    net, _ = build(spec)
    p = plan(spec)
    print("\nHypervisors:", ", ".join(f"{hv} {ip}" for hv, ip in p["hypervisors"].items()))
    for t in spec["tenants"]:
        vms = ", ".join(f"{vm} {p['vms'][vm]['ip']} on {hv}" for vm, hv in t["vms"])
        print(f"Tenant {t['name']} (VNI {t['vni']}, MTU {t['mtu']}): {vms}")
    print()
    CLI(net)
    net.stop()
