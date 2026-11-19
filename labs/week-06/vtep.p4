// vtep.p4 — a VXLAN tunnel endpoint (VTEP) in P4, for BMv2's v1model (Week 6, lecture Part 3).
//
// Week 5's tunnel added one small header that every switch had to understand. This program
// does what a VXLAN VTEP does instead: it wraps the tenant's whole Ethernet frame in VXLAN, UDP
// and an outer IPv4 header, so the network in between only has to route IP.
//
//   Into the tunnel:  vtep_encap matches (ingress port, destination MAC) and pushes the headers.
//   Out of it:        vtep_decap matches (VNI, inner destination MAC) and pops them.
//
// Both tables are empty until the control plane fills them, as in the Week 5 lab. Tested on
// BMv2 against a Linux VXLAN device (ip link ... type vxlan): pings cross the tunnel both ways.
//
// Compile:  p4c-bm2-ss -o vtep.json vtep.p4      (in your Week 5 codespace, for example)
// Simplified for teaching: IPv4 only, no ARP, no flooding, no IPv4 options in the tenant packet.

#include <core.p4>
#include <v1model.p4>

typedef bit<48> macAddr_t;
typedef bit<32> ip4Addr_t;
typedef bit<9>  egressSpec_t;

header ethernet_t { macAddr_t dstAddr; macAddr_t srcAddr; bit<16> etherType; }
header ipv4_t {
    bit<4> version; bit<4> ihl; bit<8> diffserv; bit<16> totalLen; bit<16> identification;
    bit<3> flags; bit<13> fragOffset; bit<8> ttl; bit<8> protocol; bit<16> hdrChecksum;
    ip4Addr_t srcAddr; ip4Addr_t dstAddr;
}
header udp_t   { bit<16> srcPort; bit<16> dstPort; bit<16> length; bit<16> checksum; }
header vxlan_t { bit<8> flags; bit<24> reserved; bit<24> vni; bit<8> reserved2; }

struct metadata { }
struct headers {
    ethernet_t ethernet;          // outer, once encapsulated
    ipv4_t     ipv4;
    udp_t      udp;
    vxlan_t    vxlan;
    ethernet_t inner_ethernet;    // the tenant's frame
    ipv4_t     inner_ipv4;
}

parser MyParser(packet_in packet, out headers hdr, inout metadata meta,
                inout standard_metadata_t standard_metadata) {
    state start {
        packet.extract(hdr.ethernet);
        transition select(hdr.ethernet.etherType) { 0x0800: parse_ipv4; default: accept; }
    }
    state parse_ipv4 {
        packet.extract(hdr.ipv4);
        transition select(hdr.ipv4.protocol) { 17: parse_udp; default: accept; }
    }
    state parse_udp {
        packet.extract(hdr.udp);
        transition select(hdr.udp.dstPort) { 4789: parse_vxlan; default: accept; }
    }
    state parse_vxlan {
        packet.extract(hdr.vxlan);
        transition parse_inner_ethernet;
    }
    state parse_inner_ethernet {
        packet.extract(hdr.inner_ethernet);
        transition select(hdr.inner_ethernet.etherType) { 0x0800: parse_inner_ipv4; default: accept; }
    }
    state parse_inner_ipv4 {
        packet.extract(hdr.inner_ipv4);
        transition accept;
    }
}

control MyVerifyChecksum(inout headers hdr, inout metadata meta) { apply { } }

control MyIngress(inout headers hdr, inout metadata meta,
                  inout standard_metadata_t standard_metadata) {
    action drop() { mark_to_drop(standard_metadata); }

    // Into the tunnel: the tenant's frame moves inside, and outer headers go in front of it.
    action encap(bit<24> vni, macAddr_t my_mac, ip4Addr_t my_vtep,
                 ip4Addr_t remote_vtep, macAddr_t next_hop_mac, egressSpec_t port) {
        hdr.inner_ethernet = hdr.ethernet;
        hdr.inner_ipv4 = hdr.ipv4;

        hdr.vxlan.setValid();
        hdr.vxlan.flags = 0x08;                          // "I": the VNI is valid
        hdr.vxlan.vni = vni;

        hdr.udp.setValid();
        // Source port: a hash of the inner flow, so the underlay's ECMP can tell flows apart.
        hash(hdr.udp.srcPort, HashAlgorithm.crc16, (bit<16>)49152,
             { hdr.inner_ipv4.srcAddr, hdr.inner_ipv4.dstAddr, hdr.inner_ipv4.protocol },
             (bit<32>)16384);
        hdr.udp.dstPort = 4789;
        hdr.udp.length = hdr.inner_ipv4.totalLen + 30;   // UDP 8 + VXLAN 8 + inner Ethernet 14
        hdr.udp.checksum = 0;

        hdr.ipv4.totalLen = hdr.udp.length + 20;         // the old IPv4 header becomes the outer one
        hdr.ipv4.ttl = 64;                               // a fresh hop limit for the trip
        hdr.ipv4.protocol = 17;
        hdr.ipv4.srcAddr = my_vtep;
        hdr.ipv4.dstAddr = remote_vtep;

        hdr.ethernet.srcAddr = my_mac;
        hdr.ethernet.dstAddr = next_hop_mac;
        standard_metadata.egress_spec = port;
    }

    // Out of the tunnel: the inner frame becomes the packet again.
    action decap(egressSpec_t port) {
        hdr.ethernet = hdr.inner_ethernet;
        hdr.ipv4 = hdr.inner_ipv4;
        hdr.udp.setInvalid();
        hdr.vxlan.setInvalid();
        hdr.inner_ethernet.setInvalid();
        hdr.inner_ipv4.setInvalid();
        standard_metadata.egress_spec = port;
    }

    table vtep_encap {        // which remote VTEP is this tenant MAC behind?
        key = { standard_metadata.ingress_port: exact; hdr.ethernet.dstAddr: exact; }
        actions = { encap; drop; }
        default_action = drop();
    }
    table vtep_decap {        // which local port does this VNI belong to?
        key = { hdr.vxlan.vni: exact; hdr.inner_ethernet.dstAddr: exact; }
        actions = { decap; drop; }
        default_action = drop();
    }

    apply {
        if (hdr.vxlan.isValid()) { vtep_decap.apply(); }
        else if (hdr.ipv4.isValid()) { vtep_encap.apply(); }
        else { drop(); }
    }
}

control MyEgress(inout headers hdr, inout metadata meta,
                 inout standard_metadata_t standard_metadata) { apply { } }

control MyComputeChecksum(inout headers hdr, inout metadata meta) {
    apply {
        update_checksum(hdr.ipv4.isValid(),
            { hdr.ipv4.version, hdr.ipv4.ihl, hdr.ipv4.diffserv, hdr.ipv4.totalLen,
              hdr.ipv4.identification, hdr.ipv4.flags, hdr.ipv4.fragOffset, hdr.ipv4.ttl,
              hdr.ipv4.protocol, hdr.ipv4.srcAddr, hdr.ipv4.dstAddr },
            hdr.ipv4.hdrChecksum, HashAlgorithm.csum16);
    }
}

control MyDeparser(packet_out packet, in headers hdr) {
    apply {
        packet.emit(hdr.ethernet);
        packet.emit(hdr.ipv4);
        packet.emit(hdr.udp);
        packet.emit(hdr.vxlan);
        packet.emit(hdr.inner_ethernet);
        packet.emit(hdr.inner_ipv4);
    }
}

V1Switch(MyParser(), MyVerifyChecksum(), MyIngress(), MyEgress(), MyComputeChecksum(), MyDeparser()) main;
