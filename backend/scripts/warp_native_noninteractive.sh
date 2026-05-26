#!/usr/bin/env bash
set -euo pipefail

LICENSE_KEY=""
WATCHDOG_INTERVAL="10"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --license-key)
      LICENSE_KEY="${2:-}"
      shift 2
      ;;
    --watchdog-interval)
      WATCHDOG_INTERVAL="${2:-10}"
      shift 2
      ;;
    *)
      echo "{\"status\":\"install_failed\",\"license_step_reached\":false,\"reason\":\"unknown argument: $1\"}"
      exit 0
      ;;
  esac
done

if [[ -z "$LICENSE_KEY" ]]; then
  echo "{\"status\":\"license_failed\",\"license_step_reached\":true,\"reason\":\"missing license key\"}"
  exit 0
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update >/tmp/warp-install.log 2>&1 || {
  echo "{\"status\":\"install_failed\",\"license_step_reached\":false,\"reason\":\"apt update failed\"}"
  exit 0
}
apt-get install -y wireguard-tools curl ca-certificates >/tmp/warp-install.log 2>&1 || {
  echo "{\"status\":\"install_failed\",\"license_step_reached\":false,\"reason\":\"package install failed\"}"
  exit 0
}

if ! command -v wgcf >/dev/null 2>&1; then
  arch="$(uname -m)"
  case "$arch" in
    x86_64) wgcf_arch="amd64" ;;
    aarch64|arm64) wgcf_arch="arm64" ;;
    *) echo "{\"status\":\"install_failed\",\"license_step_reached\":false,\"reason\":\"unsupported architecture: $arch\"}"; exit 0 ;;
  esac
  release_url="$(curl -fsSL https://api.github.com/repos/ViRb3/wgcf/releases/latest | grep browser_download_url | grep "linux_${wgcf_arch}" | head -n1 | cut -d '"' -f4)"
  curl -fsSL "$release_url" -o /usr/local/bin/wgcf >/tmp/warp-install.log 2>&1 || {
    echo "{\"status\":\"install_failed\",\"license_step_reached\":false,\"reason\":\"wgcf download failed\"}"
    exit 0
  }
  chmod +x /usr/local/bin/wgcf
fi

workdir="/etc/warp-native"
mkdir -p "$workdir"
cd "$workdir"
if [[ ! -f wgcf-account.toml ]]; then
  yes | wgcf register >/tmp/warp-install.log 2>&1 || true
fi
if [[ ! -f wgcf-account.toml ]]; then
  echo "{\"status\":\"install_failed\",\"license_step_reached\":false,\"reason\":\"wgcf register failed\"}"
  exit 0
fi

if wgcf update --license-key "$LICENSE_KEY" >/tmp/warp-license.log 2>&1; then
  wgcf generate >/tmp/warp-generate.log 2>&1 || {
    echo "{\"status\":\"install_failed\",\"license_step_reached\":true,\"reason\":\"wgcf generate failed\"}"
    exit 0
  }
  install -d /etc/wireguard
  cp wgcf-profile.conf /etc/wireguard/warp.conf
  sed -i '/^DNS =/d' /etc/wireguard/warp.conf
  grep -q '^Table = off' /etc/wireguard/warp.conf || sed -i '/^\[Interface\]/a Table = off' /etc/wireguard/warp.conf
  grep -q '^PersistentKeepalive = 25' /etc/wireguard/warp.conf || sed -i '/^\[Peer\]/a PersistentKeepalive = 25' /etc/wireguard/warp.conf
  systemctl enable wg-quick@warp >/tmp/warp-service.log 2>&1 || true
  systemctl restart wg-quick@warp >/tmp/warp-service.log 2>&1 || true
  cat >/etc/cron.d/warp-native <<CRON
*/${WATCHDOG_INTERVAL} * * * * root systemctl is-active --quiet wg-quick@warp || systemctl restart wg-quick@warp
CRON
  echo "{\"status\":\"license_applied\",\"license_step_reached\":true,\"reason\":\"WARP+ license applied\"}"
else
  echo "{\"status\":\"license_failed\",\"license_step_reached\":true,\"reason\":\"wgcf rejected license\"}"
fi
