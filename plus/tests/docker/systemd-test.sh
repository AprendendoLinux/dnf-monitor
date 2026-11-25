#!/bin/bash
# Teste com systemd real (container privilegiado). Uso: plus/tests/docker/systemd-test.sh [rockylinux:9 almalinux:10 ...]
set -uo pipefail
cd "$(dirname "$0")/../../.." || exit 1
rm -f dist/*.rpm plus/dist/*.rpm
./build.sh >/tmp/build-classic.log 2>&1 || { tail -30 /tmp/build-classic.log; echo "build do clássico falhou"; exit 1; }
plus/build.sh >/tmp/build-plus.log 2>&1 || { tail -30 /tmp/build-plus.log; echo "build do Plus falhou"; exit 1; }
IMAGES=("$@"); [ ${#IMAGES[@]} -gt 0 ] || IMAGES=(rockylinux:9 almalinux:10)
NETWORK=echelon; docker network inspect "$NETWORK" >/dev/null 2>&1 || NETWORK=bridge
rc=0
for base in "${IMAGES[@]}"; do
    tag="dnfmonplus-systemd-${base//[:\/]/-}"; name="$tag-run"
    echo "################ $base (systemd)"
    docker build -q -t "$tag" - >/dev/null <<DOCKER
FROM $base
RUN dnf -y -q install systemd python3 python3-dnf rpm-build createrepo_c iproute logrotate tzdata dnf-automatic >/dev/null 2>&1; dnf clean all >/dev/null 2>&1
STOPSIGNAL SIGRTMIN+3
CMD ["/usr/sbin/init"]
DOCKER
    docker rm -f "$name" >/dev/null 2>&1
    docker run -d --name "$name" --network "$NETWORK" --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
        --tmpfs /run --tmpfs /run/lock -v "$PWD":/work:ro "$tag" >/dev/null
    for _ in $(seq 30); do docker exec "$name" systemctl is-system-running 2>/dev/null | grep -qE 'running|degraded' && break; sleep 1; done
    log="/tmp/plus-systemd-${base//[:\/]/_}.log"
    if docker exec "$name" bash /work/plus/tests/docker/inside-systemd.sh > "$log" 2>&1; then
        echo "OK   $base"
    else
        echo "FAIL $base (veja $log)"; tail -25 "$log"; rc=1
    fi
    docker rm -f "$name" >/dev/null 2>&1
    docker rmi "$tag" >/dev/null 2>&1
done
exit $rc
