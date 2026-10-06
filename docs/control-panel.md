# Oak control panel

The Mini App manages Oak while conversation stays in Telegram. Its five views
show owned Telegram sessions, current state, the most recent 100 scheduled tasks,
installed runtime integrations, deployment settings and computer-use permission. `/new`,
task creation, memory contents and approval answers remain in Telegram.

In Settings, the owner can confirm a model choice for the selected session.
The list comes from the existing runtime. The next request uses the saved model
and preserves history and memory; General and other topics are unchanged.
Changes are blocked during accepted work. Catalogue availability does not prove
subscription access, and a failed model request never silently chooses another
model. The overview counts all active scheduled tasks and finds the next pending
run independently of the bounded task list.

The Settings switch grants or revokes the owner's computer-use permission.
It persists across restarts and takes effect without starting a new conversation.
Turning it off blocks new desktop actions, cancels that owner's outstanding
computer action, and interrupts the related model turn. Already completed actions
remain completed. A desktop must first be configured by the operator; the panel
distinguishes an unconfigured desktop and unavailable status from permission OFF.
The switch shows the last confirmed state while changing permission, then reloads
the observed server state after success or failure.

Task controls use the existing scheduler and controller. Cancellation removes
future scheduled execution; it does not interrupt a turn already running. Stop
targets the current owner's Telegram turn and reports that interruption was
requested until the runtime confirms the final state. Task detail shows only
lifecycle status, without chat messages or tool content. Task text, memory text,
persona instructions, account information and private filesystem paths are
excluded from the new panel responses.

Inventory uses the existing isolated runtime. A loaded Telegram session is used
when available; otherwise the UI identifies the result as runtime-wide.
Manual refresh requests a fresh installed-app snapshot. Enabled, authorized,
connected and callable states are separate. Unsupported inventory methods show
unavailable; partial plugin inventory is identified. No install/uninstall APIs
are called. [Protocol sources and fields](control-panel-runtime.md)

App management opens the runtime's validated native ChatGPT settings URL after
owner confirmation. The owner completes any connection there, then refreshes
Oak to inspect the observed state. A native settings page can use a different
browser account; opening it does not establish connection to Oak's account.

MCP OAuth starts only for a server advertised as needing OAuth, after owner
confirmation. A remote or Telegram client requires an operator-configured
reachable callback and fixed native listener port. For example, a private config
can supply these supported runtime options after its ingress is prepared:

```json
{
  "runtime_config": {
    "mcp_oauth_callback_url": "https://oak.example/callback",
    "mcp_oauth_callback_port": 5555
  },
  "web": {
    "oauth_callback_ready": true
  }
}
```

Merge these fields with the deployment's existing settings. The operator must
forward the callback path and query to the native listener without logging query
strings, and verify provider registration requirements. Oak does not create that
ingress, change Telegram configuration, or rewrite OAuth state/redirects.
Direct browser-key sessions on the runtime host may use native loopback callbacks
only when the operator sets `web.oauth_loopback_ready: true`. Public/tunnel
deployments and forwarded requests do not qualify. The operator must confirm
that the browser runs on the runtime machine; a localhost Host header alone
cannot establish that.

Login request state is durable; authorization URLs stay transient. Pending or
uncertain requests are not repeated for ten minutes, including after restart.
The panel reports observed authorization and runtime connection states. It does
not consume native OAuth completion notifications or claim success from opening
a link. The runtime retains OAuth credentials; Oak neither reads nor copies them.

Existing owner-scoped event/chat HTTP endpoints remain for compatibility, but
the control panel never subscribes to them or displays a second chat. Telegram
signed launch validation, allowlisting, private browser keys, HttpOnly cookies,
memory-only Bearer tokens, origin checks and safe DOM rendering are retained.

## Operator retest

Run the existing unittest suite in an environment that permits local sockets:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Use a separate local port and synthetic state for browser review. Check Telegram
launch and browser-key sign-in, mobile navigation, task details/cancellation,
stop request status, computer-use ON/OFF and pending/failed requests,
unavailable/partial inventories, and returned native links.
Test a real OAuth callback on a separately authorized deployment; schema/unit
checks do not establish provider access. Session-selection limitations and
native-topic routing behavior are in
[Telegram session research](session-switching-research.md).
