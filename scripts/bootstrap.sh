#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
umask 077
install_browser=false
install_voice=false
check_computer=false
for option in "$@"; do
  case "$option" in
    --browser) install_browser=true ;;
    --voice) install_voice=true ;;
    --computer) check_computer=true ;;
    --help|-h)
      printf '%s\n' 'Usage: ./scripts/bootstrap.sh [--browser] [--voice] [--computer]' 'Create the Oak environment, install dependencies and check account/model access.'
      exit 0 ;;
    *) printf '%s\n' "Unknown option: $option" >&2; exit 2 ;;
  esac
done
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
if ! command -v codex >/dev/null 2>&1; then
  printf '%s\n' 'Install the runtime dependency: npm install -g @openai/codex@0.159.2' >&2
  exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  printf '%s\n' 'Install FFmpeg with your system package manager, then run bootstrap again.' >&2
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
if [[ "$check_computer" == true ]]; then
  if ! command -v xdotool >/dev/null 2>&1 || ! command -v xmodmap >/dev/null 2>&1; then
    printf '%s\n' 'Install xdotool and xmodmap with your system package manager, then run bootstrap again.' >&2
    exit 1
  fi
  .venv/bin/python -c 'from PIL import features; assert features.check_feature("xcb"), "Pillow requires XCB screen capture support"'
  printf '%s\n' 'Enable computer.enabled and set computer.display to your running local X11 session in config.local.json.'
fi
if [[ "$install_browser" == true ]]; then
  .venv/bin/python -m playwright install chromium
fi
if [[ "$install_voice" == true ]]; then
  .venv/bin/python scripts/setup-voice.py
fi
mkdir -p .state/workspace
if [[ ! -f config.local.json ]]; then
  cp config.example.json config.local.json
fi
printf '%s\n' 'Checking the deployment subscription login. Use the runtime_home from config.local.json for codex login --device-auth.'
.venv/bin/python -m oak doctor --config config.local.json
printf '%s\n' 'Next: configure config.local.json and save the Telegram token with scripts/set_telegram_token.py.'
printf '%s\n' 'Verify: .venv/bin/python -m oak smoke --config config.local.json'
printf '%s\n' 'Run: .venv/bin/python -m oak run --config config.local.json'
if [[ "$install_browser" != true ]]; then
  printf '%s\n' 'Optional browser: .venv/bin/python -m playwright install chromium'
fi
