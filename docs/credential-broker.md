# Credential broker: implemented scope and remaining deployment gate

The local implementation is a working **synthetic-only** broker. It is not a
secure Instagram login deployment. Instagram capability remains false before
any ticket, browser context or credential UI can be created. No configuration
flag can enable a real provider. Separate ports/processes and a private Unix
socket are protocol separation, not proof of isolation from model tools.

## Implemented

- `oak/signin_broker.py`: independent human and private control applications,
  one-use 30-second attachment, owner/panel/task/turn/runtime binding, absolute
  300-second login limit, 120-second idle limit and 10-second heartbeat deadline.
- Human data stays in the broker process/browser and its own-origin view.
  Control DTOs contain only scoped lifecycle metadata. Tickets are not stored
  in Oak SQLite or durable events and are not included in URLs.
- Nonpersistent Chromium is restricted to the exact loopback fixture origin
  and three fixed paths. Every human input validates navigation epoch and
  provider frame/form destinations. No selectors, script, external URLs,
  profile/CDP/cookie export, downloads, recordings or disk screenshots are exposed.
- `oak/signin.py`: durable dispatch intent before IPC, no replay after unknown
  dispatch, owner-session revalidation and trusted status projection to the
  original native request. No new `turn/start` is created. A sent RPC is not
  proof that the runtime consumed it; transport failure remains uncertain.
- Cleanup is explicit (`waiting/confirmed/uncertain`). Cancellation during
  close keeps a tracked resource. Browser destruction precedes successful
  synthetic completion. Browser state is never handed to `oak_browser` or
  `oak_computer`; this first adapter returns status only.
- The existing Mini App request dialog/design is reused. A sandboxed frame
  shows a separate, clearly labelled test provider; no Instagram password
  fields or user-claimed authenticated result were added to A2UI.

Run locally with temporary test state and a separately installed test Chromium:

```sh
PLAYWRIGHT_BROWSERS_PATH=/tmp/oak-broker-browsers \
  python scripts/input-request-demo.py --broker --port 18783
```

The fixture uses fixed public dummy credentials shown on the fake provider.
Never enter account credentials. All services bind to `127.0.0.1`; the control
server binds only to a new mode-600 Unix socket inside a private temp directory.
The demo starts no Codex process, Telegram poller or external provider page.

## Gate before an Instagram implementation can run

The user selected ORACLE-VM. A read-only SSH check through the existing route
`ubuntu@130.61.215.238` with strict existing host-key trust returned
`Connection refused` on port 22. Current OS/process/service identity could not
be established. Historical Ubuntu ARM64 identity is not a current verification.
The separate Kira gateway at `/home/ubuntu/telegram-gateway-runtime` is not Oak
and must not be modified or reused as broker state.

The next deployment review must cover these concrete changes before applying
them: separate runtime UID and fixed launcher; immutable trusted UI/policy;
trusted gateway/broker state outside runtime mounts; mount/PID/network
isolation; no broker/CDP/display/socket access from runtime; no host sudo,
Docker/DBus or process-memory access. Every model-invoked backend adapter must
execute in the untrusted realm or enforce an equivalent policy. The runtime
sandbox does not cover adapters executing in the gateway host process.

A trusted supervisor must validate the actual launcher/process identity and
run immutable adversarial probes under that same launcher with dummy markers.
Evidence must bind deployment identity and runtime epoch; missing, stale or
changed evidence closes capability. A user-supplied JSON `isolated=true` or
self-report from a model shell is not evidence.

Only after that gate may an Instagram adapter navigate to a fixed approved
HTTPS origin. It must verify real committed origin/TLS and credential form
destinations before every input, suspend on navigation/challenge, and verify
authentication through a tested trusted provider adapter. User confirmation,
absence of a password field or existence of a cookie cannot establish success.
Provider compatibility and challenges still require a separately authorized
human login test. Do not bypass CAPTCHA or substitute professional API login
for website login.

The login secret plane must have no ordinary body/error/access logging,
analytics, saved frames/HAR/video, secret argv/environment/URLs, password
saving or profile export. Ephemeral storage/core/swap policy must be reviewed
on the actual Linux host; local mock tests do not prove forensic erasure.

No post-login account actions are currently granted. A future mediated adapter
requires an explicit bounded operation scope. Do not expose arbitrary
authenticated GUI, JavaScript, DOM, cookie/profile/CDP or raw screenshots to
the model. Credential saving, persistent cookies and ongoing account access
remain separate decisions, excluded from this implementation.

## Current-main integration requirements

Read-only pinned comparisons against PR16 main
`d3d8ba9b871955a665299ed1de28d5e748f81b8f` and PR17 main
`8e7f86bab6016fcc2cf521499218ceb57f67e634` confirmed integration work remains.
No merge/rebase was performed. Before integration:

1. Keep proactive A2UI from PR16, but use native InputRequest for required data
   that must resume the current request; authentication never uses A2UI fields.
2. Centralize launch URLs and strip all reserved keys
   `conversation/surface/request`; emit one authoritative target.
3. Preserve both surface launch and authenticated request-scope resolution.
   Do not switch A2UI focus/views under an open request dialog.
4. Retain main's 2000-character link fallback limit/button-label validation,
   publish-to-Telegram form links and streamed-placeholder fixes.
5. Run shared general/topic launch, stale mixed target, session switch,
   placeholder, fallback and original-native-response regression tests.
6. Accept PR17's optional Telegram `api_url/local_directory`, streaming
   downloads, path checks and bounded file previews while preserving input
   notification and lifecycle hooks. Keep the cloud endpoint as the default;
   this feature does not authorize activating or migrating a local Bot API.
7. Keep broker configuration and state separate from Telegram import roots:
   `telegram_api_directory`, `state_dir/inbox` and workspace. Those paths can
   expose files to model tools and must never contain a browser profile or
   trusted broker state.

Pinned upstream comparison:
[PR17 changes](https://github.com/xliberty2008x/oak-bot/compare/d3d8ba9b871955a665299ed1de28d5e748f81b8f...8e7f86bab6016fcc2cf521499218ceb57f67e634).

## Validation gates

Synthetic tests cover owner and exact origin, replay, ticket expiry, rejected
credential/success payloads on ordinary endpoints, navigation epoch, cancel,
idle/heartbeat expiry, slow startup, close cancellation, owner revocation,
uncertain dispatch/native delivery and secret canaries excluded from SQLite,
durable events and native output. The Chromium UI check is a separate local
fixture check, not live Telegram or Instagram verification.

Local validation on 2026-10-08: all 20 broker tests passed. The real Chromium
fixture check completed login plus OTP and owner cancellation; each returned
to its original native RPC, browser cleanup was confirmed and no new
`turn/start` was sent. Ignored evidence is in `output/playwright/`: an empty
login view, terminal view and a status-only JSON report. No entered credential
frame was saved. Both JavaScript files passed `node --check`.

The full default suite ran 128 tests with one pre-existing tunnel reconnect
timeout and one optional Pillow skip. The tunnel test passed alone (0.016s).
A diagnostic full run then passed all 128 tests with the same Pillow skip
(12.141s): only the `asyncio` warning logger was set to `ERROR` to avoid slow
debug-warning output; assertions and asyncio debug checks were unchanged.
Do not report the original default run as green, or substitute these local
results for an actual ORACLE-VM isolation or Telegram/Instagram acceptance test.

OS tests must additionally deny runtime/adapter reads of trusted state and
process memory, profile/CDP/display access, trusted UI/launcher mutation,
owner authority forgery, internal endpoint/DNS redirect access and cleanup
escape through process cancellation. Only booleans and deployment identity
belong in their report, never marker contents or actual secrets.

Official basis: [Telegram Mini Apps](https://core.telegram.org/bots/webapps),
[OAuth security BCP](https://www.rfc-editor.org/rfc/rfc9700.html),
[native OAuth external user agents](https://www.rfc-editor.org/rfc/rfc8252.html).
These OAuth rules do not establish Instagram website remote-browser support
or a cookie bridge from a user's phone.
