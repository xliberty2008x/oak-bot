# Scene/component V2 evaluation, 2026-10-12

This source candidate expands Jev from choosing preassembled groups to selecting
an action and individual observed controls/components. Production was still
`04dea145` during these tests. No owner account login was verified.

The first frozen code snapshot had aggregate source digest
`a7aa7baf793c5062020658d0ee0b62c88e1b6ec8ab63d1022db6170ff5a1a3a4`.
The live API is the documented Decisions endpoint, pinned to
`typesafe/jev-1.13-20260917`; browser profiles were fresh and owned by the probe.
Credentials/key contents, cookies and page contents are absent from receipts.

## First six public-page probes

[Immutable receipt](public-pages.jsonl), SHA256
`bdcab79459378ca741dcaf0ca18cd57e0ef83d78a5afcd09537b817781795f44`.
The set was fixed before the run. These are regression pages, not an independently
random unseen corpus. The standalone candidate adapter was exercised, not the
production broker or Telegram. No site inputs or actions were submitted.

| Site | Outcome | Jev HTTP requests |
|---|---|---:|
| GitHub | text/password, one action | 2 |
| Slack | unsupported layout | 0 |
| Netflix | text/password, two actions | 2 |
| Amazon | provider blocked | 0 |
| Spotify | email, one action | 2 |
| Wikipedia | browser start failed with ValueError | 0 |

**3/6 exposed fields**, all six browsers closed. The three failures remain in
the denominator. All six real model requests returned the pinned model and
reported usage; total known cost was **$0.000478884**. This measures first-form
composition, not working account authentication or universal support.
[Runner](public-pages-runner.py) has parameterized artifact paths; the original
execution used the frozen code snapshot above. A private key file is necessary
for explicitly requested real model execution; importing code alone is not proof.

## First fresh multi-step holdout: failed

The fixture was authored independently and frozen before its first inference:
chooser, member identifier/select/checkbox, validation with replacement, code
versus password, navigation and explicit owner confirmation. Fixture digest:
`29098766dc8242aef3b7194f7fa5c39b6d95e0ebe8ec2a71cc0831e089e31fb1`.
Runner SHA256 `7a1cb24392fec383f98bbb5f67cbef5effc1e9d6d882ef07c027ef8f10118668`.
It used actual Chromium, the private renderer and actual ScenePlanner transport;
the website and original continuation were synthetic shims, not live providers.

[First receipt](holdout-first-failure.jsonl), SHA256
`2597fcae17044fded55bebc54798ae23d0ee8604dc089519cfa9553cd653b983`.
The anchor request selected an action. The second, redundant composition
judgment abstained on the zero-field method chooser. The run failed, with
**2/12 HTTP attempts**, known cost **$0.0001029**, no retry, no human input and no
site POSTs. All four browser resources closed and the synthetic continuation
settled by cancellation. Thus this first run did **not** validate post-input
replanning, despite deterministic browser tests passing. Subsequent runs on
these same pages must be described as regression, never fresh holdout evidence.

## OpenRouter loading diagnosis

The actual deployed broker returned HTTP 200 without native fields because
`clerk.openrouter.ai` was outside its policy. Script-only permission loaded
Clerk scripts but left its GET bootstrap blocked. A fresh anonymous isolated
experiment with pinned, cookieblind GET environment/client requests and bounded
same-host script redirects exposed one native form (text and password) with
two actions. No inputs/actions/model calls or account login occurred. One
same-origin POST was blocked in that read-only experiment. This establishes a
loading dependency, not successful submission. Production auth-host permission
is a separate explicit configuration decision; the component catalog grants no
network authority.

## First simplification regression: failed on the next page

The same untouched fixture, now a regression, ran against snapshot
`7f8499845e83dc23c5790cc773cec4a3a7641c2d3b5935661cee4c2e0194f22b`.
Removing the duplicate aggregate composition question allowed the method chooser
to advance. The next identifier scene's anchor judgment abstained. Result:
**3 API requests**, **$0.000154014**, no values entered, no post-input validation.
All resources closed and no retry occurred. [Receipt](regression-second-page-failure.jsonl)
SHA256 `e9c693fd1dfe11bfdc63e6c0045cfa134861189e6cdb2104f3e2b42873c26219`.

A separate read-only Chromium diagnosis verified that both native forms on the
second page were valid POST forms, with enabled, permitted actions. The failure
cannot be explained by malformed fixture quoting or a blocked GET form. Semantic
roles and continuity of previous human steps were absent from that V2 context;
adding them is a general evidence improvement, not proof it fixes the failure.

## Candidate OpenRouter relay probe: failed loading

An anonymous GET-only probe against the same snapshot attempted six Clerk
requests but transferred no response content. Its subsequent single Jev anchor
request abstained; this is not a valid quality test of the completed login page.
Three writes were blocked, no values/actions occurred, cleanup succeeded.
[Receipt](openrouter-relay-loading-failure.jsonl), SHA256
`829aac3fd61bb69f0ade0b9d174b3cd5b2a95e8e911f869816927c6edc1bd220`.
Subsequent diagnosis identified cookie-setting HTTP307 public-script redirects
as the concrete loading refusal; anonymous public scripts were then separated
from credentialed API requests. Browser CORS semantics also required review. A real intercepted Chromium negative test independently demonstrated
that fulfillment bypassed CORS; activation must wait for explicit CORS/preflight
checks and their verification. No owner data was used in these experiments.


## Context regression and a private Select defect

Snapshot `71289aec1876cd846336695756a4e6c32a149474d2df00061e2466c90a245405`
added observed autocomplete roles and the last three accepted human steps.
The old fixture selected three plans with six actual requests, no abstentions,
and **$0.00040677** known cost. Method choice and validation with replaced native
controls passed. The run then timed out; [receipt](regression-context-timeout.jsonl)
is retained as failed. Offline replay of the six recorded responses proved that
the runner waited for a select caption which Oak had masked after selection.
This was also a product usability defect: a user could not read that choice.
It was corrected by separating private option-caption masking from model text.
No additional inference was used to establish this diagnosis.

## Actual OpenRouter composition

The same frozen candidate used its ordinary same-origin browser route and the
new auth-host relay in a fresh anonymous browser. After waiting for native
controls, **actual Jev composed EmailInput, PasswordInput and one action** in two
requests, costing **$0.000323526**. Thirteen Clerk resources were fulfilled with
no relay failures, and all resources closed. This is not the earlier diagnostic
that separately proxied same-origin GET responses.

[Receipt](openrouter-real-composition.jsonl), SHA256
`2d053d8c4754043b42baf36106968f7e7f6c790b89b506b2d08384e7a10506a2`.
No values or actions were submitted; three attempted page writes were blocked
by the evaluation's GET-only outer gate. This verifies real model composition,
not account authentication, the production broker or owner acceptance.


## Frozen final runtime and public demo submission

Final runtime digest:
`0b77c30671a4ae922f3daf2c4105a3e869a45364e33ea3d0d3c76e6e5ef75fb9`
(124 code files). This includes the private Select correction and separately
validated anonymous-script CORS behavior. No runtime source is changed during
these final evaluations.

The public ToScrape dummy-login regression used actual Jev and Chromium, made
exactly one site POST and observed the site's logout control. The adapter then
returned `review` with explicit human confirmation available; it did not claim
verified authentication itself. Two actual Jev requests cost **$0.000123732**,
with complete reported usage, and cleanup succeeded.
[Receipt](public-demo-final.jsonl), SHA256
`f27621f8868caee803018e0bf8ecc16ed2f2a93f0390dbd1bc347fcd91f43daf`.
This is a public demo account and standalone adapter test, not an owner account
or production-broker acceptance.


## Final old-fixture regression: passed

After the private Select fix, the unchanged membership fixture passed on its
single run against the final digest: **5/5 plans**, **10 actual Jev requests**,
**$0.000651924**, 10.552 seconds, no abstentions or unknown-cost requests.
Validation and native DOM replacement, stale-action rejection, OTP-to-password
method switching, private input clearing, navigation and explicit human
confirmation all passed. Six post-input request payloads passed privacy checks.
All four browser resources closed, and both code and fixture hashes were
unchanged. This uses the shipped private renderer with an original-continuation
shim; it is not the actual native runtime or a live provider account.

[Receipt](regression-final.jsonl), SHA256
`b5274d1c08e7d14c998033ce00caa17ab15367ebb3b489000b6bb5b047966625`.
The prior three failed outcomes remain above. This corrected run is regression
evidence and is not relabeled as an unseen test.


## Full local verification

On the unchanged final runtime digest, **650 tests passed, zero skipped,
214.937 seconds**. Chromium fixture tests were enabled and the installed Node
binary was explicitly supplied. [Structured receipt](full-suite.json) records
the log hash. These checks cover local implementation and protocol behavior;
they do not replace the separately retained real-model results or owner login.


## Fresh independent final fixture: failed after validation

A new six-state fixture was authored after the earlier implementation freeze,
validated offline and frozen before its first inference. Fixture digest
`6f0170ed863edd3688abe1780b62493d05607615869a180189e883c724d53daa`;
runner SHA256 `fe0969d079b7d4da96e59b74d59911340c5d391716bf2fdb8e4baf10c2677646`.
Against the final runtime, the first two plans were selected. After synthetic
identifier input and validation replaced the native form, Jev selected `none`
for the third scene. The unchanged private UI subsequently timed out.

The one run used **5 actual API requests**, **$0.000309708**, with complete
usage and no retry. The post-input payload passed its privacy guard, all four
resources closed and code/fixture hashes were unchanged. This is a retained
model-quality failure, not a successful login or a transport refusal. The
successful old-fixture regression does not cancel this independent failure.
[Receipt](fresh-holdout-first-failure.jsonl), SHA256
`e2aa832e771f1d17b77b6db00941bb8a625c240802bfbc4eb39ef43bc3d42bfb`.


## New seeded six-site sample

After freezing the final runtime, a new sample was drawn using Python
`random.Random('oak-scene-v2-final-sites-20261012').sample(pool, 6)` from a fixed
12-site convenience pool. [Selection and official sources](random-six-selection.json)
were frozen before adapter probes. These are not traffic-weighted estimates.

| Site | Result | Actual Jev requests |
|---|---|---:|
| Canva | Main HTTP403, provider blocked | 0 |
| Udemy | Main HTTP403, provider blocked | 0 |
| Adobe | Main HTTP200, no observed controls after three reads | 0 |
| LinkedIn | Six fields, including a repeated email/password/checkbox set; usability unverified | 2 |
| Yahoo | Main HTTP200, no observed controls after three reads | 0 |
| Pinterest | Main HTTP200, no observed controls after three reads | 0 |

**1/6 exposed composed fields**, with **no proven usable login** in this sample.
All six scopes closed. Total known cost was **$0.000420168**, with zero missing
usage records. [Receipt](random-six-public-pages.jsonl), SHA256
`0b943455b3b87ba9255a6a800555674f975301333819ffa52357da5bb4e4105b`.
No sites were replaced, no inputs/actions were submitted and no model call was
retried. The extra GET/HEAD-only evaluation guard blocked three page writes each
on Canva, Udemy and LinkedIn, and can affect initialization. No additional
static or authentication-host grants were enabled. The empty-control results
made no Jev calls; they are not evidence of model abstention. Their causes need
separate structural/network diagnosis.


## Controlled static-dependency comparison

A separate metadata-only diagnosis found blocked CDN scripts/styles and no
native forms on Yahoo/Pinterest; LinkedIn had six distinct loose native
controls, including two corresponding triplets. Their native identities were
not duplicates. Without the CDN assets both triplets were geometrically visible, so
merging controls by their labels would have guessed which target to execute.
[Diagnosis receipt](random-six-diagnosis.jsonl), SHA256
`119ee39a6e205375dbd7ea0c5c81ad2581b5e162593dd08a0549c8c0573d2d7e`.

A new isolated comparison enabled only anonymous script/stylesheet/font
resources from each site's observed exact CDN: Yahoo `s.yimg.com`, Pinterest
`s.pinimg.com`, LinkedIn `static.licdn.com`. Everything else, including the
GET/HEAD-only diagnostic gate and frozen source, stayed the same. No
production policy was changed.

| Site | Observed result with its CDN | Actual Jev output |
|---|---|---|
| Yahoo | One form, text and checkbox | text/checkbox, two actions |
| Pinterest | One form, email and password | email/password, one action |
| LinkedIn | First triplet now has no rectangles; second is visible | email/password, two actions |

All three exposed sensible field sets; **none completed account authentication**.
Six actual calls cost **$0.000552174**, with full usage and cleanup. No input or
action occurred. [Comparison receipt](static-dependency-comparison.jsonl), SHA256
`c371f7356d79f966de8938c0a565987d18a0c1b94031bc603be9142d6cde4f88`.
The original random sample remains 1/6; these additional loads diagnose a
specific dependency and are not replacements in that denominator. Adobe's
empty page and HTTP403 outcomes remain unresolved. Ungranted cross-origin
fetch/auth/analytics/Google resources stayed blocked; ordinary same-origin GET
fetch/XHR remained allowed. Activating a production host policy is a separate
operation from this anonymous comparison.


## Explicit clarification after model abstention

The recovery runtime is frozen at
`9c052e2b16e4a9f984a81376256b9ab811ec5cd7ee0c0c8682ffbf9075462a23`
(124 code files). A canonical anchor `none` decision may expose a private Select
of the current eligible observed actions, with a separate revision. Selecting an
action composes its fields with Jev; it does not fill, click, send site requests,
change completion eligibility or add a submitted step. A later ordinary submit
is still required. Corrupt or absent abstention evidence, stale scenes, failed
composition and more than 32 candidates do not silently force a model decision.

The complete local suite passed **667 tests, zero skipped, 244.165 seconds**,
including 17 new actual Chromium, broker and private-renderer recovery tests.
The source and frozen runtime match exactly.
[Full-suite receipt](recovery-full-suite.json) records the log hash and scope.
Independent code review found no blocking issue. Local tests use mocked model
responses; actual Jev recovery is evaluated separately and assistance is counted.

At this recovery checkpoint, production policy remained unchanged while the
owner decisions requested in Hub reports 5200 (OpenRouter to Clerk) and 5209
(three static CDNs) were pending. The later Clerk approval and activation are
recorded below; the anonymous comparisons did not activate those permissions.


## Real Jev assisted regression: passed

The unchanged failed studio fixture (`6f0170ed…`) was run once against the frozen
recovery runtime. The real third-scene anchor abstention recurred. Exactly **one
human clarification** selected the intended observed action; component-only Jev
inference then prepared its fields without acting on the site. The complete
scenario passed **5 selected plans**, validation replacement and stale refusal,
password/OTP method changes and clearing, two synthetic site POSTs, navigation
and explicit confirmation. Ten actual HTTP requests cost **$0.000654276**, with
complete usage, 7.595 seconds total, six post-input privacy checks and all four
browser resources closed. Code and fixture hashes remained unchanged.

[Receipt](assisted-regression.jsonl), SHA256
`24a6ae90b0db7d38407f68def47452b150f0ebb07970ef600f9f238557d753ba`.
The separately frozen runner SHA256 is
`b99898f985e00d2d950f775ba6eddc85961113859e9220180b71727018e11f5a`.
Its password widget locator uses the expected input type because the original
exact caption can be privacy-masked. The fixture, assertions and original runner
were not edited. This is an **assisted synthetic regression**, with the shipped
private renderer and a continuation shim. It is not autonomous holdout success,
production-broker acceptance or real provider authentication; the first failure
remains above. There was no retry or model response substitution.


## Core deployment and real broker submission

Core commit **`7fc161d5f8708c895ffa3a34f85530217b747085`** was deployed through
the canonical execution and website-form installers as immutable release
`/opt/oak-executor/releases/8164397942cfc32a075c`. All 124 runtime files match
`9c052e2b…` and the installed execution manifest, including the new scene/auth
modules. The smaller feature-source digest alone does not cover those modules.
Current-boot attestation passed all ten checks; boundary checks passed as both
gateway and broker identities. The trusted broker health is ready, and HTTPS
JavaScript/CSS hashes match the frozen source.

At core deployment, the broker configuration remained identical in settings to the previous release:
real Jev, max 12 calls, existing TikTok static grant, and no new Clerk or generic
CDN grants. Hub 5200/5209/5210 require the owner response for those additions.
Even anonymously downloaded scripts execute in the site page and can read its
inputs; anonymous transport does not make them inert.

The gateway was started before broker provisioning fully finished and refused
the incomplete execution boundary twice. After provisioning, its worker remained
stable with two recorded startup restarts; broker restart count is zero. No
action was replayed. Two pre-stop checks, the stopped check and post-start/demo
checks found no active or pending work. The five pre-existing uncertain intake
records were preserved.

A separately reviewed, frozen runner (SHA256
`cf1745d1bea33110a8ce075048bf9b686c9454ee8954077309704d95e9c8061b`)
created one fresh synthetic-owner lease over the trusted Unix socket and used
the real private HTTPS API. On the public ToScrape demo, actual Jev composed
text/password fields with **2 requests, $0.000123732**. Exactly one dummy-input
submit was accepted; the next form was `review`, still active, with explicit
confirmation available. The runner did not confirm or promote the session.
Cancellation and cleanup were confirmed; runtime hashes remained unchanged.
[Deployed receipt](deployed-public-demo.jsonl), SHA256
`181313d201408e3809421be7596b6bf0454afebef8401664131b5208b19ee2bd`.

This exercises the installed broker, real model, browser and real public-site
submission. It does not exercise the Telegram/mobile renderer, a native waiting
continuation or an owner's account. The separate standalone demo above observed
the logout control; this broker check observes the review transition only.


## Owner-approved OpenRouter Clerk activation

The owner answered “Так, увімкнути для OpenRouter” to the explicit question
about forwarding login input and cookies to `clerk.openrouter.ai`, with manual
input and submission retained. Hub report **5222** records the exact answer and
question reference. The deployed broker now grants exactly
`{"https://openrouter.ai": ["clerk.openrouter.ai"]}` through `auth_hosts`.
The canonical website-form installer applied the reviewed config; its service
unit and every other setting remain unchanged. The generic CDN proposals are
still inactive. [Activation receipt](clerk-activation.json).

Two idle observations and the stopped observation found no active/pending work.
The gateway was stopped during broker replacement, then restarted after broker
health and execution-boundary checks passed. Both services have zero restarts
since activation. All 124 runtime files still match `9c052e2b…` and the installed
manifest. Five existing uncertain intake records were retained without replay.
No full test rerun was needed for this config-only activation; runtime code
and the prior 667-test result are unchanged. Actual live form evidence follows
separately; enabling the domain is not proof of owner authentication.


### Actual deployed OpenRouter form: initial loading then successful composition

A first-read-only probe stopped on `clarification`: one real Jev request,
**$0.000123438**, with no candidate selected. Only three Clerk resources had
finished (six upstream requests, 487,104 bytes). Cleanup was confirmed. That
failed first-read check is retained [unchanged](openrouter-deployed-first-read.jsonl),
SHA256 `2db59232d5cc02fb0f011d52f461a45e03a3c09dfb304c4c79b92ee8518f8ce9`.
It does not represent the complete polling behavior of the shipped client.

Source review found that initial navigation waits for `domcontentloaded`;
visible navigation actions can be observed before asynchronous Clerk fields
appear. The private client continues polling every 1.5 seconds during
clarification. Each snapshot reobserves the native scene; outcomes are cached
per native revision, while a changed scene can trigger a new composition.

A separate frozen diagnostic followed that normal polling cadence, bounded to
six reads and the existing 12-call lease limit. It made **two form reads**: the
initial clarification was automatically replaced by **email/password and one
action**, with no human clarification selection. Diagnostics showed two native
composition revisions, **three actual Jev calls, $0.000447384**, and complete
usage. Thirteen Clerk resources were fulfilled through 15 upstream requests
(including two same-host script redirects), 1,368,129 bytes, zero failures.
The successful second revision supports asynchronous page initialization as
the cause of the transient first-read result. No source/config/model changes
were made between probes and no failed native revision was retried.

[Polling receipt](openrouter-deployed-polling.jsonl), SHA256
`d8ce8b67d4366967cd6951cfd449709a35e9ad0e2b8ab67b860722b143a0b601`;
runner SHA256 `6878985bba5737ce72bb564cb06b006dfb81a2fedcc2fba487ac3863edc3345b`.
Both fresh anonymous leases used the installed broker and private HTTPS API,
submitted no input/action and confirmed cancellation/cleanup. Code and config
remained unchanged. The probes establish live composition, not owner login,
Telegram/mobile rendering or native runtime continuation. The poll diagnostic
also checked that increasing model-call counts coincided with new composition
revisions. A single API read can observe more than one revision because generic
provider verification and form rendering each request a snapshot.
