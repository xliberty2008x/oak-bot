# Execution boundary for private website sign-in

Real sign-in is disabled until the gateway's `boundary_check(execution)` succeeds.
Enabling a configuration flag does not establish isolation. The current-boot
receipt must be written by the root attestation service after both actual socket
launchers pass their dummy-resource probes. Unit tests and a rendered bundle do
not count as host acceptance or Google/Instagram login acceptance.

The gateway and private sign-in broker retain their trusted state. A dedicated
account runs Codex app-server and every model-facing browser, computer, media and
research adapter. systemd launches one fixed, root-owned program per accepted Unix
socket connection. The gateway can choose bounded operations or the fixed
`codex app-server --listen stdio://` command with configuration overrides; it cannot
provide a privileged command. There is no sudo rule or privileged Docker group.
The native launcher forces the existing `gpt-6.1-sol`, OpenAI provider and ChatGPT
subscription authentication, with API-key variables absent.

Bubblewrap provides a separate user, PID, mount, IPC and UTS namespace. It binds
reviewed code and explicitly selected dependencies read-only, mounts a fresh proc
and temporary directory, and exposes only the existing workspace/runtime home as
writable data. The worker additionally receives its own desktop state; the native
runtime receives no display cookie or gateway/broker state. No profile is copied
from another account. Existing runtime/workspace contents stay in place; targeted
ACLs permit the executor to traverse their parents and use these exact roots.
Default ACLs retain gateway access where file creation modes permit it; gateway
file delivery still uses the explicit worker snapshot protocol. Desktop state is
owned by the executor and has no gateway ACL.

Networking uses the host network namespace with an nftables output policy matching
only the dedicated executor UID. This is **not network namespace isolation**.
The policy rejects loopback, private, link-local and other non-public ranges, local
FIB destinations and explicitly configured public NAT addresses of the host.
Only TCP/UDP port 53 to the explicit resolver addresses precedes these denials.
Other UIDs and Docker rules are untouched. The firewall transaction modifies only
the `inet oak_executor` table and is required by both socket services. Rules remain
installed when the service stops. Abstract Unix sockets remain part of the shared
network namespace; do not expose an unauthenticated trusted abstract socket.
Trusted filesystem sockets are outside the mounted filesystem.

## Prepare and review

The setup script renders the reviewed `oak/` source and static assets (excluding
bytecode caches) by default and requires a separate explicit
`--apply` invocation as root to install them. It does not install packages. Required
host tools are systemd, bubblewrap, nftables, ACL utilities and the existing Oak
Python/Codex/Node/browser/media dependencies. The installer reserves a previously
unused execution UID/GID, rejects supplementary groups, and refuses policy changes
while processes already run under that UID. Review the generated policy and units
before applying them. Stop only executor connections for a later upgrade; never
replace policy underneath active execution.

A specification contains deployment paths, never passwords or account contents:

```json
{
  "source": "/srv/oak-reviewed-source",
  "executor_user": "oak-exec",
  "executor_uid": 1900,
  "executor_gid": 1900,
  "gateway_user": "oak",
  "workspace": "/srv/oak/workspace",
  "runtime_home": "/srv/oak/runtime",
  "desktop_state": "/srv/oak/executor-desktop",
  "python": "/opt/oak-python/bin/python",
  "codex": "/opt/oak-codex/bin/codex",
  "path": "/opt/oak-python/bin:/opt/oak-codex/bin:/usr/local/bin:/usr/bin:/bin",
  "readonly": ["/usr", "/bin", "/lib", "/lib64", "/opt/oak-python", "/opt/oak-codex", "/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf", "/etc/ld.so.cache", "/etc/ssl/certs"],
  "dns_addresses": ["127.0.0.53"],
  "host_addresses": ["203.0.113.10"],
  "forbidden": ["/srv/oak/state", "/srv/oak/broker", "/srv/oak/config.json"],
  "display": ":90",
  "width": 1280,
  "height": 800
}
```

Replace all example paths, IDs and addresses. Include every symlink target needed
by the private Python environment, Codex wrapper and Node installation in the
read-only roots. Add `playwright_browsers_path` and its exact read-only root for an
existing Chromium installation. Optional `models` uses Oak's existing
`piper_model`, `vosk_model`, `whisper_model` names; model directories must also be
mounted read-only. Do not mount whole homes, `/etc`, the gateway repository, or a
directory containing trusted configuration, broker profiles or delivery state.
Explicit `forbidden` entries must cover every such deployment path. They are
checked for overlap with all writable and read-only mounts. The setup rejects
untrusted source symlinks; review the source checkout before rendering it.

```sh
python3 scripts/setup-execution-boundary.py --spec /private/execution-spec.json --output /private/execution-bundle
```

For installation use a new output directory with the same reviewed specification
and add `--apply` as root. The script writes an immutable release under
`/opt/oak-executor/releases`, `/etc/oak-executor/profile.json`, its egress policy,
and `oak-executor-{native,worker}.socket` plus matching instance services,
`oak-executor-firewall.service`, and `oak-executor-attest.service`.
It enables the sockets, firewall and attestation for boot. A failed attestation
leaves the receipt absent, so private sign-in and executor clients fail closed.
Fix the underlying cause and start the attestation service again. No provider
credentials are needed for these dummy-resource checks.

The receipt binds the boot ID, executor UID, immutable source/unit/firewall hashes
and profile digest. Probes use a root-owned dummy marker, a private filesystem
socket, listeners on loopback and assigned IPv4 interfaces, and the attester's
host PID. They verify UID, no effective capabilities, no-new-privileges, private
PID namespace, read-only code and denied access to those resources. The generated
network policy additionally covers IPv6 and explicit public NAT addresses; the
probe does not claim an external public-NAT round trip or live provider access.
Actual reboot recovery must be checked separately after deployment.

## Preserve this deployment's existing desktop login

When an existing deployment changes execution UID, also set
`migrate_data_ownership: true` and the explicit `gateway_state` directory (which
must be listed in `forbidden`). Codex chmods its own runtime/workspace data;
write ACLs alone cannot authorize those chmods. With the gateway stopped, setup
acquires its gateway lock, validates every data tree for unsafe hardlinks/special
files, then changes only workspace/runtime ownership without following symlinks
or modifying file contents. The gateway's durable databases and private state
remain outside these roots. Take a private deployment backup before cutover.
Never change live runtime ownership underneath the existing gateway process.

An owner may already have signed in using the existing managed desktop. For that
case only, set `migrate_desktop_from` to this deployment's existing
`state_dir/desktop`, and select a separate `desktop_state` destination. Stop the
gateway through its supervisor before applying the bundle. The setup acquires
both the source `gateway.lock` and `desktop/desktop.lock`; an active deployment
causes migration to fail. The destination `desktop_state/desktop` must be absent.
It moves the directory on the same filesystem, preserves file contents, assigns
ownership to the executor and removes other users' access. No cookies, database
contents, filenames or passwords are opened or logged. No entire state directory
is mounted, and no other deployment's browser profile is copied.

Remove `migrate_desktop_from` for subsequent installations. If a later setup step
fails, the moved profile remains at its destination for recovery; it is never
deleted or silently replaced. Validate desktop startup and have the owner check
their existing website session after cutover. Profile preservation alone cannot
promise that a provider accepts the session or that an OS keyring remains usable
after changing the UID. Private form-based login continues to use its independent
broker context and never exports that broker profile to the executor.

## Gateway integration contract

Set `execution = {"profile": "/etc/oak-executor/profile.json"}`. Use
`ExecutionClient(execution)` with asynchronous `start`, `call(op, args)` and
`close`. `runtime_command(execution, original_argv)` returns the local transport
command for RuntimeClient's subprocess. `boundary_check(execution)` is the
private broker capability gate; no receipt means false. There is no shared-UID
fallback after an execution error. Worker exceptions return their class only.
Control messages are at most 1 MiB; entire JSON lines are at most 16 MiB.

Move all `media.*`, `research.*`, `browser.run/close`, and
`computer.run/type/key/status` operations into the worker, including direct voice
transcription outside tool dispatch. The worker lazily owns `ManagedDesktop` and
its `ComputerTools`; do not also start a gateway desktop. In a managed deployment,
`browser.run` borrows that same headed Chromium context, launched through a private
Playwright pipe with Chromium's sandbox enabled. It reuses only this deployment's
existing desktop profile. No CDP listener or new network authority is needed.
`browser.close` resets the borrowed adapter; the desktop owns browser shutdown.
Private login browsers
stay in the broker and never transfer cookies, profiles or display access here.
`computer.status` only reads X11 geometry using this deployment's mounted cookie;
it never starts a desktop or takes its lock. A separate doctor worker can inspect
the running executor desktop through the existing X11 abstract socket. Before
the first desktop or browser action starts that lazy desktop, status reports it unavailable.

The gateway retains remote-control authorization, owner/session binding and task
pausing. `remote.start` returns `{id}`; `remote.read({id})` returns
`{data:base64,eof}` with a one-second poll and 64 KiB maximum;
`remote.write({id,data})` accepts 64 KiB; `remote.close({id})` ends that lease.
`remote.type({id,text})` and `remote.key({id,key})` bind text/keyboard input to
that active lease and return no screenshot.
Only one lease exists per worker. Computer input and all `browser.run` actions,
including observations, are refused while it is active.
The worker starts fixed x11vnc over a socket pair, so no public VNC listener exists.

Never reopen a returned worker/model pathname in the privileged gateway. Snapshot
outgoing data into a gateway-private artifact using these streaming operations:

| Operation | Input | Result |
| --- | --- | --- |
| `file.open_read` | `path`, optional `namespace` | `handle`, `size` |
| `file.read_chunk` | `handle` | `data` (base64), `eof` |
| `file.open_write` | `path` | `handle` |
| `file.write_chunk` | `handle`, `data` (base64) | cumulative `size` |
| `file.finish_write` | `handle` | `path`, `size` |
| `file.close` | `handle` | discards unfinished writes/closes reads |

Chunks are at most 64 KiB, with eight opaque handles and a 2 GiB per-file limit.
Reads retain a file descriptor and check device/inode, size, timestamps and link
count before/after chunks; path components use `O_NOFOLLOW`. Writes retain a
directory descriptor, stage privately and rename atomically. `namespace` is only
`workspace` (default) or `generated_images`; the latter reads exclusively from
`runtime_home/generated_images`, never arbitrary runtime authentication files.
Incoming trusted Telegram files can be read by the gateway and streamed to these
worker writes. Outgoing Telegram/web deliveries must use the completed private
snapshot, including deferred deliveries. `file.read/write` remain conveniences
for snapshots up to 8 MiB.

Calls are serialized. Cancellation closes the transport; the worker exits and
systemd/private PID namespace reap its children. No timed-out parser thread is
reused. A worker reconnect requires rebuilding adapter state; gateway integration
must handle that explicitly instead of executing adapters in-process.
