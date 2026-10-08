---
name: oak-bootstrap
description: Provision a fresh Linux VM and deploy Oak with its managed desktop, computer use, voice, Telegram gateway and Mini App; also resume an incomplete deployment.
---

# Bootstrap Oak

Use this repository's `scripts/bootstrap-vm.sh` to provision and deploy Oak.
Read [the bootstrap guide](../../../docs/bootstrap.md) for supported systems,
flags and the readiness checklist. Resolve commands from the repository root.
This Markdown workflow is usable by other agents without a skill loader.

## Establish the deployment

- Inspect the OS, current user, existing Oak config and service before changing
  the machine. The supported starting point is Ubuntu 24.04 or Debian 12/13 amd64,
  a non-root user with sudo, and network access. Keep the clone at a persistent path.
- Select a reviewed release/commit containing all required features. Do not
  remove the platform guard to claim support for Ubuntu 22.04 ARM64. Preserve
  upstream local Bot API preparation, cache and authorized migration hooks.
- Reuse a selected deployment's config, workspace, memory and account directory.
  Do not overwrite them to make a rerun appear clean. Do not copy desktop/runtime
  credentials or browser profiles from another agent or VM.
- Obtain only missing inputs: Telegram owner ID, expected bot username, private
  token file, Telegram application `api_id`/`api_hash` and the account owner's
  ChatGPT sign-in. Keep application credentials in a mode-600 environment file
  outside Git using `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`. Use hidden terminal
  input or a secure secret field, never chat, shell arguments or Git.
- Establish that this VM is the intended poller before using a bot already
  deployed elsewhere. A successful `getMe` and empty webhook cannot prove another
  machine is not polling. Stop the previous deployment as part of an authorized
  migration; do not silently run both.
- Full bootstrap provisions the official local Telegram Bot API by default for
  large attachments in every conversation. Reuse only explicitly available app
  credentials, never another account session. See
  [large-file setup](../../../docs/configuration.md#large-telegram-files).

## Run the bootstrap

Run the full installer when the requested deployment is authorized:

```bash
./scripts/bootstrap-vm.sh \
  --owner-id YOUR_NUMERIC_USER_ID \
  --bot-username YOUR_BOT \
  --token-file /absolute/path/to/private-token \
  --telegram-api-env-file /absolute/path/to/private-telegram-api.env
```

Use `--config` to resume a selected deployment. If sign-in or bot details are not
yet available, use `--prepare-only` to install and check the local machine, then
resume full mode when the missing inputs arrive. Preparation is not a live bot.
It installs Docker and builds the pinned Bot API image without requesting
credentials or starting its server or poller. Full mode uses `telegram-api.env`
beside the config unless `--telegram-api-env-file` selects another private file;
if absent, interactive bootstrap requests application credentials through hidden
terminal input. The default local API port is 8081; `--telegram-api-port` overrides
it, while omission preserves an existing configured local endpoint.

Protocol feature checks run before account access. Add `--verify-broker` for
local Chromium ordinary forms and synthetic login/OTP/cancel. On an already
managed machine, `./scripts/bootstrap.sh --features-only --verify-broker` verifies
without a deployment config, account or live bot. Do not add fixture broker URLs
or sockets to a deployment config or present this test as external sign-in.
The runtime uses isolated ChatGPT subscription authentication and exact
`gpt-6.1-sol`; do not add API billing or substitute a model when unavailable.

The installer owns OS packages, Python/runtime dependencies, managed desktop,
browser, voice configuration, local Telegram Bot API, service and gateway setup.
It reuses local Docker access or `sudo` without granting Docker group access, keeps the
Bot API cache outside Git, binds the server to loopback and disables Docker logs.
The server uses `unless-stopped` restart policy. For a Docker daemon outside
Oak's container, use `--telegram-api-host-directory` only to select the host bind
source; the server and Oak must see the mounted directory at the same absolute
path. If the network requires it, put `OAK_TELEGRAM_MTPROTO_PORT=5222` in the
private environment file. Inspect a failing step and fix its cause; do not create
a second manual poller or tunnel as a workaround.
For a supplied stable `--public-url`, verify its HTTPS proxy reaches the selected
local port. Otherwise the managed temporary tunnel must be described as temporary.

An already running deployment is check-only. For a legacy cloud-to-local
migration, finish pending work, stop Oak and back up its private config and state,
then rerun with `--config` and `--migrate-telegram-api`. Bootstrap verifies cloud
identity and webhook ownership, saves its migration checkpoint, calls cloud
`logOut` once and verifies the local endpoint before updating config and starting
Oak. Resume that checkpoint after interruption; never repeat `logOut` manually,
drop pending updates or reuse the cloud cursor for the independent local queue.
The gateway binds its cursor to the selected API endpoint and preserves it on
ordinary restarts.

## Finish the running application

Check the guide's readiness conditions against the actual deployment: account
and model, desktop, native model smoke, one ready service, HTTPS panel, owner auth
and Telegram menu. Keep local checks, model calls and user-device checks distinct.
Verify a real Telegram attachment larger than 20 MB; a built image or local file
copy does not prove Telegram media downloads work. Record a real VM reboot only
if it was actually performed and verified.

Verify the protected feature/build manifest against the selected checkout and
record the nonsecret runtime epoch. A running-service rerun is check-only;
`local_feature_checks: null` is not local test success. Record an existing Codex
CLI version and its smoke result, rather than silently replacing the CLI.
The machine packages include `x11vnc`; noVNC is pinned in the repository. Check
the panel's remote desktop availability and an authenticated connection on the
configured display. Remote control uses the existing HTTPS gateway and must not
open a public VNC port or borrow another deployment's browser/account state.
Have the owner open the current Mini App from Telegram and send a task; do not
claim their client works from a server-only HTTP check.
The shared desktop is not a secure credential channel. Synthetic tests leave
Instagram disabled; production broker activation requires separately verified
OS isolation and explicit provider/account/session authorization. Neither a code
digest nor a second process is proof of isolation. Do not save ongoing access.

Only configure additional provider OAuth/plugins when requested, using their
own login flow. An integration inventory does not establish connected access.
Report the private config path, service commands, public endpoint's stability,
checks actually completed and remaining user sign-ins. Never return tokens,
standalone access keys or signed Telegram launch data in logs or the report.
