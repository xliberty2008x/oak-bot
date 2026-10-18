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
