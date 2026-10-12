# Private website UI component library

## Current source and acceptance

The source adds catalogue **2.1.0** full-surface plans for agentic leases, using
the same six private widgets. The agent selects an observed dialog or bounded
container; exact membership prevents dropping alternative login methods. Each
action carries a separately validated field binding, so an alternative action
can send `{}` while an email/password action sends only its own fields.
`website-v2` explicitly negotiates these bindings with the private renderer.
The source also removes automatic generic completion confirmation from the
agentic path. These changes are under validation; the latest deployed owner's
valid-account second step failed. Prior negative-identifier and visibility
checks do not prove that continuation or owner authentication.

The following V2 history describes the original single-owner contract; its
strict legacy compilation path remains available. The new full-surface contract
is specified in [DESIGN.md](../DESIGN.md#complete-observed-task-surfaces-catalogue-21).

## Historical V2 source and deployment

The next native-agent path uses this same catalogue directly from the existing
ChatGPT runtime. `oak_website_agent` returns sanitized observations between
navigation and human input steps, so the agent can select an entry point, inspect
its result and compose a plan. [website_agent.py](../oak/website_agent.py) validates
these proposals without requiring a Jev anchor decision. The legacy whole-login
tool retains the Jev planner described below. The workspace lifecycle and exact
privacy boundary are specified in [DESIGN.md](../DESIGN.md#native-agent-workspace).
This addresses the reproduced OpenRouter homepage failure; a prior successful
`/sign-in` form read did not verify navigation from the homepage.

The deployed V2 implementation uses catalog `urn:oak:private-website-ui`
**2.0.0**. Jev selects individual observed controls and compatible components,
then Oak compiles the selections into its existing private widgets. Core commit
**`7fc161d`** was deployed on 2026-10-12 as immutable release
`8164397942cfc32a075c`, runtime digest `9c052e2b…`. The deployed broker passed
a real-Jev public dummy-login submission. Owner-account login and additional CDN permissions remain pending. The owner
approved OpenRouter to `clerk.openrouter.ai` in Hub 5222, and that exact grant is
now active; see the [deployment evidence](evaluations/website-scene-v2-2026-10-12/README.md#core-deployment-and-real-broker-submission).

| Source card | Change and status |
| --- | --- |
| V1, retained for legacy behavior/tests | [website_ui.py](../oak/website_ui.py) and [website_planner.py](../oak/website_planner.py) select a preassembled group. The historical V1 deployment permitted bounded pre-input labels and removed all free text after input. |
| V2: observation | [website_scene_dom.py](../oak/website_scene_dom.py) observes individual controls, actions and visible semantic text, including open shadow roots. It binds native nodes and dispatch locally. |
| V2: catalog/compiler | [website_scene.py](../oak/website_scene.py) validates the scene and compiles a reference-only component plan into `website-v1` widgets. |
| V2: independent masking | [website_scene_sanitize.py](../oak/website_scene_sanitize.py) masks broker-known values in Python before validation/projection; page JavaScript cannot replace its string operations. |
| V2: typed planner | [website_scene_planner.py](../oak/website_scene_planner.py) uses an anchor Choice batch followed by component/action/message Choices conditioned on that anchor. |
| V2: lifecycle/accounting | [website_forms.py](../oak/website_forms.py), [website_composer.py](../oak/website_composer.py) and [login_broker.py](../oak/login_broker.py) own fresh observations, private input, request limits, cleanup and trusted diagnostics. |
| Separate inactive network capability | [website_auth_hosts.py](../oak/website_auth_hosts.py) relays explicitly granted authentication-host requests under DNS, CORS, redirect and size checks. No host grant is enabled by the module's presence. |
| Unchanged UI stack | [login.js](../oak/login_web/login.js) and [form-controls.js](../oak/web/form-controls.js) render native private widgets. Ordinary custom A2UI remains a separate channel. |

The unmet need was broader than choosing a group. The old collector could omit
otherwise useful controls before Jev ran; the selector could only accept an
entire group and could not choose components per field. Removing every label
and error after input also removed evidence needed to understand unfamiliar
subsequent steps. V2 addresses those constraints. Missing DOM candidates,
blocked resources, model abstention and execution errors remain different
failure classes; a model cannot recover a control absent from its observation.

## Official skill and supported model primitives

Development uses the MIT-licensed upstream
[typesafe-ai skill](https://github.com/typesafe-ai/skills/tree/65a39f393687675ce170e6094757de20370365b9/skills/typesafe-ai),
installed at revision `65a39f393687675ce170e6094757de20370365b9`. The
[live documentation index](https://docs.typesafe.ai/llms.txt) and
[installation guide](https://docs.typesafe.ai/agent-skill) describe the skill;
it does not install a browser driver, renderer or credential.

The official [autoformat cookbook](https://docs.typesafe.ai/cookbooks/autoformat)
shows typed judgments over source parts followed by deterministic assembly.
[Function calling](https://docs.typesafe.ai/cookbooks/function_calling) similarly
keeps execution in code while the model selects typed operations and arguments.
These patterns motivate the atomic catalog and two dependent batches. Questions
within a batch are independent; the second batch receives the selected anchor
explicitly. [Choice documentation](https://docs.typesafe.ai/primitives/choice)
and the [state contract](https://docs.typesafe.ai/concepts/state) describe the
supported primitives: Jev judges text/JSON state and returns typed answers. It
does not generate arbitrary prose or HTML, execute browser actions, or inspect
screenshots. This implementation adds no vision model or text-generation fallback.

## Atomic scene and component contract

The browser owns and retains the scene. A minimal example is:

```json
{
  "provider": "website",
  "destination": "https://example.com",
  "revision": "document:7",
  "stage": "form",
  "controls": [{
    "ref": "f1", "type": "email", "label": "Account email",
    "required": true, "form": "g1", "order": 0, "max_length": 254
  }],
  "actions": [{
    "ref": "a1", "label": "Continue", "form": "g1", "order": 0,
    "kind": "button", "enabled": false, "permitted": true
  }],
  "texts": [{"ref": "t1", "kind": "help", "text": "Enter your account email"}],
  "after_input": false,
  "overflow": {"controls": 0, "actions": 0, "texts": 0}
}
```

Scene limits are **32 controls, 64 actions, 24 text entries and 64 KiB**. Labels
are at most 160 UTF-16 units; heading/help/error text is at most 240. Selects
have at most 32 opaque options, with private labels bounded to 120 units. No
input values, defaults, selectors or links are accepted as scene properties.
Unsupported or excess controls are counted in `overflow`; counts do not make
omitted controls usable. The scene stages are `form`, `unsupported_layout` and
`wait`; provider/challenge/authentication state remains outside the model plan.

Each control may carry an optional observed standard autocomplete `role`:
`username`, `current-password`, `new-password`, `one-time-code`, `email` or
`tel`, checked against compatible field types. These page-supplied hints are
fallible; `new-password` alone does not prove registration. Optional `history`
contains at most three prior successfully accepted human-chosen actions, each
with a sanitized `action` label of at most 160 units and up to sixteen
`field_types` enums. It contains no values, model reasoning or model payloads.
Both metadata fields supply context across steps and never become widget
properties or authentication evidence.

`form` records actual native form ownership, or null for loose controls. It is
an execution boundary, not a preassembled semantic candidate. `enabled` records
the observed button state; `permitted` records origin, form-method and dispatch
capability checks. Only permitted actions can be chosen. A disabled button may
be selected when associated fields can enable it; dispatch rechecks its enabled
state after filling. An action whose native form has unsupported required
controls is not permitted.

| Component | Compatible observed types | Compiled private type |
| --- | --- | --- |
| TextInput | text, email | text |
| EmailInput | text, email | email |
| PasswordInput | password | password |
| CodeInput | text, email, otp | otp |
| Select | select | select |
| Checkbox | checkbox | checkbox |

A password cannot become ordinary text, and an observed OTP cannot be
downgraded. A text/email field selected as CodeInput is compiled as `otp`, so
private secret handling follows the selected protected type. Field limits are
clamped to both the observed limit and the compiled type's existing limit.

The locally assembled plan contains only references and catalog enums:

```json
{
  "catalog_id": "urn:oak:private-website-ui",
  "catalog_version": "2.0.0",
  "revision": "document:7",
  "anchor": "a1",
  "fields": [{"ref": "f1", "component": "EmailInput"}],
  "actions": [{"ref": "a1", "presentation": "primary"}],
  "message_ref": "t1"
}
```

Compilation checks the current revision, exact schema, catalog version,
reference membership, component compatibility and duplicates. Output is bounded
to **16 fields, 8 actions and an 8 KiB plan**. Fields may omit optional controls;
all observed required controls in a non-null anchor form must be included.
Loose controls may be selected individually. Fields and alternative actions
must share the anchor's native ownership. The anchor is the sole primary
action; alternatives are secondary. A link anchor cannot have fields, and a
field-bearing form cannot include link alternatives. Excess selections fail
validation rather than being silently truncated.

Labels, option labels and a selected message are copied from the sanitized
observation. Field/action labels are safely shortened to the existing private
widget limit of 120 UTF-16 units. `message_ref: null` supplies no page message.
The compiler creates the existing `website-v1` fields/actions/message shape;
`presentation` determines selection and ordering rather than adding a renderer
schema. No model-authored text, HTML, destination or authentication verdict is
accepted. A model-returned observation never supplies compilation authority.

## Semantic evidence and private values

V2's `model_context()` projects individual refs, types, compatible components,
required flags, native ownership, order, limits, sanitized field/action labels,
visible headings/help/errors, `after_input` and overflow counts. Selects expose
only option counts. Optional roles and bounded prior human-step history are
retained. Destination, revision, option IDs and option labels remain
outside the Jev request. The native-agent tool supplies the opaque revision in
its outer observation for freshness-checked actions. Labels and text are untrusted page evidence, never
instructions or permission to act.

Sanitized semantic text remains available **after input**, including validation
errors and unfamiliar next-step labels. This replaces V1's blanket removal of
all free text. Before text leaves the browser, the collector masks known input
values, current native values, selected option captions, explicit checkbox/radio
values, emails, URLs and long opaque tokens. It also masks supported normalized
and encoded forms of known values before truncating text. Structural validation
is not secret detection: this bounded filter cannot identify every private
sentence or every possible transformation of a secret. Raw body text, HTML,
screenshots, cookies, session state, keys and human input are never model state.

An independent Python pass masks broker-known values in every field/action
label, text entry and history action before structural validation and model
projection. Private option captions mask typed free-text and credential values;
a caption is not hidden merely because its option was selected. All option
captions and IDs remain excluded from the model projection. It covers raw/normalized values, URL escapes, HTML
entities, JSON escapes, base64/base64url and known-value prefixes of at least
four characters at a truncated text end. This closes the specific bypass where
a page replaces JavaScript string methods used by the DOM collector. The DOM
filter remains useful for native defaults; Python can independently protect
only values already known to the broker, not every private fact a page knows.

The private `private_redactions` DOM operation resolves chosen opaque select
IDs to native option values and captions before dispatch. Those strings enter
only bounded broker memory, so reflection on a later document can still be
masked. They are not model output, diagnostics, logs or gateway events. Original
option captions can appear only inside the private Select widget; the selected
value itself is not exported as a default or frontend status. Input and option redactions remain through failed or uncertain dispatch
and are cleared with the lease. Passwords and codes also retain separate private
credential tracking. A nonempty human values mapping still closes the anonymous
static-resource download window, including stale or cancelled submissions.

## Two Decisions batches and actual request accounting

The client retains `POST https://openrouter.ai/api/alpha/decisions`, request
model `typesafe/jev-1.13` and accepted response snapshot
`typesafe/jev-1.13-20260917`. It pins TypeSafe as provider and disables fallback.
The [official OpenRouter tutorial](https://openrouter.ai/docs/guides/community/jev-tutorial)
and [Decisions API reference](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request)
document this protocol; it is not chat completions.

1. The first batch chooses one permitted observed anchor action or `none` from
   the bounded scene. Method selection, identifier, password and OTP steps can
   each be valid without the other credential types.
2. Conditioned on that anchor, the second batch selects a compatible component
   or omission for each eligible field, includes/omits compatible secondary
   actions, and optionally selects an observed message. The compiler rejects
   omission of required native controls. If no such questions remain, the
   anchor compiles directly after the first request.

There is no second aggregate approval of the sign-in step. Anchor selection
already makes that judgment, and the compiler enforces structural constraints.
The initial V2 held-out run selected a chooser action, then the redundant
composition question abstained. Removing that duplicate gate is a general
workflow correction, not a rule to force a particular action or a confidence
threshold. The first failure remains evidence; reruns of that case are
regressions rather than fresh held-out results.

Every returned question is validated: response model/provider, complete answer
keys, Choice membership, finite confidence and probabilities, their bounds and
sum, and consistency of the selected option. Confidence is not an authorization
or an invented quality threshold. Anchor selection may abstain; field omissions
and alternative/message choices remain individually typed. There are at most
two requests per automatic composition, with no automatic retry, alternate model
or legacy-selector fallback. A validated anchor abstention can instead request
one explicit private human clarification, described below. Responses are bounded to 64 KiB and individual requests to 20 seconds;
the broker's outer operation bound remains 30 seconds.

Explicit private configuration is unchanged:

```json
{
  "backend": "jev",
  "model": "typesafe/jev-1.13",
  "key_file": "/var/lib/oak-website-forms/openrouter.key",
  "max_calls": 12
}
```

After V2 deployment, an explicitly configured composer constructs ScenePlanner;
absence of composer configuration makes no Jev calls. The key is a raw private
regular file, with symlink and group/world access rejected. Never store it in
Git, Hub, model state, command-line arguments or ordinary gateway configuration.

Each lease reserves quota **before each actual API request**, including the
second batch. A complete two-batch composition uses two of at most twelve
requests. Failed and cancelled attempts consume quota; a refused reservation
makes no request. Reported known costs and unknown-cost call counts are retained
separately in memory. The request cap is not a persistent global dollar budget,
and an unknown reported cost does not mean free inference. Oak's subscription
runtime remains unchanged; no trading ledger or credential fallback is used.

## Fresh observation, dispatch and diagnostics

For each new revision, WebsiteForms freezes the scene, obtains a plan, compiles
it independently and observes the DOM again before displaying the form.
Same-revision reads reuse the result; failed composition is cached too. Human
input uses the private origin and exact lease binding. Before each fill and the
final click, the DOM transaction rechecks node identity, ownership, origin,
form method and revision. Submit does not call Jev and never retries an uncertain
action. The next page state receives fresh observation and composition.

`stale_form` and `navigation_pending` allow fresh reads. A failed composition
leaves cancellation available and does not repeat inference for that revision.
`submit_uncertain` is terminal and cannot be replayed. Authentication evidence,
challenge/device approval, expiry and cleanup remain broker decisions.

Named Google/Instagram requests retain provider identity, their network and
connected-app scope, and independent positive authentication evidence. Their
private widget schema is bound at attach and on each snapshot. They never offer
generic owner confirmation. Generic owner confirmation requires an accepted
value-bearing submission and a fresh noncredential state; an initial blank page
or chooser with no values cannot enable it. `owner_confirmed` is not
`authenticated`. The separate authentication-host relay described below supplies
network capability only; it does not provide generalized authentication proof.

Candidate diagnostics are available only on the trusted Unix control route
`/diagnostics`, with the existing peer check and full lease binding. They expose
lease state, composition counts/catalog flag and bounded per-request
phase/status, model pin, elapsed time, fixed error enum and cost metadata. They
do not renew a lease or expose observations through the ordinary agent or human
frontend. No page labels, response bodies, input, URLs, tokens or free-form
exception text belong in this in-memory trace.

## Human clarification after an anchor abstention

A canonical Jev anchor decision of `none` is preserved as an abstention. When
1–32 eligible observed anchors exist, the private UI presents a `clarification`
step using the existing Select component and a “Показати поля” button. The text
explicitly says that this chooses which fields to show, without clicking the
website or submitting account data. Larger candidate sets are not silently
truncated. Empty scenes, challenges, transport failures and malformed or absent
abstention evidence do not enter this path.

Clarification has its own opaque UI revision, bound privately to the native
scene revision and complete sanitized observation. `/api/choose-action` accepts
only the existing private lease binding, UI revision and an opaque choice. It
uses the same origin/lease/boundary checks as the input API. It is not a gateway
or executor tool. Before processing the choice, Oak re-observes the page and
rejects any changed scene. It then asks Jev only the component batch conditioned
on the human-selected anchor. The compiler and later dispatch checks remain
unchanged; there is no repeated anchor judgment or confidence override.

Choosing consumes one clarification attempt before inference. Its actual model
request shares the lease's existing quota. Repeated polling costs nothing;
network duplication of an already successful identical choice is idempotent.
Failed, cancelled or uncertain choice composition is not retried. The returned
form uses the native revision and still requires a separate explicit human
submit before any DOM filling or site click. Clarification does not set
submitted state, input history, disclosure state, authentication or completion.
It may renew ordinary human idle liveness, never absolute lease expiry.

Trusted diagnostics expose only clarification state/count/overflow and the
composer's `abstained` versus `composed` status. Candidate labels, human choice
and decision probabilities are not diagnostic output. This is a human-assisted
recovery path, not evidence that Jev selected the action autonomously.

## Separate authentication-host capability, inactive by default

The source candidate accepts an optional `auth_hosts` mapping from an exact
generic HTTPS destination to exact backend hostnames. Limits are eight origins,
four hosts per origin and sixteen total bindings. Named provider policies are
unchanged. The portable mapping is empty by default. The owner explicitly
approved the deployed `https://openrouter.ai` → `clerk.openrouter.ai` grant
on 2026-10-12 (Hub 5222); it is now active. Other proposed hosts remain inactive.
Deploying source does not authorize a new credential recipient by itself.

The relay pins only public DNS answers and accepts non-navigation GET/HEAD/POST
script/fetch/XHR requests from the configured page origin. It preserves the
browser's actual request cookies and authorization only for the granted host;
its own HTTP session has no cookie jar. It enforces CORS response checks and
credential-free preflight requests before non-simple requests because Playwright
interception can bypass Chromium's normal CORS handling. For credentialed API responses, this is deliberately stricter than anonymous
browser requests: the exact origin and credential support are required. Public
script requests strip Cookie, Authorization and body; their anonymous CORS check
accepts one matching or wildcard origin without requiring credential support.
Both an Origin header and an explicit CORS request mode identify CORS scripts.

Fetch/XHR and POST redirects are rejected. Public script GET/HEAD redirects are bounded to the same exact host,
with anonymous CORS checks on each applicable response and JavaScript MIME
validation. Script response cookies, including redirect cookies, are ignored.
Credentialed scripts are unsupported. There is no navigation, WebSocket or arbitrary-host grant. Each lease
has request, body, total-byte, concurrency and duration bounds; close revokes
the relay and its HTTP session. Only fixed failure/status counters enter trusted
diagnostics. This capability is independent of static-resource grants and of
whether Jev can understand the resulting controls.

## Evidence and remaining acceptance

The original deployed generic baseline exposed fields on **0/12** sites with
Jev unconfigured. Later real typed fixture runs matched 3/4, then 7/8, then 8/8
after prompt corrections; those are historical regressions, not unseen-site or
owner-login evidence. With pre-input semantic evidence, the deployed `cf7ba70`
evaluation exposed fields on **4/12 fresh sites and 3/12 regression sites**.
All 24 leases confirmed cleanup without account input. **7/24 forms exposed is
not universal support or successful authentication.** See the
[baseline](evaluations/website-forms-2026-10-11/README.md) and
[real Jev evaluation](evaluations/website-forms-real-jev-2026-10-12/README.md).

Owner acceptance previously failed on Google and TikTok. The later Google
reserved-identifier negative control accepted one dispatch and returned
`provider_blocked`; it did not complete a Google login. Another reported
unsupported layout occurred before composition, so it cannot establish a Jev
failure. Actual owner login remains unverified.

The V2 catalog/planner/sanitizer's 39 focused no-network tests cover reference binding,
component safety, native ownership, omissions, size limits, response validation,
per-request quotas, cancellation, typed roles/history, encoded known-value
masking and secret-free accounting. They use mocked
Jev answers. A separate real public-page regression of the initial V2 snapshot
(before removal of the duplicate composition question) exposed fields on
**3/6** pages: GitHub, Netflix and Spotify. Slack had an unsupported layout,
Amazon was blocked and Wikipedia failed during startup; those three cases made
no model calls. All six scopes closed with no inputs or actions. The three
composed pages used six actual Jev calls, with total reported cost
**$0.000478884**. This used an isolated source snapshot with digest
`a7aa7baf793c5062020658d0ee0b62c88e1b6ec8ab63d1022db6170ff5a1a3a4`,
not production-broker or owner-account sessions. See the
[V2 evaluation](evaluations/website-scene-v2-2026-10-12/README.md).

The V2 evaluation separately records 667 local checks, real-Jev multi-step
renderer regressions, public-site failures and the deployed broker submission.
These results do not establish owner authentication or universal site support.
Older full-suite receipts remain historical.

An unfamiliar supported site should not need a new login template. Supported
DOM observations, network access and meaningful sanitized evidence are still
necessary. This does not promise automatic CAPTCHA/passkey handling,
canvas-only controls, inaccessible frames or arbitrary cross-origin SSO. The
optional exact-origin static-resource policy remains a separate, explicitly
configured experiment; component composition does not broaden it.
