# syntax=docker/dockerfile:1
#
# Course lab image — Mininet, Open vSwitch and network tools, used in Weeks 1–6.
#
# You normally don't need this file: the image is published, and Week 1 starts it with
#   docker run -it --rm --privileged -v "$PWD":/lab -w /lab firdaussahran/netlab-mininet:1.0
#
# Build it yourself only if you can't pull the published image:
#   docker build -t firdaussahran/netlab-mininet:1.0 -f netlab-mininet.Dockerfile .

FROM ubuntu:24.04

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      mininet openvswitch-switch openvswitch-testcontroller bridge-utils \
      iproute2 iputils-ping net-tools traceroute tcpdump iperf3 curl \
      python3 python3-yaml procps nano less \
    && rm -rf /var/lib/apt/lists/*

# Starts the Open vSwitch daemons in the background (used from Week 4), then runs
# whatever command was given — a bash shell by default.
COPY --chmod=755 <<'EOF' /usr/local/bin/netlab-start
#!/bin/sh
mkdir -p /var/run/openvswitch /etc/openvswitch
[ -f /etc/openvswitch/conf.db ] || ovsdb-tool create /etc/openvswitch/conf.db /usr/share/openvswitch/vswitch.ovsschema
ovsdb-server --remote=punix:/var/run/openvswitch/db.sock --pidfile --detach -vconsole:off --log-file /etc/openvswitch/conf.db
ovs-vsctl --no-wait init
ovs-vswitchd --pidfile --detach -vconsole:off --log-file
exec "$@"
EOF

ENTRYPOINT ["/usr/local/bin/netlab-start"]
CMD ["bash"]
