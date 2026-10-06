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
- Reuse a selected deployment's config, workspace, memory and account directory.
  Do not overwrite them to make a rerun appear clean. Do not copy desktop/runtime
  credentials or browser profiles from another agent or VM.
- Obtain only missing inputs: Telegram owner ID, expected bot username, private
  token file and the account owner's ChatGPT sign-in. Use hidden terminal input
  or a secure secret field for the token, never chat, a shell argument or Git.
- Establish that this VM is the intended poller before using a bot already
  deployed elsewhere. A successful `getMe` and empty webhook cannot prove another
  machine is not polling. Stop the previous deployment as part of an authorized
  migration; do not silently run both.

## Run the bootstrap

Run the full installer when the requested deployment is authorized:

```bash
./scripts/bootstrap-vm.sh \
  --owner-id YOUR_NUMERIC_USER_ID \
  --bot-username YOUR_BOT \
  --token-file /absolute/path/to/private-token
```

Use `--config` to resume a selected deployment. If sign-in or bot details are not
yet available, use `--prepare-only` to install and check the local machine, then
resume full mode when the missing inputs arrive. Preparation is not a live bot.
The runtime uses isolated ChatGPT subscription authentication and exact
`gpt-6.1-sol`; do not add API billing or substitute a model when unavailable.

The installer owns OS packages, Python/runtime dependencies, managed desktop,
browser, voice configuration, service and gateway setup. Inspect a failing step
and fix its cause; do not create a second manual poller or tunnel as a workaround.
For a supplied stable `--public-url`, verify its HTTPS proxy reaches the selected
local port. Otherwise the managed temporary tunnel must be described as temporary.

## Finish the running application

Check the guide's readiness conditions against the actual deployment: account
and model, desktop, native model smoke, one ready service, HTTPS panel, owner auth
and Telegram menu. Keep local checks, model calls and user-device checks distinct.
Have the owner open the current Mini App from Telegram and send a task; do not
claim their client works from a server-only HTTP check.

Only configure additional provider OAuth/plugins when requested, using their
own login flow. An integration inventory does not establish connected access.
Report the private config path, service commands, public endpoint's stability,
checks actually completed and remaining user sign-ins. Never return tokens,
standalone access keys or signed Telegram launch data in logs or the report.
