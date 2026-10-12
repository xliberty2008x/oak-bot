# Oak UI design and reuse contract

Read this before changing the Mini App, forms, UI dependencies, catalogues or
renderers. This is the canonical UI contract. Source files define the actual
implementation; update this document when its contract changes. The broader
runtime architecture is in [docs/design.md](docs/design.md).

## CURRENT — deployed agentic workflow and acceptance limits

**Owner authentication through the dynamic form remains unverified.** The
deployed workflow delegates through MCP to Jev and returns unsupported steps
to the native agent within the same private workspace. The owner's later report
confirmed an email field after 1–2 minutes. Production traces identified extra
model round trips, delayed DOM hydration and a rejected native-owner association.
The deployed recovery changes and their measured limitations are recorded in the
[latency investigation](docs/evaluations/website-agent-2026-10-12/production-latency.md).
Jev still abstains on the tested OpenRouter entry page; GPT fallback presents its
email step. Earlier homepage/clarification failures and direct `/sign-in` probes
remain separate historical evidence. Distinguish source checks, real model
judgments, live form discovery, owner interaction and successful authentication.
See the [failure investigation](docs/agentic-website-forms-research.md) and
[component contract](docs/website-component-library.md).

The owner subsequently reported a failed second step: the private UI exposed
only completion confirmation, while the model had received an action-only
scene. The older invalid-identifier check did not cover that valid-account
transition. The surface and continuation correction was deployed as `12f97ef`;
the latest owner Google attempt still failed at the next step. Deployment,
anonymous rendering and owner authentication remain separate results, recorded
in the [audit implementation report](docs/evaluations/website-agent-2026-10-12/audit-implementation.md).

### Complete observed task surfaces (catalogue 2.1)

One selected native form cannot represent an email form and sibling sign-in
methods. The source now introduces observed surface references: the active
dialog, or bounded native form/semantic container candidates when there is no
dialog. The agent selects the relevant observed surface. The compiler requires
all supported controls and every observed action in that surface, including
unavailable choices. The surface can contain several native form owners.
Unsupported, disabled/read-only or excess controls count as omitted controls
and produce an explicit partial-form notice. They cannot pass a complete-surface
acceptance check. This is semantic
reconstruction using private widgets, not a pixel copy or execution of arbitrary
generated HTML in the credential iframe.

`TextInput` and `EmailInput` express a semantic component choice, not authority
to change the observed native text/email validation or length bound. Both
legacy and surface compilers retain a native text field as text even if the
agent selects EmailInput, and retain a native email field as email if it selects
TextInput. This prevents an email-or-username field from acquiring an invented
email-only validator or 254-character limit. The existing protected password
and one-time-code classifications remain independently enforced.

Each action has its own `field_refs`, validated against its actual native
ownership and dispatch kind. A native submit retains required-field checks;
links bind no fields, and an observed method button may bind none. The private
renderer submits only the clicked action's fields, and the broker independently
checks that exact binding before the DOM transaction fills and clicks once.
Unrelated drafts never become values for an alternative method. Policy-denied
methods stay visible and disabled with a visible explanation; displaying them
grants no new destination.
Agentic leases negotiate `website-v2` with up to 32 controls and 64 actions.
Legacy `website-v1` keeps its original single-owner contract.
Its composer observation omits surface membership, including during historical
anchor clarification, so it cannot emit `website-v2` action bindings into the
older renderer. Inference, cached plans and freshness checks use that same
legacy projection; agentic `website-v2` observations retain observed surfaces.

The main agent may supply a bounded public `goal` to `oak_website_task`. The
gateway passes it through the MCP start operation and binds it once to the
lease's planner. Omitting it preserves existing-account sign-in. It describes
intent, grants no additional authority, and must contain no human values or
private account facts. Status, receipts and shared events do not echo it.
Changing the objective cannot replace a running task or reset its model budget.
The planner chooses among complete observed surfaces semantically; it does not
silently choose the smallest native-form ancestor. Unique authoritative modal
scope and unambiguous component types need no additional semantic judgment.

Missing fields never create agentic completion confirmation. Method choosers
continue through planning, and temporary empty pages remain bounded unresolved
states. The agentic path no longer offers generic owner confirmation based on
absence of controls; trusted provider verification remains independent. Legacy
explicit owner confirmation retains its distinct `owner_confirmed` outcome.

A separate fixed private action, “Завершити й передати сесію Oak”, becomes
available after an accepted human action. It never replaces observed controls
or classifies a transitional page as complete. The human may explicitly finish
the task through a value-free private endpoint bound to the latest presentation
revision. The broker retains the session as `owner_confirmed`, not
`authenticated`; only a provider adapter can establish the latter. No model
plan can create this capability, and drafts are cleared without entering the
completion request or shared state. Stale completion refreshes without replay.
Recognized blocked and challenge states suppress this completion action, even
after an earlier accepted human step. An accepted click alone cannot make a
later denial look complete.

The private blocked-state message distinguishes an Oak origin-policy denial,
an unsupported popup closed by Oak, a remote HTTP error, recognized rejection
text on the page, a failed main-document connection, and an Oak request-handler
failure. Unknown causes use neutral copy. These are fixed private messages;
no URL, response body, exception text or new model observation field is exposed.
An Oak-denied navigation keeps its local cause when its aborted request later
reports a network failure. The renderer accepts only these exact fixed blocked
messages. A JavaScript button can reveal its destination only when clicked;
being currently available does not authorize a new origin or predict success.
Legacy fixed-origin adapters keep cross-origin links unavailable. The agentic
adapter may present secure same-window links for the private consent flow below.
Popups remain closed and there is no automatic back-navigation or implicit
OAuth permission.

### Private approval of a new website origin

An agentic lease retains its original task destination while separately tracking
the origin of the committed main document. A new HTTPS main-document GET/HEAD
pauses before its network request. The private page displays the exact normalized
origin and a fixed consent control outside the model-generated form. Only the
human can approve that server-held transition, once, using its current private
revision. `/api/approve-origin` accepts only `request_id` and `revision`; neither
the client nor the model supplies a URL or a grant. OAuth paths, query strings,
cookies and form values remain private. Waiting is projected to the model as
`device_approval` with an empty `wait` scene and an opaque revision.

After consent, all DNS answers must be public and the selected numeric address
is pinned for this lease. Full broker authority and expiry are checked again
after DNS and immediately before releasing the paused request. A bounded,
authenticated per-lease CONNECT tunnel preserves the browser's end-to-end TLS,
cookie isolation and redirect semantics. CDP Fetch intercepts every document
hop, including redirects that Playwright routing does not revisit. There is
no direct-network fallback, TLS verification override or request-body replay.
The original same-origin native POST requires one private dispatch admission;
a definite stale rejection revokes it. Redirected POST requests are denied.

Surface forms support a bounded native Select of up to 256 options; legacy
forms retain 32. Option captions and native option values stay private, and
models receive only an option count. The unchanged 64 KiB scene budget applies
across the complete scene. This supports ordinary longer lists without dropping
their controls or inventing site-specific widgets; larger lists remain explicit
omissions.

At most four document origins, including the root, can be approved. Consent
waits at most 120 seconds and never beyond lease authority. The committed
document origin changes only after navigation commits; old plans and client
drafts are discarded. Every new agentic private snapshot includes
`current_origin`, which identifies the actual credential recipient. Once the
client receives that field, it rejects a later omission. Existing anonymous
resource quotas and the irreversible input freeze survive origin changes;
the configured authentication-host relay stays bound to the original document.
Redirected subresources are conservatively denied, and popup, iframe, non-HTTPS
and cross-origin native POST login paths remain unsupported.

Owner completion remains unavailable while on an identity-service origin.
After return to the root and successful connected-browser checks, trusted
promotion retains only the root tunnel for at most 900 seconds. This is a
single explicit lifetime change, capped at 1,200 seconds from construction;
expired transports cannot be revived. Identity-service tunnels are revoked,
traffic quotas remain cumulative, and no further origin consent is available
in the connected session. Ordinary root-scoped content POSTs remain possible;
redirected POST bodies remain denied. Closing the lease cancels held approvals,
background startup navigation and owned tunnels without waiting for idle expiry.

Implementation and bounded verification are recorded in
[the private-origin evaluation](docs/evaluations/website-agent-2026-10-12/private-origin-approval.md).

The requirement is **one reusable form engine**: inspect the current page,
compose a private UI from individual observed controls and a fixed component
library, execute the human's chosen action once, then observe the next state.
The old selector chose an entire group already assembled by code; it could not
use controls omitted by that collector. Removing all page text after input also
removed unfamiliar next-step labels and errors. The current surface contract
addresses these unmet needs without site-specific login templates. The official
renderer migration has a separate compatibility gate; the private value channel
remains independent of ordinary A2UI state.

### Native agent workspace

`oak_website_agent` keeps the existing subscription-backed ChatGPT runtime in
the loop. `begin` creates a task-bound Mini App workspace and returns immediately.
Opening the private iframe attaches an anonymous browser. `observe` returns a
sanitized scene and the component catalogue; `act` executes one grounded,
value-free navigation action before private input. The agent reasons over the
result and calls `present` with a catalogue plan. After the human submits once,
the same native tool returns the next sanitized observation to the agent.
`wait` continues waiting within the same workspace when no human step has yet
arrived. It also returns when an actionable scene becomes available without a
pending human plan, even if the human event sequence has not changed. An
unchanged displayed human form continues waiting for the owner. This uses the
existing runtime and model, not a second planner thread
or an API-billed fallback. Jev remains available on the legacy whole-login path
but is not a mandatory gate for native-agent navigation or composition.

The trusted `/agent` Unix route accepts only observe, act and present operations
on explicitly agentic leases. The model sees an opaque revision, stage, bounded
scene and `{event_seq, outcome}` progress. It receives no input values, cookies,
option values, HTML, screenshots or arbitrary selectors. The existing scene
compiler validates the model's plan, preserving native form ownership, required
controls and password/code widget types. Invalid proposals remain recoverable;
they do not destroy the human workspace. Only the private renderer can submit
human values. After private input, actions are presented to the human rather
than dispatched autonomously from potentially filled fields.
For value-free navigation, `form: null` means that a control has no native form
association. Unrelated loose or hidden inputs do not become associated with a
standalone navigation button merely because both have no form owner. Actual
native associations, including external `form=` attributes, remain enforced.

After an accepted value-free action, the adapter first reads the resulting
page. While its stage is `agent_working` or `unsupported_layout`, an unchanged
sanitized scene/progress or a changed scene with no controls and no actions
allows at most two additional seconds polling reads every 100 ms under the same
browser and lease locks. This also covers a temporary empty DOM before hydration.
A revision increment alone does not establish hydration; intermediate changes
do not reset the deadline. Changed nonempty evidence and trusted blocked,
challenge, authenticated or human-wait stages return immediately. The deadline returns the latest complete
observation for normal revision validation on the next step. No navigation or
model request is repeated. Close, cancellation and the private-input boundary
stop the readiness wait; cancellation after dispatch prevents another autonomous
action. This absorbs delayed DOM changes without an extra parent-model turn.
In legacy plans, secondary actions retain the anchor's native form owner. In
surface plans, each action's own native form owner controls its `field_refs`;
null is not a wildcard. A recoverable `stale_form` or `invalid_plan`
response includes one fresh, validated observation when available, with matching
human progress and the original error. This is a bounded read under the same
lease lock, never a replay of the rejected action. The agent revises from that
evidence; an explicit observe is needed only when the refresh is unavailable.
Expiry, cancellation, boundary failure and invalid refresh data retain cleanup
and terminal-state behavior. The [production latency investigation](docs/evaluations/website-agent-2026-10-12/production-latency.md)
separates the measured cause, source regression tests and unmeasured live impact.

Observation respects inactive `inert`/`aria-hidden` ancestors, including shadow
hosts, and scopes candidates to the active modal dialog before applying scene
limits. Focus chooses among visible modal dialogs, with the last visible modal
as fallback. Scope is recomputed during freshness checks and contributes to the
native revision. Default-value redaction still examines controls throughout the
document; a modal cannot hide private defaults from the masking pass.

The persistent iframe shows `agent_working` between plans, keeps cancellation
and polling available, and clears submitted secret widgets. It does not ask the
owner to reopen a new request for each step. Its presentation revision changes
when the agent publishes a plan even if the native DOM did not change; only the
current private revision maps back to the native action. Stable polls preserve
drafts. `present` and `wait` keep the native call pending through one human step,
bounded by the remaining workspace lifetime (at most 300 seconds).
A server-owned workspace authority
is bound to owner, chat, thread, turn and runtime epoch. Restart and turn end
revoke it. Native action IDs are reserved durably before dispatch; uncertain or
duplicate actions are not replayed. Terminal workspace delivery is
`not_required` because the original `begin` RPC already returned; subsequent
native calls receive the outcome. Trusted provider verification remains separate
from the model's interpretation. Legacy inline confirmation and the distinct
agentic private finish action both preserve the `owner_confirmed` outcome.
An action-only page after accepted private input can still be an intermediate
step. The native agent may present its observed actions as a zero-field private
form and continue to the next input step. The absence of fields does not establish
authentication or force the workflow to end. Before a plan is presented,
`agent_working` human snapshots remain free of fields and actions. An action-only
scene is planned and shown for human choice; an empty transitional scene is
reobserved within a bounded readiness window. Neither automatically proposes
completion on an agentic lease; the separate private finish action remains an
explicit human decision.

The candidate native-agent adapter supports public frontend script, stylesheet
and font discovery through an explicit `anonymous_static_resources` boolean,
disabled by default. Production activation requires explicit owner approval for
the broader executable-script origins; existing approved static and
authentication-host scopes remain in force meanwhile. Anonymous fetching omits
cookies, authorization and referrer, but a downloaded script executes in the
website page, survives into later input steps and can read that page's DOM.
Credential-free downloading therefore does not make new script origins harmless.
The owner explicitly approved this capability on 2026-10-12, and the deployed
broker now enables it. The portable default remains false. This approval covers
anonymous scripts, styles and fonts; authentication destinations remain separately
scoped. Activation does not establish that a site's navigation or login works.
Discovery is bounded to eight public DNS-pinned hosts, 512 requests across the
lease's documents, 24 MiB total and 4 MiB per resource, with the existing
concurrency, redirect and timeout limits. Identity, gzip, zlib/raw deflate and
Brotli responses are decoded in bounded chunks; both encoded and decoded bodies
retain the 4 MiB resource and 24 MiB lease caps. `Brotli==1.2.0` supplies bounded
Brotli decoding. Encoding support adds no origin, header or credential permission.
The legacy exact-host relay retains
its 96-request cap. Private diagnostics expose only counts, including request
quota rejections, without host names, URLs, headers or content. Query-bearing URLs, API calls and
navigation gain no permission. Before any nonempty human values mapping,
including rejected or stale input, the relay freezes to exact URLs whose
anonymous downloads already completed. This capability does not extend
credential destinations or replace the separate authentication-host policy.
Legacy mode and native-agent mode without the opt-in retain explicit static
host configuration. Form continuation and readiness fixes work independently
of this loading option.

The observation limits below remain explicit. An agent can reason over the
observed controls but cannot act on an omitted or inaccessible element. This
change does not establish universal site compatibility or completed owner login.

### Delegated Jev task

`oak_website_task` accepts one public website URL and retains the original native
call while the private workspace is open. The gateway injects its existing
task-bound lease into an MCP initialization; the model never supplies lease
authority. The broker exposes MCP 2025-11-25 initialization, tools/list and
tools/call on its authenticated Unix control socket only. The human origin has
no MCP endpoint. A session exposes one idempotent task start, status and cancel;
it does not expose arbitrary browser actions or credential input.

`WebsiteTask` owns the observe → Jev decision → guarded navigation/presentation
loop. Each lease has a fresh StepPlanner with at most twelve actual paid calls;
a navigation decision uses one Choice request, while presentation may use a
second batch for components. Inference occurs outside the browser lock. Actual
actions still validate the lease, current revision and native form ownership.
Polling an unchanged scene or waiting for human input makes no model request.
An unchanged observation without a human form has a ten-second readiness
deadline; expiry returns the same workspace to the native agent without another
paid request or repeated action. Displayed forms, device approval and optional
owner confirmation retain their normal human waiting period.
After a private human submission, the same task observes and composes the next
step without another main-model round trip. After private input, only the human
can execute a presented action. No provider-specific route or selector is added.

Unsupported evidence, an invalid proposal or exhausted budget returns control
to the native agent in the same workspace. This is explicit escalation, not
successful authentication. A failed/uncertain action is never replayed. Stopping
a delegation preserves the browser when safe; cancellation during an in-flight
browser operation may conservatively close the lease. Turn end, expiry and
private cancellation still revoke the entire browser authority. Provider
verification and explicit owner confirmation remain separate outcomes.

The native escalation result includes only fixed `delegation_steps` and
`delegation_calls` counts alongside the existing status. A planner's unsupported
proposal reports `no_candidate`; a trusted blocked/challenge stage retains
`unsupported`. This distinguishes model abstention from a browser boundary
without recording inference payloads. The returned scene is still a fresh
post-escalation observation, not a stored copy of the planner's earlier input.

The MCP result contains only fixed task states, progress and counts. Sanitized
observations reach Jev inside the broker; passwords, codes and chosen option
values retain the existing private channel. Per-request latency and cost
diagnostics contain enums and numbers, not page contents or model payloads.
No CopilotKit frontend or dependency is installed by this change. The
[comparison](docs/evaluations/website-agent-2026-10-12/latency-comparison.md)
records actual GPT/Jev timings and retained failures. Candidate integration
tests, deployed behavior and owner authentication are separate evidence.

Both paths use catalog `urn:oak:private-website-ui` **2.0.0**:
32 controls, 64 actions, 24 heading/help/error entries and a 64 KiB scene. On the
legacy whole-login path, Jev
first selects an observed permitted action, then selects a compatible component
or omission per eligible field, secondary actions and an observed message.
These are at most two dependent Choice batches, not generated HTML or prose.
The second batch contains only component/action/message questions; if none
remain, the selected anchor compiles after one request. A redundant second
approval of the sign-in step was removed after it rejected a chooser already
selected by the first batch. That first failure remains evidence, and a rerun
is a regression rather than a fresh held-out result.
The [official autoformat cookbook](https://docs.typesafe.ai/cookbooks/autoformat)
and [function-calling cookbook](https://docs.typesafe.ai/cookbooks/function_calling)
provide the typed-judgment and deterministic-assembly patterns. Jev accepts
text/JSON evidence; this implementation does not add vision or another model.

The six components are TextInput, EmailInput, PasswordInput, CodeInput, Select
and Checkbox. Native form ownership is an execution constraint, not semantic
grouping. All observed required fields of a non-null anchor form must be
included; unsupported required native controls make its action impermissible.
Passwords and OTPs cannot be downgraded. Output remains at most 16 fields and
8 actions. A disabled action can be selected when fields may enable it, but
actual dispatch rechecks its enabled state. Link actions never fill fields.
Only grounded references and enums are accepted; labels/messages are copied
from the sanitized observation. The plan cannot assert authentication.
Optional standard autocomplete roles retain compatible semantic hints; optional
history contains at most three successfully accepted human-chosen action labels
and field-type lists. History includes no values or model reasoning. Both are
evidence for interpreting the next step, not widget properties or proof of login.

V2 retains bounded, redacted field/action labels and visible headings, help and
errors **after input**. The browser excludes known human values, native selected
option values/captions, emails, URLs and opaque tokens before projection. A
private `private_redactions` operation retains chosen native option values and
captions in broker memory across navigation, never in model data or logs.
Destination, options, input values, cookies, keys, raw body text,
HTML and screenshots are not model evidence. Redaction is bounded and cannot
identify every private sentence; structural validation is not secret detection.
Page words remain untrusted evidence, never instructions or execution authority.
This replaces V1's pre-input-only `public_context` policy in the candidate.
An independent Python sanitizer masks broker-known values in scene text and
history before validation/projection, including common encodings and truncated
known-value prefixes. A page cannot defeat that pass by replacing JavaScript
string methods. It does not identify unknown private facts; native-default
inspection and page-text filtering remain bounded, separate safeguards.

Each actual Jev request reserves one of at most twelve per-lease calls before
networking, including the second batch and failed/uncertain attempts. Known
cost and unknown-cost calls remain separate; this is not a global spending
budget. Same-revision reads cache both success and failure. The trusted Unix
`/diagnostics` route uses peer and full lease-binding checks and exposes only
bounded in-memory counters, enum/status/model/timing and cost metadata. It
contains no page text, input, URLs, response bodies or credentials. A separate
authentication-host relay remains inactive without an explicit exact-origin/host
grant. The owner approved OpenRouter to `clerk.openrouter.ai` (Hub 5222), and
that exact deployed grant is active. Public DNS pins, CORS/preflight checks, bounded same-host script
redirects and private cookie handling limit that network capability. It does not
replace provider verification or supply generalized authentication proof.

The [independent V1 baseline](docs/evaluations/website-forms-2026-10-11/README.md)
at `71f966e` exposed fields on **0/12** sites with Jev unconfigured. Later real
structural fixture runs matched 3/4, then 7/8, then 8/8 after prompt changes.
Those fixtures became regressions; they did not establish unseen-site support.
A real Google identifier group still received an abstention with structural-only
state, motivating pre-input semantic labels. The deployed semantic collector
later exposed forms on **7/24** sites: 4/12 fresh and 3/12 regression. All scopes
confirmed cleanup without account input. Missing controls, blocked assets,
access-error pages and model judgments remain separate failure classes.
[Real-model and page receipts](docs/evaluations/website-forms-real-jev-2026-10-12/README.md)
retain those results. The later Google reserved-identifier negative control
accepted one dispatch and returned `provider_blocked`, not authentication.

The V2 catalog/planner/sanitizer has passed 39 focused no-network tests with mocked Jev
answers. A separate real public-page probe of the initial V2 snapshot, before
removing the duplicate composition question, exposed fields on **3/6** pages:
GitHub, Netflix and Spotify; Slack had an unsupported layout, Amazon was blocked
and Wikipedia failed at startup. All six scopes closed without input or actions;
only the three composed pages invoked Jev, for six API calls total. This used
an isolated source snapshot, not the production broker or owner accounts.
[V2 receipts](docs/evaluations/website-scene-v2-2026-10-12/README.md) document the
scope and code digest. Actual multi-step owner login remains unverified.
Acceptance still requires held-out pages with unfamiliar labels, validation
failure, conditional steps, navigation and DOM replacement, followed by
separate actual owner-login verification. Named Google/Instagram identity,
network policy and positive authentication evidence remain separate adapters.
Universal reuse is limited to supported DOM controls, permitted resources and
sufficient sanitized evidence; it does not promise automatic CAPTCHA/passkey
handling, canvas-only controls, inaccessible frames or arbitrary cross-origin SSO.

Legacy static frontend dependencies use an explicit exact-origin to exact-host/type
configuration, independent of form selection. No additional legacy host grant is
active by default. The transport retains public DNS pins, cookie-free bounded downloads
and a monotonic freeze of anonymously fetched URLs before any human values
mapping, including rejected/stale input. It does not extend navigation, API or
credential destinations. The separately approved TikTok boolean retains its
scope. The [inactive six-origin proposal](docs/evaluations/website-static-resources-2026-10-12/README.md)
is a network experiment, not evidence that forms or login work on those sites.

The source candidate uses **`oak-a2ui-lit@1.1.0`**, a restricted adapter around
published **`@a2ui/lit@0.13.0` and `@a2ui/web_core@0.13.0`**, for ordinary A2UI
cards. The wire remains **A2UI `v0.9.1`** and the canonical catalogue ID is unchanged.
The official processor, surface, keyed rendering and binding replace the old
custom tree/data-model implementation. Compatibility entries retain literal text,
native single-select and Oak's label/button variants and add bounded display
components; Card, Column, Row and TextField reuse upstream implementations. Private website values retain
their separate trusted renderer and broker channel. This source gate does not
establish production rollout or actual Telegram WebView/provider acceptance.
[Gate, retained failures and build provenance](docs/evaluations/a2ui-lit-2026-10-12/README.md).

| Layer | Responsibility and source |
| --- | --- |
| Telegram and Mini App | Conversation remains in Telegram. The authenticated Mini App supplies control-panel views and contextual forms. [telegram.py](oak/telegram.py), [web.py](oak/web.py), [index.html](oak/web/index.html), [app.js](oak/web/app.js). |
| AG-UI events | Runtime text/tool/lifecycle events and registered `CUSTOM` payloads; durable routing, replay and SSE/polling. An event protocol, not a widget renderer. [events.py](oak/events.py), [bus.py](oak/bus.py), [runtime.py](oak/runtime.py). Oak implements its emitted subset, not the full AG-UI RunAgentInput API. |
| A2UI protocol/schema | Declarative create/update/delete operations and actions. Official pinned wire schemas and Oak's restricted catalogue validate different parts of the contract. [a2ui.py](oak/a2ui.py), [a2ui-catalog.json](oak/web/a2ui-catalog.json). |
| A2UI rendering | Official web-core processor/binding and Lit surface/keyed rendering behind a restricted Oak catalogue/theme adapter. Authoritative snapshots are still polled and converted into canonical envelopes; no new SSE transport. [a2ui.js](oak/web/a2ui.js), [adapter](frontend/a2ui-runtime.js). |
| Ordinary contextual forms | Server-owned non-secret forms completing the same native request; shared widgets with a distinct transport from A2UI cards. [requests.py](oak/requests.py), [requests.js](oak/web/requests.js), [interaction.py](oak/interaction.py). |
| Shared native widgets | Label/input/select/checkbox construction, without value storage, event handlers, network or persistence. Callers own schemas and permissions. [form-controls.js](oak/web/form-controls.js). |
| Private composition library | `urn:oak:private-website-ui` 2.0.0 compiles atomic control refs and six compatible components into existing private widgets. The native agent supplies grounded plans through [website_agent.py](oak/website_agent.py); the legacy tool retains Jev composition. [Component contract and source card](docs/website-component-library.md). |
| Private human broker | Separate-origin human page and private browser lifecycle. Values go directly to the broker; the gateway receives status and scoped identity. [login page](oak/login_web/index.html), [login_broker.py](oak/login_broker.py), [login.py](oak/login.py). |
| Website adapters | Visible controls, stable references, origin checks, challenges and provider evidence. A renderer does not supply browser extraction. [website_forms.py](oak/website_forms.py), [signin_providers.py](oak/signin_providers.py), [connected_browser.py](oak/connected_browser.py). |

Ordinary A2UI actions become persisted input for the same conversation. Required
contextual forms resolve the original waiting RPC in its original turn. Private
website forms return status through that continuation, never human field values.
`owner_confirmed` means the owner confirmed completion; only provider evidence
establishes `authenticated`. A redirect, stage or renderer event cannot assert it.
Generic completion confirmation additionally requires a previously accepted
submission containing field values; an empty initial page or chooser action
cannot enable completion.

### Exact pins and custom boundaries

| Item | Current pin or boundary |
| --- | --- |
| A2UI wire fixtures | `v0.9.1`, upstream commit `db4306536438df46e4f0443b9c4ec0d5f1a42dc4`; unchanged server/client/common schemas and Apache-2.0 provenance in [OAK-PROTOCOL.md](tests/fixtures/a2ui/upstream/OAK-PROTOCOL.md). |
| Renderer | `oak-a2ui-lit@1.1.0` adapter; `@a2ui/lit@0.13.0` / `@a2ui/web_core@0.13.0` Apache-2.0, reproducible esbuild `0.25.12` local bundle. [Lock](frontend/package-lock.json), [vendor provenance/licences](oak/web/vendor/a2ui/OAK-VENDOR.md). |
| Catalogue | `urn:oak:a2ui:canonical:v1`: Card, Column, Row, Text, TextField, ChoicePicker, Button, Divider, Badge, Metric, Progress, Table, Chart, Image. Constrained bindings and explicit actions; no full Basic Catalogue, HTML/scripts, arbitrary URLs/styles, functions or unregistered components. |
| Ordinary limits | 64 KiB batch/action/state; 32 messages/batch; 4 surfaces; 64 components/surface; depth 16; 32 choices; 2000-character text fields. [Details](docs/a2ui.md). |
| Private schema | At most 16 generic fields, opaque IDs, password/OTP clearing and revision/lease checks. Its widgets do not expand the ordinary model catalogue. |
| Private observation metadata | V2 candidate scene: 32 controls, 64 actions, 24 texts, 64 KiB; native ownership, order, required/enabled/permitted flags and bounded redacted semantic text. Text remains available after input; values/options/routing remain private. Every compiled component is bound to an observed ref and current revision. |
| Browser/HTTP | `playwright==1.63.0`, `aiohttp==3.14.4`; complete runtime pins in [requirements.txt](requirements.txt). |
| Desktop client | noVNC `v1.7.0`, commit `63107bd06d9e1f6136ff21aeda8cd62cbf0d433e`; [provenance](oak/web/vendor/novnc/OAK-VENDOR.md). RFB desktop access, not a form renderer. |
| Schema checking | Optional `jsonschema==4.25.1` for [validate-a2ui-schema.py](scripts/validate-a2ui-schema.py), not a production requirement. |

Display cards use escaped Lit text, native table headers/captions, labelled SVG
charts and progress, and the existing theme. Chart axes retain negative values;
the display does not silently clamp them to zero. Images accept workspace paths
only at publication, then become conversation-owned artifact IDs: at most eight
images per surface, 32 distinct sources per batch and 10 MiB per source before
worker transfer. Browser snapshots contain no filesystem paths or remote image
URLs. The authenticated image loader accepts only PNG/JPEG/WebP/GIF, caps the
stream at 10 MiB and rechecks the owner epoch after body reads. Each active image
owns a reference to its blob URL; replacement, surface deletion and owner reset
release references, abort unused fetches and revoke unused URLs. Late responses
cannot create or restore a URL after removal. This lifecycle never handles
private website field values.

AG-UI uses Oak's mapper/validator without a pinned AG-UI SDK dependency.
[Historical event checks](docs/validation.md), package installation, local tests,
production rollout and successful provider login are separate claims.

## Existing visual language and components

Preserve the existing dark green panel and compact private form. Reuse actual
CSS and DOM components; this document introduces no redesign, font download or
spacing framework.

| Concern | Current source of truth |
| --- | --- |
| Colours | [style.css](oak/web/style.css) `:root`: `--bg: #050a08`, `--bg-2: #0a1612`, `--ink: #ecf5f0`, `--muted: #8fa59c`; `--accent: #3ddc97`, `--accent-2`/`--focus: #8ff5c9`; amber `#f5b14c`, red `#ff7a72`. Preserve semantic primary/quiet/attention/destructive states. |
| Typography | Same file: 16px/1.55 Inter with system fallbacks; h1 28–38px, h2 19px, h3 16px. Inter is a font-stack preference, not a declared bundled font dependency. |
| Spacing/surfaces | Existing 24px panel/card padding, 16px A2UI column/surface gaps, 8px field gaps; `--radius: 22px`, `--radius-sm: 14px`; existing glass surfaces/borders. There is no full named spacing-token system; reuse these rules. |
| Components | `button`, `.quiet`, `.danger`, `.topic-form`, `.a2ui-field`, `.a2ui-card`; panel buttons at least 44px high, form controls 50px. [form-controls.js](oak/web/form-controls.js) owns native construction; callers own layout/behavior. |
| Private page | [login.css](oak/login_web/login.css): compact dark sign-in content following the owner's modal reference, with a trusted exact-origin header, 24px desktop/18px narrow padding, 48px fields and 44px actions. System-font stack at 16px/1.45; muted green neutrals retain Oak's mint focus. The outer dialog supplies the only border, radius and shadow; the iframe fills it without another card or outer padding. Locally served CSS on the separate origin. |
| Mobile/accessibility | Panel safe-area, responsive navigation/dialog and reduced-motion rules in [style.css](oak/web/style.css); narrow-width rules/visible focus in [login.css](oak/login_web/login.css); labels/live regions/status/error roles in each HTML/JS. DOM tests do not establish mobile-keyboard or screen-reader behavior. |

Preserve drafts and active typing during same-revision updates. Do not steal
focus for unrelated status updates. Keep errors, submission and cancellation
usable with the keyboard open. Apply existing Oak tokens when evaluating
upstream components; check the private document and stylesheet independently.

Ordinary A2UI cards suspend polling on page hide and resume one polling timer
on return, including restoration from the browser's back/forward cache. Retained
controls stay disabled until a fresh snapshot confirms their current state.
Same-revision drafts and uncertain-action guards survive suspension; pending
actions are never replayed. Owner/topic reset remains distinct: it disposes the
renderer and rejects old reads, action callbacks and completions. This lifecycle
does not change the private website form channel.

The private sign-in card contains one task heading and the current credential
recipient. For general website tasks its heading updates from the validated
committed origin, not a model label or the original root destination. All
observed fields and actions remain present; alternative methods never inherit
unrelated drafts. Password reveal is a local, explicitly labelled toggle with
`aria-pressed`; submission, cancellation and revision changes clear and mask the
secret widget. The close button cancels the same owned lease as the footer
cancel action. No desktop-takeover control is shown without an implemented
credential-context handoff.

After a valid attachment, the separate-origin page may send
`{type: "oak-login-layout", height: integer}` to its validated parent origin.
This is the only additional parent message: no origin, request identifier,
labels, values or secrets accompany it. The measured card height is capped at
4096 pixels; longer surfaces scroll within the host's viewport limit rather
than losing their layout message. ResizeObserver updates it for
new steps; stable dimensions do not emit. The host verifies exact iframe source
and origin and applies its own viewport limit. The private iframe supplies the
single visible card; the outer dialog does not repeat its title, description or
fixed empty height. Desktop/mobile Chromium screenshots establish layout only;
physical Telegram keyboard behavior and successful owner authentication remain
separate checks.

Opening an authenticated, pending real-site request attaches its private channel
once when the broker reports it available. It does not require a second launch
click or retry an attempted/uncertain attachment. Until the private page reports
its first layout after attachment, the host keeps a visible cancellation control.
If loading does not finish within 30 seconds, it removes the iframe, restores
the host controls and offers cancellation without retrying the launch. Terminal
state, reset and scope changes remove the layout listener and pending ticket.

## Upstream reuse and further evaluation

The narrow source migration now uses **upstream `@a2ui/web_core` for processing,
surface state and binding**, with **Lit surface integration** and restricted
Basic Catalogue components styled using Oak's existing tokens. Custom entries
cover demonstrated literal-text, native-select and label-button differences.
The complete upstream catalogue is not exposed. React/CopilotKit runtime and
private-website Lit integration remain separate evaluation scope.

Published `@a2ui/web_core@0.13.0` and `@a2ui/lit@0.13.0` were inspected on
2026-10-11, then installed with exact pins and a transitive lock for the
2026-10-12 source migration gate. Package `0.13.0`, wire `v0.9.1`, import path
`v0_9` and catalogue ID are separate pins. The [gate](docs/evaluations/a2ui-lit-2026-10-12/README.md)
records real canonical fixture/browser checks under unchanged CSP, local bundle,
licences, narrow compatibility entries and remaining deployment/WebView limits.

Compare a narrow mapping to universal components, the Lit wrapper, and retained
custom components only where needed. Preserve the allowlist, limits, action
receipts, expiry and revisions. The [official renderer guide](https://a2ui.org/guides/renderer-development/),
[client guide](https://a2ui.org/guides/client-setup/) and
[Lit README](https://github.com/a2ui-project/a2ui/blob/main/renderers/lit/README.md)
document available implementations; availability does not prove Oak compatibility.
The owner's subsequent reference was identified as CopilotKit Open Intelligent
UI and inspected at `f6e4388b26a64b9a0714943b08a1ce622b924eec`. Its component
registration, AG-UI continuation and cached Jev presentation routing are relevant
patterns. Its example login is non-functional and it supplies no browser login
executor. See the [source review](docs/agentic-website-forms-research.md#owner-supplied-open-intelligent-ui-reference--2026-10-12).
That reference review alone did not establish compatibility. The later amended
audit authorized the narrow migration; its source gate is recorded above.

### Change rules

1. Reuse existing widgets/components for small changes. Do not add another
   parser, surface-state store, binding engine or schema-validation engine
   without documenting the specific unmet need and reviewing it in the PR.
2. Stack/dependency changes must compare relevant upstream options first. Record
   exact pins/licence, reproducible bundle delivery/CSP, catalogue mapping,
   retained custom code and migration tests. Serve reviewed local assets on each origin.
3. Update DESIGN.md and relevant protocol/provenance docs in the same PR.
   Proposals remain EVALUATION until implementation and recorded checks establish
   a new CURRENT state. Scope the work; this document does not authorize a full
   framework migration, catalogue expansion or service activation.

## Private human values

Passwords, OTPs, cookies, tokens and other private human values must not enter
ordinary A2UI model/action data, AG-UI events, model context, logs, analytics or
durable ordinary input. Ordinary text fields cannot reliably detect arbitrary
secrets; never present them as a credential channel.

Upstream **`sendDataModel` can forward the entire client data model**. Oak rejects
enabling it in [a2ui.py](oak/a2ui.py). Preserve that restriction during migration;
never connect the private renderer to ordinary action/data-model forwarding.
Shared widgets must not imply shared stores, callbacks, tokens or transports.

The broker owns ticket/lease checks, owner/chat/thread/turn/runtime binding,
expiry, cancel/detach and browser cleanup. The gateway receives status only.
Provider evidence and owner confirmation remain distinct. A UI library does not
replace [execution isolation](docs/execution-boundary.md) or the
[website-form lifecycle](docs/website-forms.md).

## Acceptance matrix: requirements and actual evidence

Use affected rows; mark unrelated rows N/A. Record commands, results and scope
in the PR. Documentation, source inspection and synthetic fixtures do not prove
live Telegram behavior or real provider access.

| Area | Acceptance requirement | Existing evidence and limits |
| --- | --- | --- |
| Streaming/incremental updates | Correct create/update/delete and partial assembly; preserve drafts/action identity; no lost/duplicated streamed events. | [test_a2ui.py](tests/test_a2ui.py), [test_events.py](tests/test_events.py), [test_web.py](tests/test_web.py). Current A2UI UI polls snapshots; upstream streaming-processor parity is pending. |
| Focus/validation | Labels associate with controls; updates preserve typing; new steps focus appropriately; malformed values/options fail closed. | [test_login_ui.py](tests/test_login_ui.py) uses a small Node DOM; [test_requests.py](tests/test_requests.py) and A2UI tests check server validation. Real browser focus/screen-reader checks are separate. |
| Dark mode | Existing colours, contrast, errors, disabled states and reduced motion remain legible. | CSS/source inspection and historical panel checks in [validation.md](docs/validation.md); this document performs no new visual/contrast check. |
| Mobile keyboard | Narrow portrait/landscape, safe areas, scrolling and usable submit/cancel with keyboard open. | Responsive CSS exists; actual mobile Telegram/browser/keyboard results must be recorded for affected changes. Node tests do not establish this. |
| Cancel/reopen | Clear secrets, reject late responses, release the owned resource; reopen with a fresh valid request. | UI cancel/detach/late-response tests plus broker/request tests cover constituent behavior; full real-browser cancel/reopen remains a distinct check. |
| Replay/recovery | Original RPC resolves at most once; reject stale revisions/tickets; never replay uncertain actions. | [test_a2ui.py](tests/test_a2ui.py), [test_requests.py](tests/test_requests.py), [test_website_login.py](tests/test_website_login.py), [test_login_broker.py](tests/test_login_broker.py). |
| Unknown components/limits | Reject unregistered catalogues/components/functions and excessive size/depth; render literal text safely. | A2UI malformed/cycle/limit tests, private-schema tests and [schema validator](scripts/validate-a2ui-schema.py). The pinned upstream bundle also passed actual Chromium cycle/depth/count, literal-text and unknown-extension checks; see the 2026-10-12 Lit gate. |
| Private isolation | No human values in parent messages, ordinary DB/events/model output/logs; retain ownership/origin checks and cleanup. | Mocked-IPC/Node canary tests cover software paths. Host attestation and provider access require separate verification. Host probes passed; owner website-form acceptance failed. |
| Upstream migration | Locked local bundle loads under CSP; catalogue/state/action parity and affected rows pass without broader capability. | Pinned local Lit/web_core migration passed five renderer checks, including four Chromium cases and the actual WebGateway/CSP assets. Reproducible build, catalogue/theme and binding/action evidence is recorded in [the 2026-10-12 gate](docs/evaluations/a2ui-lit-2026-10-12/README.md); physical Telegram WebView and owner login remain separate. |

Recorded on 2026-10-11 at `482e6ce`: the full unit suite passed **344 tests, 0 skipped**.
Focused runs included 16 A2UI, 15 Node UI and 18 mocked-IPC website-continuation
tests. At `e9f5e87`, 36 focused checks passed after the text-only input fix;
the subsequent full run with `OAK_PROVIDER_BROWSER_TESTS=1` passed **345 tests
in 91.633 seconds, 0 skipped**. Those tests missed the owner failures described
above. With exact `jsonschema==4.25.1`, the offline schema validator accepted
8 server messages and 1 client action and rejected 3 unsupported fixtures.
These records do not automatically validate future commits or establish a live
provider login. For documentation-only changes, check links/paths, pins and the
diff; unrelated service execution or repeated application tests are unnecessary.

The subsequent optional Jev/component integration passed **431 tests, zero
skipped, in 92.077 seconds** with Chromium fixtures enabled on 2026-10-11.
Inference responses were mocked; real model quality/latency, mobile Telegram and
provider login remain unverified. Details are in the
[component contract](docs/website-component-library.md).

Real model receipts for the bounded pre-input semantic collector, live named-page
discovery and the complete renderer regression are recorded in the
[real Jev evaluation](docs/evaluations/website-forms-real-jev-2026-10-12/README.md).

Historical V1 candidate verification: **535 tests passed, 0 skipped, 170.564 seconds**,
using `OAK_PROVIDER_BROWSER_TESTS=1` and the installed Chromium runtime. This
includes intercepted browser fixtures, mocked protocol tests and local tools;
real model/live-page evidence is listed separately above.

After canonical deployment of `cf7ba70`, the frozen independent complement
exposed fields on 4/12 sites; the original12 regression exposed 3/12. All24 scopes
confirmed cleanup and no account input was submitted. **7/24 exposed forms is
not universal support or successful owner login.** Details and immutable receipts
are in the real Jev evaluation linked above.

Before V2, the generic exact-origin static-resource extension passed the full local suite:
**550 tests, 0 skipped, 176.642 seconds**, with Chromium fixtures enabled. It is
inactive without explicit configuration; no additional live host policy was
activated by these checks. The current Google reserved-identifier negative
control accepted one dispatch and returned `provider_blocked`; full Google login
remains unverified.

The V2 scene/compiler/planner changes supersede the current-source group-selection
and blanket post-input text-removal descriptions, not their historical receipts.
Core V2 commit `7fc161d` is deployed as release `8164397942cfc32a075c`. Local
integration and a real-Jev public dummy-login broker submission passed; actual
owner acceptance remains pending. See the
[versioned source card](docs/website-component-library.md#source-candidate-and-deployed-behavior)
for module ownership and the changed contract.

Private Select captions remain visible across selection and validation. Typed
free-text and credentials echoed in those captions are masked independently;
Jev receives option counts only. Public authentication-host scripts and
credentialed API requests use distinct cookie and CORS handling.

Final V2 local verification: **650 tests passed, 0 skipped, 214.937 seconds**,
including intercepted Chromium and Node checks. Real-model failures and actual
site submissions remain separate evidence in the V2 evaluation; this result
alone is not an owner-login acceptance claim.

After a validated Jev anchor abstention, a private clarification step may ask the
human which observed action to prepare. This reuses Select, carries a distinct
UI revision and does not act on the website. One component composition follows
the explicit choice; only a subsequent ordinary form submit can fill/click.
Failed or uncertain choice requests are not automatically retried. See the
component contract for private endpoint, scope, quota and stale-scene rules.

Recovery candidate verification: **667 tests passed, 0 skipped, 244.165 seconds**
on runtime `9c052e2b`; this includes 17 actual Chromium/broker/private-renderer
clarification tests with mocked model responses. Real model assistance, deployed
service verification and owner login are separate acceptance results.
