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
