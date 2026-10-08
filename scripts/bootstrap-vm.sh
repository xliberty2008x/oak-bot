#!/usr/bin/env bash
# Run as the deployment user; only package/service installation uses sudo.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
for arg in "$@"; do
  if [[ $arg == --check-platform ]]; then
    command -v python3 >/dev/null 2>&1 || { printf '%s\n' 'Platform check needs an existing Python 3 interpreter; nothing was installed.' >&2; exit 1; }
    exec python3 scripts/bootstrap_vm.py "$@"
  fi
done
umask 077
if [[ $(id -u) == 0 ]]; then
  printf '%s\n' 'Run bootstrap as a normal deployment user with sudo access, not root.' >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  command -v apt-get >/dev/null || { printf '%s\n' 'Ubuntu/Debian with apt is required.' >&2; exit 1; }
  sudo apt-get update
  sudo apt-get install -y python3
fi
exec python3 scripts/bootstrap_vm.py "$@"
