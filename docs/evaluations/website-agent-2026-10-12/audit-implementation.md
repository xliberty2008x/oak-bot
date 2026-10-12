# Independent audit implementation

The owner requested implementation of the independent audit dated 2026-10-12,
then supplied an amended delivery. Its SHA-256 is
`e368a68fc4c8716603cc73da41ddf4c587bd5bdbba0abcf3a541ea5a45d1de7a`.
The amendment includes portable source excerpts and distinguishes independent
anonymous upstream observations from the owner's private workflow. It is a
requirements/evidence reference, not proof of the resulting implementation.

Owner authentication through the dynamic form remains unverified. The previous
deployed code was `a774e7e`; its invalid-identifier continuation check does not
cover a valid-account method chooser, password or OTP transition. That evidence
and the subsequent owner failure remain in [production-latency.md](production-latency.md).

The subsequent explicit Oak denial on the owner's Google transition is addressed
by the general [private origin-approval follow-up](private-origin-approval.md).
That change separates root task identity from the current credential recipient;
the earlier V6 acceptance remains evidence for its original frozen revision.

## Source changes

| Requirement | Implementation boundary |
| --- | --- |
| Complete relevant area, including sibling methods | Collector supplies bounded observed surface membership. Jev selects the semantic surface, rather than a smallest-form heuristic. Active modal scope is authoritative. Compiler requires exact coverage of the selected surface. This alone does not prove actual DOM completeness. |
| Per-action private values | Every surface action has native-owner-checked `field_refs`. Renderer serializes only that action's fields; broker and DOM validate again. Links and value-free alternatives send an empty map. Native submits include all observed owner fields, including optional external associations. |
| Show unsupported alternatives honestly | Policy-denied actions remain visible and disabled with a visible, accessible explanation. Display grants no additional credential destinations. |
| Resume after intermediate steps | Agentic absence of inputs cannot create generic completion confirmation. Action-only scenes are planned; empty transitions receive a bounded readiness wait. Human input resumes the same browser context and task. |
| Explicit owner completion remains possible | A separate fixed private finish action becomes available after an accepted human step. It sends only a fresh presentation revision and retains the session as `owner_confirmed`, never provider-verified `authenticated`. It does not replace or omit observed site controls. |
| Task goal, rather than an immutable login objective | Optional generic public goal goes from native tool through MCP start to a lease-bound planner. It cannot reset an existing job or grant authority. Omission preserves existing-account sign-in compatibility. Human values remain private. |
| Avoid unnecessary component judgments | Standard unambiguous types/roles map directly to the fixed widgets. Semantic anchor, ambiguous surface and genuinely ambiguous field/binding judgments remain real Jev requests. |
| Explicit partial-form limits | Six supported field types, 32 controls, 64 actions, 24 text entries, 16 candidate surfaces. Unsupported, disabled/read-only and excess visible controls count as omissions and produce a partial-form notice. Any overflow fails complete-form acceptance. |

The legacy single-owner compiler remains necessary for explicitly legacy callers;
agentic real DOM plans must use `surface_ref` and negotiate `website-v2`.
Server validation, stale revision checks, once-only dispatch, cancellation and
lease binding remain authoritative.

## Verification gates

These gates are separate; none can substitute for another:

1. Source/compiler, actual intercepted Chromium, renderer and MCP tests.
2. Official pinned A2UI Lit package compatibility, catalogue/theme and CSP gate.
3. After code freeze, one previously unseen multi-step fixture with real Jev,
   private human simulation, all-channel canary scanning, stale/once/cancel and
   full supported-area comparison. Preserve the first outcome.
4. Real anonymous OpenRouter full-surface observation and one reserved invalid
   identifier submission, separately from owner-account authentication.
5. Owner's valid-account continuation and authentication through the private UI.

Production cutover and gates 1–2 are complete within the local Chromium,
published-package, catalogue and CSP scope; mobile Telegram WebView, iOS/WebKit
and pixel parity remain untested. The strict OpenRouter gate remains
failed because this VM's upstream DOM did not expose the historical passkey
alternative; the separate invalid-identifier continuation diagnostic passed its
narrower scope. The independent fixture gate passed once on the new frozen
scenario below; the owner-account gate remains unsuccessful/unverified. No
result establishes universal website compatibility or completed owner login.

Broader verification exposed and corrected compatibility defects beyond the
original two symptoms: legacy v1 received v2-only action metadata, optional
externally associated native fields could fall outside a submit surface,
32 Select controls exceeded the old private redaction-result count, and ASCII
JSON escaping made valid Unicode-heavy forms exceed the private response cap.
The legacy recovery suite retained its original mocked choices and passed after
the actual v1/v2 projection was separated. The private form response uses UTF-8
serialization; secret masking and input bounds remain independent.

## Frozen source checks

The final affected-source pass ran 215 tests. The first invocation passed 165
and explicitly skipped 50 opt-in browser cases; the follow-up enabled Chromium
and passed those 50/50 in 78.396 seconds. No skipped case is counted as passed
from the first invocation. This includes private renderer, agent/broker, native
tool/MCP transport, same-context continuation/completion, surface membership and
per-action ownership, and the ordinary Lit browser adapter. The preceding focused
planner (81), legacy recovery (18) and private Select/redaction (7) suites passed
after the compatibility corrections described above. These are local protocol
and browser tests, not model execution or provider authentication.

Read-only peer review checked the goal path and compiler/DOM action bindings;
secret-pattern scanning of the 59 pending files found no API keys, private keys
or JWTs. Existing and new synthetic test literals remain test data. The ordinary
renderer bundle was reproduced from the pinned lockfile and its separate gate
is recorded in [the Lit evaluation](../a2ui-lit-2026-10-12/README.md).

The earlier broad run was terminated before a suite summary; two subsequent
groups exposed compatibility failures. Those failures led to the corrections
and focused rechecks above. This checkpoint does not claim one clean complete
repository-suite invocation.

## Deployment and actual OpenRouter results

The source freeze was committed as `9c0be14`; a documentation-only correction
is `12f97ef`. The latter was deployed after two idle checks and an idle check
after stopping the canonical gateway. Existing configuration and five uncertain
intake records were preserved. Broker health and the restarted gateway passed.
The executor manifest changed only code root, file manifest and policy digest:

- Product tree digest: `6c070b7133f09db35311a9afacc8735a46abae84aaec0caf784046786133b499`
- Policy digest: `9b37ef24d5d31949f20bbe08193c4bf730b198fe3e0c453829e38a6cb13e109d`
- Release identity: `686bf2cf2a230c88a5ed`, 135 product files / 143 manifest files.

The first actual full-surface OpenRouter trial,
[v5.3 receipt](openrouter-full-surface-first-v5.3.jsonl), **failed** the strict
historical-alternative check. Email plus six actions rendered in 40.722 seconds
from the native turn; no identifier was entered or submitted. Jev abstained on
the homepage (556 ms), then the native model continued the same workspace.
This result is retained and is not replaced by the later diagnostic.

Two fresh anonymous read-only diagnostics on this VM found no passkey text node
in the upstream DOM; direct sign-in supplied GitHub, Google, MetaMask, Continue
and Sign up. A modal also supplied Close modal. This differs from the audit's
independent upstream observation. The cause of that environmental difference is
not established; no missing action was invented and no browser capability was
spoofed.

A separate [v5.4 diagnostic](openrouter-full-surface-diagnostic-v5.4.jsonl) kept
the strict mismatch as **failed**, but continued the independently authorized
invalid-identifier phase. It confirmed both complete selected observed surfaces,
the actual private POST's action/revision/field-key binding, one submission,
HTTP 200, `Identifier is invalid`, and a second full private form. First input UI
was 31.293 seconds from the native turn; POST-to-second UI was 13.273 seconds.
Jev abstained (590 ms); native model gaps totalled 31.842 seconds. These are
single instrumented trials, not an isolated performance comparison or a
sub-second end-to-end claim.

[The inspected empty screenshot](openrouter-full-surface-diagnostic-v5.4.png)
shows email, all six observed actions, separate explicit owner completion and
cancel after the invalid response. It contains no owner account data. Both
trials confirmed workspace/browser cleanup and unchanged product/configuration.
Their dispatcher was harness-emulated: real native subscription model, production
MCP/broker/private UI, but no real Controller or Telegram verification. Native
transport/receipt canary checks passed; raw Jev payload scanning was not performed
in these two OpenRouter trials. The independent fixture gate separately measures
that channel. Neither result establishes valid-account continuation or login.

## Independent fixture: preserve the infrastructure failure

After the product freeze, a separate author created the held-out fixture without
reading the product or planner and without using an existing fixture template.
The manifest SHA-256 is
`c075786268449f10e10904dd93ffdb229de870b1bd3e1650570beb6ed4ae02e0`;
the seven assets' aggregate hash is
`349c419811f5ed70e6a9d715336e028b1dfc71298ecba0cc70443b4357063664`.
Its five human actions cover an alternative after unrelated drafts, optional
fields, an action-only step, password and OTP, then a final cancellable form.
Only the homepage and public task goal are supplied to the actual agent.

The [first instrumented trial](heldout-first-v1.jsonl) failed before `turn/start`, fixture navigation or
Jev execution: the observer required a native rollout file before the first
turn could create it. It recorded zero fixture contexts, Jev calls, requests and
leases. The observer then terminated its gateway before the gateway's cleanup
acknowledgement. The immutable first receipt SHA-256 is
`96acbfd3a5610c2e164b96b515d6754999a4aeb71ab82366496c286eef9aff15`.
Its `cleanup_confirmed:false` remains unchanged; it is not an acceptance PASS.
The separate [post-run OS observation](heldout-v1-postrun-closure.json) found the
owned child PIDs and sockets absent and only pre-existing production executor
instances remaining. It does not recover the missing gateway acknowledgement
or native log observation.

A separately frozen infrastructure regression uses two-phase native thread
registration and exact-thread rollout binding after `turn/start`, before any
private test input. It scans the log from byte zero and allows cooperative
cleanup before forced termination of its own child processes. The product,
fixture, goal and planner are unchanged. This is explicitly a regression after
the first infrastructure failure, never a relabelled first successful trial.

The [v2 infrastructure regression](heldout-regression-v2.jsonl) bound and
scanned the exact new native thread log (54,776 bytes), but still opened no
website and made no Jev request. Both website tools were registered correctly.
Its native `exec` attempted `tools.oak_website_task`, but the harness had explicitly
disabled the code-mode host and received `code-mode host is disabled`.
The native turn completed at 8.729 seconds; the resulting 45-second input-request
wait timed out. Gateway cleanup was reported false;
the receipt omitted individual cleanup phases, so its exact cause is not proved.
The [separate OS closure check](heldout-v2-postrun-closure.json) confirmed absent
owned PIDs/sockets and only old production executor instances. Neither the
timeout nor clean OS closure is a form acceptance PASS. The original receipt and
product/fixture/configuration hashes remain unchanged.
The [installed native host contract](native-code-host-contract.md) confirms the
minimal harness correction: enable the stable, default-on `code_mode_host`,
retain experimental `code_mode:false`, the two client tools and empty native
environments. Static contract inspection is not a workflow PASS.

The [v3 regression](heldout-regression-v3.jsonl) made the real native delegation,
opened one fixture browser and attached all four pinned private UI assets, but
still made zero Jev calls. The exact native result was the fixed delegation-
unavailable wrapper; first complete-form inventory timed out after 50 seconds.
Both children and the exact native terminal turn confirmed clean completion.

The cause was reproduced with the unchanged MCP handler in memory: the harness
scanner's eager `request.read()` exhausted the body before MCP's streaming
reader. Initialization consequently returned HTTP 400 / JSON-RPC `-32700`,
`Invalid JSON message`. The receipt's one MCP request/response and zero planner
instances agree with this reproduction; the raw response itself was scanned,
not retained. The observer must tee bytes as consumed rather than pre-read the
stream. This requires no product, fixture, goal or budget change.

The [v4 regression](heldout-regression-v4.jsonl) used a streaming tee whose
equivalence with the unchanged MCP reader was checked offline. It reached two
accepted private human steps in one context, rejected one stale revision, and
made ten real Jev requests. It then failed the complete-inventory check on the
action-only third step. The first two accepted steps and three inventory checks
are partial evidence, not an acceptance PASS. All scanned channels, including
the actual Jev requests/results and exact native log, had zero canary findings.
Both child cleanup acknowledgements and native interruption were confirmed;
product, fixture, configuration and all prior receipts remained unchanged.

The receipt did not retain the mismatching form or selected-surface metadata,
so the exact third-step cause is not established by that run. A separate
read-only Chromium reconstruction of the frozen third-step template found the
correct relevant zero-field/two-action regions, alongside broader regions that
also contained an unrelated bulletin signup. It made no model calls or human
submissions and does not prove which region the v4 model selected. The next
diagnostic must retain bounded opaque selection/membership and fixed mismatch
reasons rather than infer them from a timeout.

The [v5 diagnostic](heldout-diagnostic-v5.jsonl) preserves all earlier receipts
and is explicitly not a fresh acceptance trial. It failed after 97.020 seconds,
with cleanup confirmed and no canaries in the observed channels. Its ten Jev
requests totalled 3.074 seconds; nine known costs totalled $0.000849324 and the
invalid-response attempt had unknown cost. This does not measure provider
internal processing or constitute an isolated latency comparison.

The recorded third-step surface choice selected a broad container containing
one bulletin field and three actions; both relevant region candidates contained
zero fields and the two expected choices. Jev then returned an invalid component
response. The existing native fallback showed the correct zero-field choices,
password step and OTP step in the original browser. Five private submissions
were accepted and the stale check passed. Six inventory matches include the
second check after the stale probe, not six distinct successful stages.

After the OTP submission, the fresh native observation contained the final
surface, but the native turn completed without another presentation. The private
UI remained `agent_working`, so the required final inventory/cancel/late checks
were not reached. This also exposed a mismatch in the test design: the supplied
goal was only to sign in; the final page said the user was signed in and asked
for Account display name / optional Recovery email / Save preferences. Rendering
that editing step exceeds the stated login goal. This is a plausible stopping
point, not proof of authentication, and cannot turn the immutable result into
PASS. Any new acceptance fixture must align its final human step with its public
task goal. The result's default `same_workspace:false` is set true only after all
final assertions; it does not demonstrate an actual workspace replacement.

## Owner continuation report and OAuth boundary

The owner subsequently selected Google and reported that the next step blocked
the login. This is a failed owner workflow, not completed authentication.
No owner credentials, page contents or private browser state were inspected.
Source review found that the generic blocked stage conflates remote rejection,
failed document loading and Oak's origin boundary. Consequently the report
alone cannot establish that Google itself rejected the login.

The current anonymous browser prohibits external top-level navigation and
closes popups. The separately approved Clerk host permits bounded script/fetch
traffic; it does not grant OAuth navigation. A script-driven method button can
therefore appear in the initial form before its subsequent external transition
is rejected. The audit explicitly allows unavailable methods, but they must be
described honestly. This is an execution-capability limit, independently of
whether the selected surface renders every observed method. No additional
authentication destination or browser state was granted by these diagnostics.

The follow-up source correction uses fixed private messages for local origin
denial, closed popup, remote HTTP failure, recognized rejection text, network
failure and local request-handler failure. An unknown cause remains neutral.
The local cause takes priority over the network event caused by Oak's own abort.
The renderer admits only the exact fixed messages; raw exceptions and page text
cannot enter this blocked-state display. Recognized blocks/challenges also veto
the separate owner-finish action, including its server-side revision check.
There is no OAuth grant, automatic retry or new navigation capability.

Seventy-one distinct focused checks passed: twelve state/intercepted-Chromium
and document-error cases, then fifty-nine private Node renderer and agent
browser cases with no skips. The first invocation had skipped twenty-seven
Node cases because Node was absent from PATH; the second explicitly supplied
the installed Node binary and executed them. Neither invocation used models,
external authentication or the owner's account. Deployment of this follow-up
must be recorded separately from the already deployed surface change.

## Follow-up source freeze and deployment

Commit `70dca79778f2701af47ef5470605ddbb2fa011dd` includes the fixed block
messages and the [candidate-evidence correction](surface-candidate-evidence.md).
Every surface Choice description now contains its own sanitized observed
controls, actions and text. Only structurally identical aliases are collapsed;
broader candidates remain available to the model. This changes evidence
presentation, not the compiler, goal, permissions or semantic selection rules.
Eighty-six focused checks passed, including eight intercepted Chromium cases.
No inference accompanied those tests; improved model accuracy remained unproven
at freeze.

The commit was deployed at approximately 12:57 UTC after two idle observations
and another idle observation after the canonical gateway stop. The five uncertain
intake records were preserved. Fresh execution attestation passed, broker health
was ready, and the canonical gateway restarted with worker running. Independent
review confirmed unchanged broker configuration, sockets/firewall, authentication
hosts, resource grants, model pins and key location. Only release paths, their
file manifest and policy digest changed.

- Product tree digest: `300b4427268ff1c35c8051680ff4d1df4e496013d54eb754e4155142be25ea5d`
- Policy digest: `8db6645df53b1622568215b307b609eed18d95b44760b36ec445b3b3b456be4c`
- Profile SHA-256: `d32b7cad5612df9729a9183cec31b53cd24b9adb8818a38a7a689aa8308bb3f7`
- Broker configuration SHA-256: `456adca7c35e6ae0f23be403b31ec620c82fb0bcae0ca2f4be2fb7dba5fd87bf`
- Release identity: `a57535260dccb2eab749`, 136 product files / 143 manifest files.

A new independent fixture was commissioned only after this code freeze. Its
author received a neutral interface contract without prior fixtures, product,
planner prompts or model results. The public goal must explicitly include the
final account-related form for human review without saving. The first completed
authored version and its first real trial must remain unchanged, including any
failure. Earlier V1–V5 results remain separately pinned and failed.

The author's first completed HTML omitted the contract's hidden password input.
Static review found this before any model/browser trial. That version remains
unchanged (HTML SHA-256
`f6181858404e72bddc30f2ddf7b1cdf3df02025babb0bb73e444eb5896dc91df`).
The author made a separate pretrial revision adding only one native password
inside a `hidden aria-hidden="true"` ancestor. The manifest, public goal, routes,
visible inventories, canaries and behavior were byte-identical. This was a
contract correction, not tuning against a model outcome. The revised HTML
SHA-256 is `9c04da89a961d6af05f6fd50c945a94ba6e96eab05d27488f9a770038bfc4209`;
manifest SHA-256 is
`1f48339ce2d234ff1c5b8ab5fbce1b6582d5f70f1690dfe49e84783ac7a45459`.
The first actual trial uses that frozen revision. Its goal explicitly requests
sign-in followed by an invoice-contact update form for human review without
saving; the final visible form directly serves that goal.

## Fresh independent acceptance result

The [V6 first real trial](heldout-first-v6.jsonl) **passed** in 76.974 seconds.
Its SHA-256 is
`256c07d6fb5ac425ea7197bbd5737b125fbd7b537701088de4f29afa4dfa9071`;
the exclusive first-trial claim SHA-256 is
`8ed0e0fdd8a5ef45b6cda6d04b559b1452a75887bd460c7aa6ac036e9af41827`.
This was the first model/browser execution of that frozen fixture, with the
real Controller, exact subscription-backed `gpt-6.1-sol`, MCP, production Jev
configuration, browser executor and private UI assets. Fixture HTTP responses
were served locally behind the unchanged origin guard; this is not a public
website or provider-authentication test.

All six distinct surfaces matched the independently authored full visible
field/action inventories, their per-action field ownership, availability and
explanations. The seven inventory checks include a second check after the stale
probe. The initial password/email form included all sibling alternatives;
its hidden password was absent. Choosing the value-free email method after
private drafts consumed no fields. Five accepted private submissions in total
occurred once each in one browser context/workspace. Email, OTP, optional controls,
Select and an intermediate zero-field/two-action screen continued to the final
four-field/two-action contact form. Cancellation and stale/late submission
rejection passed; no final save or owner-completion action occurred.

The first private form appeared in 18.442 seconds. Eleven real Jev requests
totalled 3.590 seconds, individually 245–461 ms; reported known cost totalled
$0.000854238 with no unknown-cost attempts. Jev selected complete regions for
the first three human forms, then abstained on the action-only screen. Three
native fallback calls correctly presented that screen, the invoice-preparation
form and the final contact form in the existing task. The PASS therefore covers
the combined agent workflow, not Jev alone. Different fixtures and uncontrolled
external conditions prevent attributing an accuracy or speed improvement to the
payload change from this one result.

Every observed nonempty canary channel had zero findings: real Jev requests and
results, broker control/MCP, native requests/events and the exact 100,702-byte
fresh native log, AG-UI/A2UI state, full isolated Controller database, and child
output/receipts. Empty stderr/Python-log channels do not constitute additional
traffic coverage. Provider-internal state was not observable. Both child cleanup
acknowledgements, exact native turn interruption and owned-process/socket/
workspace cleanup were confirmed. Product, configuration, profile, fixture,
runners and all V1–V5 receipt/claim hashes remained unchanged.

The [initial empty private form](heldout-v6-empty-initial.png) was captured before
any drafts (SHA-256
`6efd7ebae1b2297b3fd1d3fd28d44bb9fa0236c6cefcc17790e8b1fd98826387`).
Independent metadata review found no false-PASS concern. This single bounded
result establishes neither universal reliability, a subsecond form workflow,
live OAuth, Telegram/mobile acceptance nor successful owner authentication.

## OpenRouter check after follow-up deployment

The [separate live regression](openrouter-70dca79-regression.jsonl) reused the
unchanged strict V5.3 harness against product `70dca79`, with a new exclusive
receipt. Its SHA-256 is
`f8e96e45e3d335ba03c6d3e2319649ae25ef86f7206e538156cc17442ce4d41c`.
It **failed** the same historical passkey-alternative gate and made zero private
submissions or owner inputs. Email and six observed actions rendered; no passkey
was invented. Configuration/product and the initial failure receipt remained
unchanged; broker/MCP/browser/native cleanup was confirmed.

First usable input appeared 36.072 seconds after the native turn was sent
(37.925 seconds from experiment start). Jev's homepage anchor decision abstained
after 515 ms, so the subscription GPT continued through four fallback calls.
The receipt records 27.613 seconds of model/transport gaps over the whole run;
these overlap other spans and cannot simply be subtracted from first-form time.
The prior strict run took 40.722 seconds to its first input. This is a direct
observation with the same harness, not an isolated causal speed comparison:
model decisions, transport and the live site are uncontrolled. The surface
payload correction does not address this earlier homepage-anchor abstention.

This runner uses the real native runtime, production broker/MCP and private UI,
but emulates dispatch rather than using the full Controller tested by V6.
Its reflection checks cover the native transport and durable receipt; unlike
V6 it does not scan raw Jev payloads. It establishes neither successful account
authentication nor valid-identifier continuation on the follow-up release.
