#!/bin/bash
# Gera dist/dnf-monitor-<versão>-1.noarch.rpm a partir de src/ e packaging/.
# Usa o rpmbuild local; se não houver, usa um container (docker/podman) com Rocky Linux 9.
# O RPM é noarch e sem tag de distro: serve para EL8, EL9 e EL10.
set -euo pipefail
cd "$(dirname "$0")" || exit 1

VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/dnf_monitor.py)
[ -n "$VERSION" ] || { echo "não achei __version__" >&2; exit 1; }

TOP="$PWD/build/rpmbuild"
rm -rf build; rm -f dist/dnf-monitor-*.rpm
mkdir -p "$TOP"/{SOURCES,SPECS,BUILD,RPMS,SRPMS} dist
cp src/dnf_monitor.py packaging/dnf-monitor.{conf,service,logrotate,8} README.md CHANGELOG.md LICENSE "$TOP/SOURCES/"
sed "s/@VERSION@/$VERSION/g" packaging/dnf-monitor.spec > "$TOP/SPECS/dnf-monitor.spec"

if command -v rpmbuild >/dev/null 2>&1; then
    rpmbuild -bb --define "_topdir $TOP" "$TOP/SPECS/dnf-monitor.spec"
else
    ENGINE=$(command -v docker || command -v podman || true)
    [ -n "$ENGINE" ] || { echo "instale rpm-build ou docker/podman" >&2; exit 1; }
    "$ENGINE" run --rm -v "$PWD/build:/build" -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" rockylinux:9 bash -c '
        trap "chown -R $HOST_UID:$HOST_GID /build" EXIT      # mesmo se o rpmbuild falhar
        dnf -y -q install rpm-build >/dev/null 2>&1 &&
        rpmbuild -bb --define "_topdir /build/rpmbuild" /build/rpmbuild/SPECS/dnf-monitor.spec'
fi
cp "$TOP"/RPMS/noarch/dnf-monitor-"$VERSION"-1.noarch.rpm dist/
echo "Gerado: dist/dnf-monitor-$VERSION-1.noarch.rpm"
