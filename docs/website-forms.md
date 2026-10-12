# Dynamic website forms

**Acceptance status, 2026-10-11:** owner Google and TikTok attempts on the earlier
deployment failed. Subsequent corrections add a reusable inner composition loop,
with real Jev now privately configured and evaluated. Owner login acceptance remains outstanding. The
[investigation](agentic-website-forms-research.md) separates reproduced defects,
corrections and measured evidence. A loaded first screen or passing unit suite is
not successful login.

`oak_website_form({"url":"https://example.com/login"})` pauses the original
agent tool request and opens a human form in the existing Mini App. The private
browser reads the website's current visible controls, creates bounded fields and
buttons, and inspects the next page after submission. A domain-specific template
is not required. Supported controls include text, email, password, verification
code, a single select and checkbox, with up to 16 fields. Field and option IDs are
opaque; labels are text, never provider HTML. Passwords/codes never enter the
agent's tool arguments, conversation, gateway database or Hub.

The owner sees the exact HTTPS destination before starting. Generic forms permit
only that credential/navigation origin, using public DNS resolved and pinned
before Chromium starts. TikTok has an optional, default-off two-host
[static-resource policy](website-resource-policy-proposal.md); it does not permit
external credential submissions or SSO. Multiple unrelated forms require the
optional composer. Radio/file/multiselect controls, GET forms carrying input in the
URL, CAPTCHAs, passkeys and unrecognized challenges can require manual desktop
control. The adapter reports these limitations instead of claiming completion.
The original native agent waits for the final result. When privately configured,
the [Jev helper](website-component-library.md) selects the relevant observed group
after each changed page, and ordinary code compiles the existing widgets.
Provider verification and browser actions remain broker-controlled. It does not
guarantee that every website accepts browser automation.

A form with absent HTML `method`, `action` and `target` attributes can expose
JavaScript-managed controls, including identifier-only steps. It must have at
least one active supported control; Jev still selects its relevance. Before inserting any input, Oak installs document
CSP `form-action 'none'`: native form submission, including direct `form.submit()`,
is blocked while JavaScript fetch/XHR continues under the existing network
policy. If the browser actually blocks native submission, the next read reports
an unsupported layout rather than redisplaying an apparently working form. Only
a boolean outcome is retained; a blocked URI can contain private input and is
never recorded. Explicit GET forms remain unsupported. The CSP lasts until navigation;
ordinary native POST forms appearing later in the same document are unavailable.
This prevents a native GET fallback from placing credentials in the URL without
rewriting the site's request or replaying events. The CSP does not prevent site
JavaScript from putting data into same-origin requests or navigation; existing
origin restrictions continue to apply. It does not prove successful authentication.

`oak_request_input` still provides the `google_sign_in` and `instagram_sign_in`
shortcuts. Their adapters recognize known provider stages and require positive
authenticated-page evidence. The generic adapter can use that evidence on
recognized provider origins too. An unknown website always needs the owner's
explicit **Підтвердити завершення** action on a fresh noncredential state.
This action becomes available only after an accepted submission containing
field values. An initial empty page or an empty chooser action cannot enable it.
`owner_confirmed` is distinct from `authenticated`; a redirect, cleared password
field or HTTP 200 is never sufficient verification.

After completion the model receives only status, destination and `request_id`.
It can continue the same task using
`oak_account_browser({"provider":"website","request_id":"…","action":"read"})`.
The private browser remains in its broker; no cookies, profile, browser debugging
connection, script evaluation or file access is exported. Actions are bounded to
the selected site, and click/type retain Oak's owner approval flow. Account-security
and login destinations stay unavailable to this model-facing adapter. An echoed
password/code prevents content capture. After password/code submission the model
uses checked text and controls; screenshots are disabled because pixels cannot
be checked for a secret atomically with arbitrary website scripts. Click/type
accept only fresh control handles returned by the broker. Access ends with the original turn or
after 15 minutes, whichever comes first. Login does not authorize publishing,
deletion, payment or unrelated account actions.

Tickets are one-use and expire after 30 seconds. A form expires after five
minutes, an idle form after two minutes, and loss of gateway/human heartbeats
closes its context within 45 seconds (bounded in-flight cleanup can take longer).
Closing the completed iframe revokes only its human token; cancelling the request
or ending the original task closes the browser. Interrupted/uncertain operations
are not replayed. Tokens exist only in memory and authorization headers, never
URLs or browser storage. Failed cleanup remains explicitly uncertain.

## Deployment

Provision and verify the [execution boundary](execution-boundary.md) first. Every
model-executed command and browser/computer/media/research adapter must run under
the dedicated executor UID. Keep the gateway, broker and executor UIDs separate.
The native runtime continues to use the deployment's own ChatGPT subscription and
`gpt-6.1-sol`. The existing desktop profile can be moved while stopped using the
explicit migration option; do not discard a desktop login during an upgrade.

Provide an immutable shared Python environment with Oak dependencies and a
Chromium installation outside private account homes. Render a broker specification:

```json
{
  "origin": "https://forms.example.com",
  "parent_origin": "https://oak.example.com",
  "gateway_user": "oak",
  "broker_user": "oak-login",
  "broker_uid": 1901,
  "broker_gid": 1901,
  "python": "/opt/oak-runtime/venv/bin/python",
  "code_root": "/opt/oak-executor/releases/REVIEWED_RELEASE",
  "browsers": "/opt/oak-runtime/browsers",
  "execution": {"profile": "/etc/oak-executor/profile.json"}
}
```

Run `python3 scripts/setup-website-forms.py --spec /private/broker.json --output
/private/broker-bundle` to review the config and systemd unit. Add `--apply` as
root to install. The service binds its public human endpoint only to
`127.0.0.1:18766` and uses `/run/oak-website-forms/control.sock` for the gateway.

Optional `static_resources` configures exact page origins, exact resource hosts
and nonempty lists of `script`, `stylesheet` or `font` types. Absent/empty adds
no destinations; only a matching generic website lease receives the policy.
For example, `{"https://login.example.com":{"assets.example.com":["script","stylesheet"]}}`.
These are frontend-download permissions, not form, navigation, SSO or API
permissions. The existing `tiktok_static_resources` boolean remains separately
scoped. Review the [transport and inactive proposal](website-resource-policy-proposal.md)
before activating an additional origin's policy. Named provider shortcuts do not
receive generic static policies.

Configure a separate HTTPS reverse proxy origin to that loopback port, with
request/access logging disabled and no body capture. The gateway socket checks
the peer UID in addition to request/owner/chat/turn/runtime/session binding.

Add these keys to the existing private gateway configuration, preserving all
other settings and durable state:

```json
{
  "execution": {"profile": "/etc/oak-executor/profile.json"},
  "web": {
    "website_login": {
      "socket": "/run/oak-website-forms/control.sock",
      "origin": "https://forms.example.com"
    }
  }
}
```

`web.website_login` fails closed without the current-boot execution receipt.
The build digest and static `credential_isolation_verified: false` field are not
host acceptance. Availability is checked against the installed policy and broker
at request time. The broker's systemd unit does not log provider browser errors
or credential-bearing requests. Validate the actual unit/UID/mount/network policy,
native subscription runtime, file transfer, desktop control, TLS iframe and a
real owner form before recording deployment acceptance. DOM fixtures and public
GET-only checks do not prove real Google/Instagram account authentication.
