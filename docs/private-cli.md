# Private CLI authentication

Native `exec_command` sessions are not a credential channel. Oak cannot adopt a
native session ID, PID, terminal, argv, environment or working directory into a
private session. Opening an X11 terminal does not remove native output or cache
access. No display authorization or execution isolation is bypassed.

The optional CLI channel starts a fixed, reviewed provider adapter in the private
broker from the outset. The broker owns input, output and a private account cache;
the gateway and model receive only scoped lifecycle status and bounded verified
authentication/GPU metadata. Raw CLI output and OAuth links are human-only.

Authority binds owner, selected conversation, thread, turn, runtime epoch and the
panel session. The owner explicitly opens the private form and submits the code
there. Codes never enter ordinary requests, chat, AG-UI, screenshots, logs or
SQLite. Losing an acknowledgement never replays process creation or human input.
Expiry, cancellation, turn changes and restart revoke the lease and reap its
process group. Authentication is provider evidence, not an exit code or a checkbox.

The channel is unavailable unless its separate-origin service, fixed provider and
current execution boundary are provisioned. Its private page uses `/cli/` on the
configured private origin; a deployment may route this prefix to a separate
backend on its existing trusted private origin. No additional DNS name is assumed.
An unavailable response must not suggest a nonexistent terminal or ask for a code
in conversation. Live Google login is a separate owner action; offline fixtures
do not establish real account authentication or Colab GPU availability.

GPU metadata is a fixed supported-type inventory from the pinned official CLI
(`T4`, `L4`, `G4`, `H100`, `A100`), not a quota or capacity promise.
`availability: unknown` is explicit even after a successful authenticated read.
An empty inventory never means the account has no GPUs. Authentication never
allocates a runtime, attaches a GPU, or consumes compute to test availability.

## Fixed provider installation

The adapter uses the official [Colab CLI source](https://github.com/googlecolab/google-colab-cli/tree/006ff96ac2d0e4dcfb702467a76493d687929be8),
revision `006ff96ac2d0e4dcfb702467a76493d687929be8`, in a separate Python 3.12
virtual environment. Provision it as root outside the executor's read-only or
writable mounts. Never reuse a model-writable CLI installation or account HOME.

After reviewing and installing that revision and its dependencies, seal the
complete installed file inventory with the root-only helper:

```sh
sudo python3 scripts/seal-cli-provider.py \
  --python /opt/oak-private-cli/colab-006ff96ac/bin/python \
  --output /etc/oak-cli/provider.json
```

The helper records digests and the upstream commit without importing the provider,
requesting credentials or starting a process. It refuses writable dependencies,
unexpected external symlinks, global site packages, a different source commit and
an existing output file. The service validates that exact inventory before it
becomes available; later installation or permission changes revoke readiness.
The command is fixed to the sealed Python, `-I`, and the immutable
`oak/colab_auth.py` adapter. Neither model fields nor broker configuration accept
arbitrary commands. Python symlinks must resolve to root-owned immutable files.

The adapter follows the pinned official OAuth2/cache functions, replacing only
their human prompt with the private broker protocol. A successful read-only
Colab account metadata request establishes authentication; its private response
is discarded. Native CLI processes never receive the broker's token cache.

## Standalone private service

Use the same immutable release as the current execution profile. The standalone
entry point is `python -I /opt/oak-executor/releases/REVIEWED_RELEASE/oak/cli_server.py
--config /etc/oak-cli/broker.json`. Run it as the dedicated private broker UID,
never as root, the gateway UID or the executor UID. A root-owned configuration
contains only reviewed deployment paths and origins:

```json
{
  "origin": "https://private.example.com",
  "parent_origin": "https://oak.example.com",
  "gateway_uid": 1000,
  "broker_uid": 1901,
  "socket": "/run/oak-cli/control.sock",
  "port": 18767,
  "execution": {"profile": "/etc/oak-executor/profile.json"},
  "state_dir": "/var/lib/oak-cli",
  "provider_manifest": "/etc/oak-cli/provider.json"
}
```

The state directory is broker-owned mode 0700; the socket directory is
broker-owned mode 0750 with the gateway's group. The socket itself is 0660 and
also verifies the connecting gateway UID. Keep configuration, state, socket and
provider installation outside every executor mount. Paths must be canonical.
The service listens only on loopback, disables HTTP access logs, locks its
dedicated state, removes owned orphan caches and invalidates old session receipts.
Neither startup nor a capability check starts a provider process.

Provision a systemd unit with `User` set to that broker, `Group` set to the gateway
group, `UMask=0077`, `KillMode=control-group`, `NoNewPrivileges=yes`,
`ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes` and
`ProtectProc=invisible`. Use `StateDirectory=oak-cli` with mode 0700 and
`RuntimeDirectory=oak-cli` with mode 0750; allow writes only to those two
directories. Order startup after `oak-executor-attest.service`. Reap the entire
old service group before restart; then orphan-cache cleanup can safely remove
secret state.

### Stable reciprocal filesystem masks

When the CLI and website brokers share a host, mask each broker's configuration,
state and control socket inside the other's mount namespace. Both runtime
directories must exist **before either service starts**. An optional
`InaccessiblePaths=-/run/...` silently skips an absent directory; creating that
directory later can expose the peer's control socket to the existing namespace.

Install the following root-owned, non-writable-by-others file as
`/etc/tmpfiles.d/oak-private-brokers.conf`. Replace `BROKER_USER` and
`GATEWAY_GROUP` with the installed units' approved `User` and `Group`; use each
broker's own user if they differ. The final `-` sets no age-based cleanup.

```text
d /run/oak-cli 0750 BROKER_USER GATEWAY_GROUP -
d /run/oak-website-forms 0750 BROKER_USER GATEWAY_GROUP -
```

Add this drop-in to **both** `oak-cli.service` and `oak-website-forms.service`,
keeping each unit's existing `RuntimeDirectory` and `RuntimeDirectoryMode`:

```ini
[Unit]
After=systemd-tmpfiles-setup.service

[Service]
RuntimeDirectoryPreserve=yes
```

The CLI unit must include:

```ini
[Service]
InaccessiblePaths=-/etc/oak-website-forms -/var/lib/oak-website-forms /run/oak-website-forms
```

The website unit must include:

```ini
[Service]
InaccessiblePaths=-/etc/oak-cli -/var/lib/oak-cli /run/oak-cli -/opt/oak-private-cli
```

The reciprocal `/run` entries deliberately have **no `-` prefix**: a missing peer
root must prevent startup. Keep other existing restrictions. `yes` preserves
directory identities across both explicit stops and restarts; `restart` does
not cover stop followed by start. Reboot clears `/run`, and tmpfiles recreates
both roots before either namespace is assembled. These are the documented
[systemd directory-lifecycle semantics](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#RuntimeDirectoryPreserve=).

For installation or repair, stop the gateway and both brokers after checking
that work and private leases are idle. Install the reviewed files, run
`systemd-tmpfiles --create /etc/tmpfiles.d/oak-private-brokers.conf`, reload the
units and verify their syntax before starting either broker. Confirm both roots
are canonical directories with the expected owner, group and mode. Do not remove
or clean either runtime root while a peer namespace remains active.

Before restarting the gateway, enter each broker's actual mount namespace as
its service UID and confirm the peer directories cannot be opened. Stop and
start each broker separately while the other stays running; verify the runtime
directory inode is preserved and both directions still deny access. Then check
the status-only CLI health route and static assets without creating a lease.
Provider-process, cache and authentication-receipt counts must remain zero for
an unused installation. If these checks fail, keep the optional CLI service
inactive while recovering the gateway and website service; do not restore a
known incomplete mask as an enabled CLI capability.

On the existing private HTTPS origin, proxy `/cli/` to its loopback port while
preserving the prefix. Disable access logs and request/response buffering for
that location, and set a 32 KiB request/body-buffer bound. The private page's
frame-ancestor policy permits only the configured parent origin. The main
gateway's existing `web` configuration adds only:

```json
"cli_login": {"socket": "/run/oak-cli/control.sock", "origin": "https://private.example.com"}
```

Validate the unit, nginx configuration, immutable provider manifest and
current-boot execution attestation before restarting the selected gateway.
Rollback code/configuration while retaining current durable action receipts.
`oak_cli_auth` requires an owner-requested connection and an explicit owner click
in the private form. Its verification session grants no generic authenticated
shell; cache cleanup follows task/session revocation and has a 15-minute maximum.
