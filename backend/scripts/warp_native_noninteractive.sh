#!/usr/bin/env bash
set -euo pipefail

LICENSE_KEY=""
WATCHDOG_INTERVAL="10"
STATUS_ONLY="false"
RESTORE_LATEST_BACKUP="false"
WORKDIR="/etc/warp-native"

emit() {
  printf '%s\n' "$1"
}

license_tail() {
  local value="${1:-}"
  if [[ -z "$value" ]]; then
    printf ''
  else
    printf '%s' "${value: -4}"
  fi
}

current_license() {
  if [[ ! -f wgcf-account.toml ]]; then
    printf ''
    return
  fi
  sed -n "s/^license_key[[:space:]]*=[[:space:]]*['\"]\\([^'\"]*\\)['\"].*/\\1/p" wgcf-account.toml | tail -n1
}

install_prerequisites() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update >/tmp/warp-install.log 2>&1 || {
    emit "{\"status\":\"install_failed\",\"license_step_reached\":false,\"reason\":\"apt update failed\"}"
    exit 0
  }
  apt-get install -y wireguard-tools curl ca-certificates >/tmp/warp-install.log 2>&1 || {
    emit "{\"status\":\"install_failed\",\"license_step_reached\":false,\"reason\":\"package install failed\"}"
    exit 0
  }

  if ! command -v wgcf >/dev/null 2>&1; then
    local arch wgcf_arch release_url
    arch="$(uname -m)"
    case "$arch" in
      x86_64) wgcf_arch="amd64" ;;
      aarch64|arm64) wgcf_arch="arm64" ;;
      *) emit "{\"status\":\"install_failed\",\"license_step_reached\":false,\"reason\":\"unsupported architecture: $arch\"}"; exit 0 ;;
    esac
    release_url="$(curl -fsSL https://api.github.com/repos/ViRb3/wgcf/releases/latest | grep browser_download_url | grep "linux_${wgcf_arch}" | head -n1 | cut -d '"' -f4)"
    curl -fsSL "$release_url" -o /usr/local/bin/wgcf >/tmp/warp-install.log 2>&1 || {
      emit "{\"status\":\"install_failed\",\"license_step_reached\":false,\"reason\":\"wgcf download failed\"}"
      exit 0
    }
    chmod +x /usr/local/bin/wgcf
  fi
}

ensure_account() {
  mkdir -p "$WORKDIR"
  cd "$WORKDIR"
  if [[ ! -f wgcf-account.toml ]]; then
    yes | wgcf register >/tmp/warp-install.log 2>&1 || true
  fi
  if [[ ! -f wgcf-account.toml ]]; then
    emit "{\"status\":\"install_failed\",\"license_step_reached\":false,\"reason\":\"wgcf register failed\"}"
    exit 0
  fi
}

backup_account() {
  if [[ -f wgcf-account.toml ]]; then
    mkdir -p "$WORKDIR/backups"
    cp wgcf-account.toml "$WORKDIR/backups/wgcf-account.$(date -u +%Y%m%dT%H%M%SZ).toml"
  fi
}

validate_current_license() {
  local existing
  existing="$(current_license)"
  if [[ -z "$existing" ]]; then
    return 1
  fi
  wgcf update --license-key "$existing" >/tmp/warp-current-license.log 2>&1
}

activate_warp() {
  wgcf generate >/tmp/warp-generate.log 2>&1 || {
    emit "{\"status\":\"install_failed\",\"license_step_reached\":true,\"reason\":\"wgcf generate failed\"}"
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
}

status_only() {
  install_prerequisites
  mkdir -p "$WORKDIR"
  cd "$WORKDIR"
  local existing tail valid
  existing="$(current_license)"
  tail="$(license_tail "$existing")"
  valid="false"
  if [[ -n "$existing" ]] && wgcf update --license-key "$existing" >/tmp/warp-status-license.log 2>&1; then
    valid="true"
  fi
  emit "{\"status\":\"account_status\",\"account_present\":$([[ -f wgcf-account.toml ]] && echo true || echo false),\"valid\":${valid},\"license_tail\":\"${tail}\",\"traffic_available\":false,\"traffic_remaining\":null,\"reason\":\"wgcf does not expose a stable remaining-traffic field for this account\"}"
}

restore_latest_backup() {
  install_prerequisites
  mkdir -p "$WORKDIR"
  cd "$WORKDIR"
  local latest existing tail valid
  latest="$(ls -1t "$WORKDIR"/backups/wgcf-account.*.toml 2>/dev/null | head -n1 || true)"
  if [[ -z "$latest" ]]; then
    emit "{\"status\":\"restore_failed\",\"account_present\":$([[ -f wgcf-account.toml ]] && echo true || echo false),\"valid\":false,\"reason\":\"no wgcf-account backup found\"}"
    exit 0
  fi
  cp "$latest" wgcf-account.toml
  existing="$(current_license)"
  tail="$(license_tail "$existing")"
  valid="false"
  if [[ -n "$existing" ]] && validate_current_license; then
    valid="true"
    activate_warp
  fi
  emit "{\"status\":\"account_restored\",\"account_present\":true,\"valid\":${valid},\"license_tail\":\"${tail}\",\"traffic_available\":false,\"traffic_remaining\":null,\"reason\":\"restored latest wgcf-account.toml backup and checked current license\"}"
}

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
    --status-only)
      STATUS_ONLY="true"
      shift
      ;;
    --restore-latest-backup)
      RESTORE_LATEST_BACKUP="true"
      shift
      ;;
    *)
      emit "{\"status\":\"install_failed\",\"license_step_reached\":false,\"reason\":\"unknown argument: $1\"}"
      exit 0
      ;;
  esac
done

if [[ "$STATUS_ONLY" == "true" ]]; then
  status_only
  exit 0
fi

if [[ "$RESTORE_LATEST_BACKUP" == "true" ]]; then
  restore_latest_backup
  exit 0
fi

if [[ -z "$LICENSE_KEY" ]]; then
  emit "{\"status\":\"license_failed\",\"license_step_reached\":true,\"reason\":\"missing license key\"}"
  exit 0
fi

install_prerequisites
ensure_account

existing="$(current_license)"
existing_tail="$(license_tail "$existing")"
if validate_current_license; then
  activate_warp
  emit "{\"status\":\"license_applied\",\"license_step_reached\":false,\"candidate_key_tested\":false,\"current_license_tail\":\"${existing_tail}\",\"reason\":\"existing WARP license is valid; retained current account\"}"
  exit 0
fi

backup_account
if wgcf update --license-key "$LICENSE_KEY" >/tmp/warp-license.log 2>&1; then
  activate_warp
  emit "{\"status\":\"license_applied\",\"license_step_reached\":true,\"candidate_key_tested\":true,\"previous_license_tail\":\"${existing_tail}\",\"reason\":\"WARP+ license applied\"}"
else
  emit "{\"status\":\"license_failed\",\"license_step_reached\":true,\"candidate_key_tested\":true,\"previous_license_tail\":\"${existing_tail}\",\"reason\":\"wgcf rejected license\"}"
fi
