#!/usr/bin/with-contenv bash
# shellcheck shell=bash
# Install every package in /defaults/kali-packages.txt that Kali lacks.
#
# Runs as root at each container start (linuxserver custom init, mounted from
# docker/desktops/kali/). Normal starts find everything installed and finish in
# a second; the first start after an image update reinstalls the list, which
# takes several minutes, and the desktop comes up once it's done.
#
# Never blocks Kali from starting: if the VPN isn't up yet or apt fails, it
# logs why and Kali starts without the missing tools (they install next start).

list=/defaults/kali-packages.txt
[ -r "$list" ] || exit 0

missing=""
for pkg in $(sed 's/#.*//' "$list"); do
	dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed" \
		|| missing="$missing $pkg"
done

if [ -z "$missing" ]; then
	echo "[kali-tools] all tools present"
	exit 0
fi

echo "[kali-tools] installing:$missing"
export DEBIAN_FRONTEND=noninteractive
if apt-get update -qq && apt-get install -y -qq $missing; then
	apt-get clean
	echo "[kali-tools] done"
else
	echo "[kali-tools] install failed; Kali starts without:$missing (retried next start)"
fi
exit 0
