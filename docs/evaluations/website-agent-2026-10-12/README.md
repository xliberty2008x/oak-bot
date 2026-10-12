# Native agent website workspace — 2026-10-12

The latest [compact sign-in interface](compact-signin.md) replaces duplicate
containers with one current-domain modal and keeps the full observed step's
fields and alternatives. [Varied-site results](compact-sites.md) exclude
OpenRouter and retain failures, real Jev proposals and post-input rebuilding.
[Deployment verification](compact-signin-deployment.md) records the installed
code, exact public assets and fresh isolation checks.

## Trigger and reproduced baseline

The owner's OpenRouter private form showed `composition_unavailable` before
displaying fields. The actual request targeted the homepage, not `/sign-in`.
An anonymous reproduction using the installed private iframe and real Jev
confirmed the same failure: two abstentions, no fields or actions, and a
clarification candidate overflow. This is distinct from the earlier successful
direct `/sign-in` form read. The failing homepage receipt is retained locally
with SHA256 `9d80c410cd26cf3732ee2b9ef77597565135b08f98fed137db1faa5eb13211cb`.
Its UI observer also reported four response-body collection errors; the visible
UI, recorded 409 and broker diagnostics independently established the product
failure. It is not reclassified as a clean test run.

The architectural issue was that `oak_website_form` suspended the native GPT
tool until the whole login finished. GPT never saw intermediate pages, while
Jev was asked to choose a login action on the homepage without navigating to
the hidden login UI. The correction is a staged native-agent tool using the
same subscription-backed runtime and existing component catalogue. See
[the workspace contract](../../../DESIGN.md#native-agent-workspace).

## Delegation and latency investigation

The later owner report of 1–2 minutes was checked against actual production
events in [production-latency.md](production-latency.md). That investigation
separates native model round trips, stale/invalid plans and DOM hydration from
Jev inference. [Bounded input experiments](jev-input-experiments.md) retain the
negative results; their experimental planner variants were not deployed.

The owner's subsequent 2–3 minute delay report prompted a measured GPT/Jev
comparison, documented in [latency-comparison.md](latency-comparison.md).
The same installed browser and private renderer were used. GPT reached the
OpenRouter email step in 81.214 seconds, with 76.261 seconds between native
tool responses and subsequent model calls. Its GitHub run took 21.334 seconds;
the first Jev GitHub run took 7.210 seconds. These are individual trials with
different task-start boundaries, not latency percentiles or account login.
All failed OpenRouter Jev trials remain in the evidence.

The source candidate now adds one native `oak_website_task` call delegating the
whole private workflow through the broker's MCP endpoint. The actual Jev loop
owns navigation, presentation and continuation after human input; the main
agent resumes only if it needs to handle an unsupported step. Browser and
private-value authority remain in the existing broker. A generic decimal
probability-total validation bug was corrected without changing its inclusive
1% tolerance. A public diagnostic subsequently found an unnecessary identity
provider switch despite existing sign-in fields; the semantic instructions now
prefer presenting a usable current sign-in step, keeping every permitted
candidate available. Neither correction is a site-specific route or selector.

Candidate verification: **815 tests passed, zero skipped, in 278.821 seconds**,
including Chromium and Node checks. Full-suite log SHA256:
`c94ee1c70fb26772c343d4af154bacd4667d2f5ddfbf3d8de1c3094da5abab1a`.
After the final semantic instructions and native tool guidance changed,
44 affected tests passed in 1.890 seconds; these overlap the full suite.
The subsequent bounded readiness-wait correction passed 55 affected task,
broker/MCP and gateway tests, zero skipped, in 2.856 seconds. It prevents an
unchanged empty interface from waiting until lease expiry after Jev chooses
wait or navigation leaves the same scene; no repeated paid request is made.
The real Chromium MCP fixture submits a synthetic identifier privately and
automatically receives the password form; its model responses are mocked.
Actual Jev site quality, deployed MCP operation and owner authentication remain
separate results. See [the delegated task contract](../../../DESIGN.md#delegated-jev-task).

## Acceptance protocol fixed before live trials

Use the actual installed broker, private renderer and a fresh subscription
`gpt-6.1-sol` thread with the real `oak_website_agent` tool description. Start
from the exact public homepage. The model must choose observed actions and
compose a grounded plan; the harness must not supply selectors, a login path,
an action sequence or a preselected form. Stop after visible private login
fields, an explicit blocked state, or the trial limit. Do not enter account
credentials or submit a registration, payment or destructive action.

The primary regression is `https://openrouter.ai`. Additional public homepage
trials are selected before execution using `random.Random(20261012).sample`
from this ordered pool: GitHub, GitLab, Wikipedia, Reddit, Stack Overflow,
Dropbox, Notion. The selected three are **Reddit, GitHub and Dropbox**. They
cover reuse of the same agent/tool contract; they are not a statistical estimate
of universal compatibility. Prior field-discovery experiments on any overlapping
site remain prior exposure, not a fresh held-out result.

All first outcomes, including failures and blocked resources, must remain in
the record. No additional authentication/CDN grants are included in this change.
The existing explicit OpenRouter → `clerk.openrouter.ai` grant is preserved.

## Evidence boundaries

Source tests exercise native continuation, private browser DOM operations,
renderer polling, validation, redaction and lifecycle. Live page discovery must
be reported separately from those tests. An isolated real-model harness does
not establish owner Telegram/iOS interaction or successful account login.
Actual owner credentials and final authentication remain outside this test.

## First deployed native-agent trial

Commit `89961c9` passed **712 tests**, with no skips, and was deployed as
release `7afc5a69c130bbe89875`. [Deployment metadata](deployment.json) separates
this code baseline from the live result.

The [first actual GPT trial on the OpenRouter homepage](openrouter-first.jsonl)
**failed the input-form acceptance criterion**. ChatGPT observed the page and
selected `Sign Up`, but the broker rejected navigation. The model corrected
one invalid tool argument shape, then presented the observed button privately
for human activation. The renderer worked and remained cancellable, but no
input fields appeared and no autonomous navigation completed. There were no
owner inputs, registration submissions or observer errors; cleanup was confirmed.

An independent anonymous DOM probe identified the general error: the standalone
button and an unrelated hidden search input both had no native form owner.
The navigation guard incorrectly treated equal null owners as an association.
The correction compares associated controls only when a real native owner
exists. It preserves protection for actual forms and all navigation after
private input. Chromium tests cover loose hidden/search/default controls,
real external `form=` associations and unchanged native input values. Tool
argument signatures and bounded expected-field errors were also clarified.
Any later OpenRouter run is a regression on this exposed case, not fresh evidence.

A separate anonymous exploratory click then found a second observation defect:
the opened modal had `role=dialog` and `aria-modal=true`, but the collector filled
all 64 action slots with the inactive background and omitted every modal action.
The page marked background nodes `inert`. The general correction excludes
inactive ancestry and selects the active modal before bounding the scene.
It keeps whole-document private-value masking and native form constraints.
This diagnostic supplied no action sequence to the model and is not acceptance
credit; the model regression still starts independently from the homepage.

## Agent regression and first random-site results

The corrected code `db7d365` was deployed as release `229231db6d2897635833`.
[Deployment metadata](deployment-regression.json) records its exact digest and
focused checks. The 712-test complete suite belongs to the preceding native
workspace baseline; the subsequent focused suites overlap and are not summed
as a new full-suite count.

In the [OpenRouter regression](openrouter-regression.jsonl), real GPT chose the
homepage entry, observed the active signup dialog, chose its sign-in action,
and presented an **email-first** form with Continue. Visual inspection of the
empty private renderer confirmed this. No site navigation sequence was given
to the model. There were no model tool errors, input values or submissions.
The receipt still says `failed`: the predeclared OpenRouter criterion wrongly
required email and password together. It is retained unchanged; the evidence
establishes autonomous navigation and first-step rendering, not a passed
combined-field criterion, post-identifier continuity, or account login.

The fixed random sample produced these first outcomes, with no retries:

| Public homepage | Observed result | Receipt |
| --- | --- | --- |
| GitHub | GPT navigated and rendered username/email plus password and Sign in | [First trial](github-first.jsonl) |
| Reddit | Initial observation was provider-blocked, with no controls or actions | [First trial](reddit-first.jsonl) |
| Dropbox | GPT navigated, then observed unsupported layout with no controls or actions | [First trial](dropbox-first.jsonl) |

Thus **one of three** sample sites produced a usable input handoff. All three
used the same native tool, broker and renderer; all closed their leases and
both browser contexts, with unchanged runtime/configuration. No input or
submission occurred. GitHub and Dropbox, plus the OpenRouter regression, had
an unexplained shell-wrapper exit 143 even though their native child runs
completed and persisted complete cleanup receipts. This is a process-wrapper
anomaly, not a clean process pass or an interrupted model outcome.

The renderer ran in Chromium with a 430 by 900 viewport. This is mobile viewport
simulation, not iOS or Telegram verification. The isolated harness forwards the
real tool operations to the installed broker; it does not exercise the gateway
production database dispatch. That dispatch has separate source tests.

## Real post-identifier continuation

The separate [negative-identifier trial](openrouter-negative-identifier.jsonl)
used the same deployed `db7d365` code and actual subscription model. The private
UI emulator submitted exactly one reserved nonexistent identifier on a verified
existing-account email-only step. It did not provide the value to GPT. The same
workspace returned `event_seq=1`, `after_input=true`, and submitted progress.
GPT observed the site response, selected its observed error, and presented a new
empty email form plus Continue. The private renderer visibly showed “Identifier
is invalid.” This verifies the previously failing after-email continuation,
including fresh model reasoning, without any account authentication claim.

The model also recovered one stale-form response before input by observing again;
no stale action was replayed. No owner credentials, password, OTP or second
submission were involved. The process exited zero; cleanup and unchanged
runtime/configuration were confirmed. Harness SHA256:
`c7a2a2d5fc616ca9dfc86ef6e46df885e0d225f03204aebac08dbac9838fc726`.
Receipt SHA256:
`c009d71b359c6425120144aae93c68a4ac93f563cecd3ba822eb5a3b81b69080`.

## Independent Dropbox diagnosis

A separate anonymous [diagnostic probe](dropbox-diagnosis.jsonl) is not a retry
or acceptance credit. After one observed sign-in navigation, the same-origin
document returned HTTP 200 and remained empty through 25 seconds: no controls,
actions or body text, with document loading complete. One script and 28
stylesheets from a public static host failed under the broker policy. There
was no cross-origin authentication navigation or unsupported control type.
The blocker was the general resource loader, which required per-origin static
host configuration and presented the resulting blank page as unsupported layout.

## Generic follow-up fixes and activation boundary

The next source change fixes two protocol restrictions without a site-specific
route: an action-only page after input can offer its observed next action, and
`wait` returns a ready unpresented page even without a new human event. Optional
owner confirmation remains separate, and a displayed form still waits for the
human. Adapter browser tests cover identifier → action-only Continue → password;
gateway tests cover readiness, pending forms, optional review and expiry.

A tested anonymous static-resource discovery candidate addresses the diagnosed
CDN limitation. It is **disabled by default** in both constructor and immutable
broker configuration. Hub coordinator report 5259 requires explicit owner
approval for the general executable-script scope before production activation.
The [concrete proposal](anonymous-resources-proposal.md) specifies that scope,
limits and the fact that loaded scripts can read later DOM input. Existing
Clerk/TikTok grants alone do not activate this candidate.


## Additional randomized sites and approved resource-policy regression

The later [seeded public-homepage evaluation](random-sites.md) retained first
failures on Wikipedia, GitLab and Stack Overflow: zero of three input handoffs.
After owner approval enabled anonymous resources, a separate Dropbox trial still
failed. An unbilled isolated diagnosis confirmed public CDN loading but found
the relay's shared 96-request cap exhausted before the login dependencies could
finish loading. The linked report preserves all five receipts, limits the claims
to observed behavior, and distinguishes this later activation from the earlier
disabled-by-default candidate described above.


The subsequent deployed 512-request-budget
[Dropbox regression](native-mcp-dropbox-resource-regression.jsonl) reached an
empty Email + Continue form in 30.501 seconds from native task send. Jev performed
two navigations and abstained; GPT presented the form in the same workspace.
Actual resource counters were 183/512 requests with zero quota rejections.
The [expanded report](random-sites.md#deployed-resource-budget-regression-empty-input-handoff)
keeps the earlier failure, records measured latency and cost separately from
local tests, and makes no account authentication claim.

## Production latency and recovery follow-up

The [latency investigation](production-latency.md) reproduces a native-form-owner
plan rejection and preserves two subsequent first OpenRouter trials. First
rendered input took **59.729 s**, then **40.291 s** after the recovery update.
The recovery visibility trial used no separate fallback `observe` or `wait` calls: both stale
action errors carried fresh observations, and accepted navigation returned the
hydrated modal. There were no invalid plans; model gaps still totaled **30.446 s**.
Jev abstained in both trials, so both required GPT fallback.

These are individual measurements, not a general speedup claim. The unchanged
harness's UI criterion checks visibility, not enabled/interactable controls;
its empty-input screenshot shows dimmed buttons. Both visibility trials used no
inputs or submissions and closed their test scopes. Earlier negative-identifier continuation
evidence remains separate; these trials do not establish current authentication,
owner login or actual Telegram gateway acceptance. Both receipts and all prior
failures are retained in the linked report.

A separate [current-runtime functional check](production-latency.md#separate-current-runtime-interactivity-and-negative-continuation-check)
verified enabled input/Continue/Cancel before one reserved invalid-identifier
submission, then the same workspace rendered a new empty form with the site's
validation error. It preserves the visibility-only measurements above and
establishes interactivity/negative continuation for that trial, not authentication
or Telegram acceptance.
