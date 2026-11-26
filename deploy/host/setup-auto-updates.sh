#!/usr/bin/env bash
# Turns on weekly automatic updates for the Debian box that hosts the bot.
# Run it once on that box:  sudo bash setup-auto-updates.sh
# It's safe to run again; it rewrites the same files each time.
#
# What it sets up (times are the box's local time, see `timedatectl`):
#   Sunday 03:30  refresh the package lists
#   Sunday 04:00  install updates from Debian (security and stable updates),
#                 Docker (download.docker.com) and Tailscale (pkgs.tailscale.com)
#   Sunday 04:30  reboot, but only if an update needs it (/var/run/reboot-required)
# If the box is off on Sunday night, the run happens the next time it boots.
#
# Things to know:
#   - A Docker update restarts the bot's container; it comes back by itself
#     (restart: unless-stopped in compose.yaml). So does a reboot.
#   - A Tailscale update drops the tailnet link for a few seconds. The job runs
#     on the box itself, so that doesn't interrupt it.
#   - In an LXC, a reboot only restarts the container, and the kernel is the
#     Proxmox host's, so kernel updates come from the host instead.
#   - Don't run this on a Proxmox host itself; Proxmox wants its own updates
#     done by hand.
#   - Logs: /var/log/unattended-upgrades/
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this as root: sudo bash $0" >&2
  exit 1
fi

# Debian, or something built on it such as Raspberry Pi OS.
. /etc/os-release
if [ "${ID:-}" != "debian" ] && [[ " ${ID_LIKE:-} " != *" debian "* ]]; then
  echo "This is for Debian-based systems; /etc/os-release says ID=${ID:-unknown}." >&2
  exit 1
fi
if [ -e /etc/pve ]; then
  echo "This looks like a Proxmox host (/etc/pve exists). Run it inside the bot's VM or LXC instead." >&2
  exit 1
fi

echo "==> Installing unattended-upgrades"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q unattended-upgrades

echo "==> Turning on the periodic apt jobs"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
// Written by discord-adjutant's deploy/host/setup-auto-updates.sh.
// The schedule itself is set by the apt-daily timers (see the weekly.conf drop-ins).
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF

echo "==> Choosing what to update and allowing reboots"
# Sorts after Debian's 50unattended-upgrades. Apt adds list entries together,
# so Debian's default origins (the release and security updates) still apply.
cat > /etc/apt/apt.conf.d/52adjutant-unattended-upgrades <<'EOF'
// Written by discord-adjutant's deploy/host/setup-auto-updates.sh.
Unattended-Upgrade::Origins-Pattern {
    // Debian stable updates (security updates are already in Debian's defaults).
    "origin=Debian,codename=${distro_codename}-updates,label=Debian";
    // Docker Engine and the Compose plugin.
    "site=download.docker.com";
    // Tailscale.
    "site=pkgs.tailscale.com";
};

// Reboot when an update needs it, at 04:30, after the 04:00 run.
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-WithUsers "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";

// Keep /boot from filling up with old kernels.
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
EOF

echo "==> Moving the apt timers to once a week"
# An empty OnCalendar= clears the daily schedule from the packaged timer.
# Persistent=true (from the packaged timer) still catches up a missed run at boot.
mkdir -p /etc/systemd/system/apt-daily.timer.d /etc/systemd/system/apt-daily-upgrade.timer.d
cat > /etc/systemd/system/apt-daily.timer.d/weekly.conf <<'EOF'
# Written by discord-adjutant's deploy/host/setup-auto-updates.sh.
[Timer]
OnCalendar=
OnCalendar=Sun *-*-* 03:30
RandomizedDelaySec=10m
EOF
cat > /etc/systemd/system/apt-daily-upgrade.timer.d/weekly.conf <<'EOF'
# Written by discord-adjutant's deploy/host/setup-auto-updates.sh.
[Timer]
OnCalendar=
OnCalendar=Sun *-*-* 04:00
RandomizedDelaySec=0
EOF

systemctl daemon-reload
systemctl enable --now apt-daily.timer apt-daily-upgrade.timer
# Restart so the timers pick up the new schedule straight away.
systemctl restart apt-daily.timer apt-daily-upgrade.timer

echo
echo "==> Done. Check the timezone the schedule uses:"
timedatectl | grep -i 'time zone' || true
echo
echo "==> Next runs:"
systemctl list-timers --all 'apt-daily*' --no-pager
echo
echo "==> Sources it will update from:"
unattended-upgrade --dry-run --debug 2>&1 | grep -i 'allowed origins' || \
  echo "(Couldn't read them; run: sudo unattended-upgrade --dry-run --debug)"
echo
cat <<'EOF'
To patch now instead of waiting for Sunday:
  sudo unattended-upgrade -v
  [ -f /var/run/reboot-required ] && sudo reboot

To undo all of this:
  sudo rm /etc/apt/apt.conf.d/20auto-upgrades /etc/apt/apt.conf.d/52adjutant-unattended-upgrades
  sudo rm -r /etc/systemd/system/apt-daily.timer.d /etc/systemd/system/apt-daily-upgrade.timer.d
  sudo systemctl daemon-reload
EOF
