#!/bin/sh
set -eu
p="${LAB_NET_PREFIX:?LAB_NET_PREFIX ausente}"
case "${LAB_ROLE:?LAB_ROLE ausente}" in
  noc)
    for subnet in 20 30 40; do
      ip route replace "$p.$subnet.0/24" via "$p.10.2"
    done
    ;;
  core)
    ip route replace "$p.30.0/24" via "$p.20.11"
    ip route replace "$p.40.0/24" via "$p.20.12"
    ;;
  pop-centro|pop-norte)
    ip route replace "$p.10.0/24" via "$p.20.2"
    ;;
  olt-centro)
    ip route replace default via "$p.30.2"
    ;;
  olt-norte)
    ip route replace default via "$p.40.2"
    ;;
  *) echo 'Papel de rede desconhecido' >&2; exit 1 ;;
esac
touch /tmp/lab-node.ready
exec tail -f /dev/null
