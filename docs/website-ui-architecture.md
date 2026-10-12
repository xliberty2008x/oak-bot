# Website forms: existing standards and shared UI

Canonical requirements, visual sources and acceptance matrix are in
[DESIGN.md](../DESIGN.md). This document supplies the supporting architecture
and dependency evidence. The sections below retain the 2026-10-11 evaluation.
The 2026-10-12 source candidate now replaces ordinary custom rendering/binding
with pinned official Lit/web-core through a restricted adapter; see the
[migration gate](evaluations/a2ui-lit-2026-10-12/README.md) for exact source,
browser/CSP evidence, retained failures and deployment/WebView limits.

Reviewed 2026-10-11. This records the repository implementation and an offline
reuse change. It is not evidence of production deployment or successful login
to any provider.

Oak already implements an A2UI subset, but its renderer is custom. The private
website form originally duplicated native field construction. The small change
in this review extracts that construction into one shared module. It does not
turn the private form into an agent-visible A2UI surface, and it does not claim
adoption of an upstream renderer package.

## Four separate responsibilities

| Responsibility | Existing Oak implementation | What a UI library can replace |
| --- | --- | --- |
| Agent events and conversation routing | `oak/events.py`, `oak/bus.py`, public SSE and `CUSTOM` events | AG-UI client/server SDKs can handle protocol work; they do not inspect website forms. |
| Declarative ordinary UI | `oak/a2ui.py`, `oak/web/a2ui.js`, `oak/web/a2ui-catalog.json` | An A2UI renderer and processor can replace custom surface, component and binding code. |
| Website inspection and provider decisions | `oak/website_forms.py`, `oak/signin_providers.py`, `oak/connected_browser.py` | This still needs a browser adapter: visible controls, stable references, redirects, challenge detection and evidence of authentication. |
| Private human input and its lifetime | `oak/login_web/login.js`, `oak/login_broker.py`, `oak/login.py` | Native widgets or a renderer may be reused here, but owner/turn/session binding, separate origin, leases and credential handling remain application responsibilities. |

AG-UI defines agent/application event exchange; A2UI describes declarative UI.
They are complementary standards. Neither standard, by itself, provides a
website-login extractor or a private credential broker. This distinction follows
the [official AG-UI overview](https://docs.ag-ui.com/introduction) and the
[A2UI transport documentation](https://a2ui.org/concepts/transports/).

The ordinary A2UI action path intentionally writes field values into its data
model and durable state, constructs action context and calls
`Controller.submit`. See `oak/web/a2ui.js:createA2UIPanel` and
`oak/a2ui.py:A2UIStore.action`. It is appropriate for non-secret preferences.
Passing password or OTP values through that path would disclose them to normal
agent state. Sharing widget code does not require sharing this path.

The private form receives only an attachment ticket from the parent frame.
`oak/web/requests.js` verifies frame origin/source; the private page exchanges
the ticket for a memory-only lease. Values are sent directly to the broker's
same-origin human API. `oak/login.py` receives status and scoped session identity,
then resolves the original waiting native request. `owner_confirmed` remains
distinct from provider-established `authenticated`.

## Exact repository dependencies and provenance

| Item | Repository evidence and version |
| --- | --- |
| A2UI wire format | `v0.9.1`; upstream commit `db4306536438df46e4f0443b9c4ec0d5f1a42dc4`. `tests/fixtures/a2ui/upstream/OAK-PROTOCOL.md` records unchanged server/client/common schemas and Apache-2.0 license provenance. |
| A2UI renderer | `oak-a2ui-dom@1.0.0`, declared in `oak/web/a2ui.js`. This is Oak's own implementation, not an installed upstream package. |
| A2UI catalogue | `urn:oak:a2ui:canonical:v1`, in `oak/web/a2ui-catalog.json`. Six components: Card, Column, Text, TextField, ChoicePicker, Button. No full Basic Catalogue claim. |
| AG-UI | Oak's event mapper/validator; no AG-UI SDK dependency in `requirements.txt`. `docs/validation.md` records a historical check against the official 1.0 event JSON schema, but this checkout does not supply a pinned AG-UI SDK or equivalent vendored schema provenance. Do not describe it as an installed AG-UI renderer. |
| Browser and HTTP libraries | `playwright==1.63.0`, `aiohttp==3.14.4` in `requirements.txt`. |
| Other Python runtime libraries | `Pillow==12.3.0`, `piper-tts==1.8.0`, `vosk==0.3.45`, `yt-dlp==2026.8.19`, `markdown-it-py==4.2.0`. None is a schema-form renderer. |
| Optional schema validation | `jsonschema==4.25.1` documented in `docs/a2ui.md`; not a production requirement. `scripts/validate-a2ui-schema.py` validates local fixtures without network retrieval. |
| Desktop client | Vendored noVNC `v1.7.0`, commit `63107bd06d9e1f6136ff21aeda8cd62cbf0d433e`, documented in `oak/web/vendor/novnc/OAK-VENDOR.md`. This is the RFB desktop client, not a form renderer. |

There is no application npm manifest/lockfile or installed React, Lit, Angular,
JSON Forms, `@a2ui/lit` or `@a2ui/web_core` dependency in this checkout. The A2UI
protocol pin above is not an npm package version. Its
[upstream schema location](https://github.com/a2ui-project/a2ui/tree/db4306536438df46e4f0443b9c4ec0d5f1a42dc4/specification/v0_9_1/json)
is recorded by the local provenance file; this audit did not re-download and
re-hash those fixtures.

## Minimal reuse implemented

`oak/web/form-controls.js` exports `createFormControl(spec)` and
`updateFormControl({label, control}, spec)`. It creates native labels, text/email/
password/OTP inputs, checkboxes and selects. Labels and options use `textContent`.
It installs no event handlers, performs no network/storage operations, and does
not read or keep field values.

Both `renderRequestFields` and A2UI's TextField/ChoicePicker branch in
`oak/web/a2ui.js` use this helper. `oak/login_web/login.js` uses the same helper
for its bounded provider/website schema. That removes the duplicated label,
input and option construction instead of growing another generic renderer.

Each caller still owns its validation, limits and values. Ordinary fields retain
the existing 2000-character text limit and string-array A2UI choice binding.
Private forms retain opaque field/action IDs, at most 16 fields, password/OTP
clearing, revision handling, explicit owner confirmation and lease cleanup.
Changing the shared widgets does not expand the ordinary A2UI catalogue to
accept password or OTP components.

The helper is served as `/form-controls.js` independently on both origins.
The main panel imports `./form-controls.js`; the private module imports
`/form-controls.js`. The private origin must serve its own local file under the
existing CSP, not load executable code from the parent origin or a CDN. The
fixed static routes and `oak/features.py` source-manifest entry are required
integration changes. No model-generated URL selects this module.

## Established renderer alternatives

The npm registry returned published **0.13.0** releases for both
[`@a2ui/web_core`](https://registry.npmjs.org/@a2ui%2fweb_core/0.13.0) and
[`@a2ui/lit`](https://registry.npmjs.org/@a2ui%2flit/0.13.0) during this review.
Both declare Apache-2.0. The web-core release archive was fetched into memory,
checked against registry SHA-512 integrity and inspected without executing it
or installing dependencies. Its integrity is
`sha512-4Clpksne0RY9KAheQoPoTwVDJ+6XI0Es2yqVME/PN0aWQ1321Wih69SEr8Nkc+7uKMyV5FlK32o88uoyM7ZBCw==`.
This establishes published availability and an exact candidate artifact, not
runtime compatibility with Oak. The npm package version `0.13.0`, wire version
`v0.9.1`, `v0_9` import path and catalogue ID are separate versioning decisions.

The candidate's exports contain `/v0_9`, `/v0_9/universal` and
`/v0_9/basic_catalog`. Its v0.9 adapter explicitly supports catalogue versions
0.9 and 0.9.1 and recognizes all four operations Oak uses. Its
`MessageProcessor` accepts registered catalogues, an action handler and
validation options. That is concrete source-level support for investigating
the existing wire protocol without a forced v1.0 conversion. See the
[official adapter source](https://raw.githubusercontent.com/a2ui-project/a2ui/main/typescript/web_core/src/processing/adapters/v0_9.ts)
and [package exports](https://raw.githubusercontent.com/a2ui-project/a2ui/main/typescript/web_core/package.json).

The [official Lit README](https://github.com/a2ui-project/a2ui/blob/main/renderers/lit/README.md)
states that its Basic Catalogue uses universal Custom Elements from web-core
and re-exports them for compatibility. Thus the universal/basic-catalogue path
is a concrete alternative to adding the Lit wrapper. It is not a zero-dependency
DOM library: the published web-core package itself depends on Lit, Zod,
signals, colour validation and other packages. A reproducible local bundle and
lockfile are still needed; Oak's current static module server cannot resolve
bare npm imports on its own.

| Option | Fit and remaining work |
| --- | --- |
| `@a2ui/web_core/v0_9/universal` plus `/v0_9/basic_catalog` | Preferred evaluation: upstream processing and a strictly allowlisted selection of maintained Custom Elements, styled with Oak tokens. Map catalogue differences; do not expose the full upstream catalogue. |
| Official A2UI Lit renderer plus `@a2ui/web_core` | Compare the documented surface wrapper against direct universal components. Preserve the same allowlist, catalogue mapping, bundling and CSP requirements. |
| `@a2ui/web_core/v0_9` with necessary Oak catalogue entries | Compatibility option for demonstrated gaps in standard components. Reuse upstream processing/state/binding/validation; retain custom entries only where needed, rather than another complete engine. |
| Official A2UI React/Angular renderers | Appropriate if the application already adopts that framework. Oak currently does not, so this is a larger change than sharing its native controls. |
| JSON Forms | Established JSON-Schema/UI-schema rendering with React, Angular and Vue renderer sets. Suitable for ordinary structured forms, but it introduces a framework/schema adapter and does not replace browser extraction or the private broker. |

The [official A2UI client guide](https://a2ui.org/guides/client-setup/) lists
maintained Lit, React and Angular renderers and describes custom catalogues.
Its [renderer guide](https://a2ui.org/guides/renderer-development/) recommends
`@a2ui/web_core` for shared protocol processing and documents the `v0_9` API for
v0.9/v0.9.1. These are upstream capabilities, not dependencies already adopted
by Oak. Package versions must be selected and locked during an actual migration;
this audit neither installs them nor claims that the newest package automatically
matches Oak's pinned catalogue.

The built-in Basic Catalogue is **not a drop-in replacement** for Oak's
canonical catalogue. The inspected 0.13.0 package contains these specific
differences, also visible in the
[official component schemas](https://raw.githubusercontent.com/a2ui-project/a2ui/main/typescript/web_core/src/v0_9/basic_catalog/components/basic_components.ts):

| Existing Oak shape | Upstream Basic Catalogue difference |
| --- | --- |
| Catalogue `urn:oak:a2ui:canonical:v1` | Built-in ID is `https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json`. Register Oak explicitly or perform a versioned mapping. |
| Button has literal `label`, with `primary`, `quiet`, `danger` variants | Button requires a child component ID; variants are `default`, `primary`, `borderless`. A generated Text child and style mapping are required. |
| Text variants `heading`, `hint`, `body` | Upstream has heading-level variants, `caption`, `body`. |
| TextField/ChoicePicker accept `required` | Upstream strict schemas use validation `checks`; passing Oak's `required` directly is rejected. |
| ChoicePicker is a native single select | Upstream has selection variants and display styles; preserving the dropdown interaction needs explicit mapping or an Oak component. |

Preserve Oak's existing whitelist and limits before feeding any processor.
Registering the entire Basic Catalogue would otherwise broaden available
components, functions and media/URL behavior. Also preserve the application
envelope: Oak's authoritative snapshot contains a revision, expiry and consumed
state; it is not itself a sequence of A2UI wire messages. A client adapter must
reconstruct create/update/delete operations or use an equivalent snapshot
adapter without losing local drafts or durable action semantics.

[JSON Forms documentation](https://jsonforms.io/docs/) describes separate data
and UI schemata; its [renderer-set reference](https://jsonforms.io/docs/renderer-sets/)
lists framework-specific implementations. This is a viable comparison, rather
than grounds to write a new form framework.

For an upstream migration, first map the current six ordinary components to a
restricted registered catalogue and pass existing action/revision/recovery
tests. A concrete offline acceptance should load the exact locked package,
process `tests/fixtures/a2ui/profile.json`, compare resulting component/data
state and client action envelopes, test create/update/delete and same-revision
draft preservation, reject forbidden catalogues/components/functions, and verify
single-use revision/uncertain-action behavior through the existing gateway.
Browser fixtures must check native labels, focus, selection, accessibility and
the existing CSP without CDN requests. Those upstream-package execution tests
have not been run in this review; archive inspection is not a substitute.
Keep separate renderer instances and transports on each origin. Any
private instance must keep credentials out of ordinary data-model forwarding,
action context, debugging, persistence and parent messages. In particular,
`sendDataModel` can forward the entire client model; preserve Oak's current
rejection of enabling it. Adopting a standard
library does not justify weakening that separation.

“Intelligence UI” is not identified by an exact repository/product link in this
review. Do not silently equate it with AG-UI, A2UI, CopilotKit or a similarly
named product. Its exact link remains a follow-up for product-specific
comparison; that uncertainty does not block the AG-UI/A2UI findings or reuse.

## Validation and remaining scope

The extraction has 15 passing offline Node/stdlib checks in
`tests/test_login_ui.py`. The harness loads real ES modules and links the shared
helper, including the ordinary `renderRequestFields` consumer. Checks cover
origin/source/ticket validation, literal labels, choice handling, input
preservation, immediate secret clearing, cancellation/detach, generic controls
and the owner-confirmed/provider-authenticated distinction.

`tests/test_website_login.py` passed 18 mocked-IPC continuation checks after the
non-finite-deadline regression update: same native RPC, owner/chat/thread/turn/
epoch/session binding, status-only persistence, cleanup and unavailable-boundary behavior.
All 16 existing checks in `tests/test_a2ui.py` also pass after extraction.
JavaScript syntax checks and `git diff --check` pass.
The subsequent full unit suite passed 344 tests, 0 skipped. The separate
`scripts/validate-a2ui-schema.py` run with exact `jsonschema==4.25.1` accepted
8 server messages and 1 client action and rejected 3 unsupported fixtures.
These are recorded 2026-10-11 checks, not live-provider verification.
`tests/test_login_broker.py`,
`tests/test_signin_providers.py` and `tests/test_website_forms.py` cover broker and
adapter behavior; browser-fixture tests remain distinct from successful access
to a real provider account.

No service activation, provider login or desktop takeover is part of this audit.
The shared module change is a small local reuse improvement. Replacing the
custom A2UI protocol processor/renderer with the official implementation remains
an explicit, separately testable migration.
