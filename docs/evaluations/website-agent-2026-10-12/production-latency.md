# Production website-form latency investigation — 2026-10-12

A read-only inspection of website-tool events from the run starting at
09:20:42.180 UTC reproduced one rejected component plan locally. No other tool
arguments, user messages, credentials or page text were exported. Times below
are seconds from run start, not network latency measurements.

| Website operation | Start | Result | Observed outcome |
| --- | ---: | ---: | --- |
| Delegated task | 6.059 | 17.577 | Same workspace returned with `requires_agent` / `unsupported`. |
| Native action | 25.842 | 26.195 | Accepted; sanitized scene unchanged, revision advanced. |
| Readiness wait | 33.408 | 33.602 | Modal now visible: controls changed from 1 to 5, actions from 64 to 10. |
| Native action | 44.533 | 44.832 | Existing-account step with 1 control and 6 actions. |
| Present plan | 54.749 | 54.861 | `invalid_plan`; two secondary buttons had different native form owners. |
| Observe | 61.237 | 61.333 | Exactly the same observation and revision as before the rejected plan. |
| Corrected present | 71.861 | 131.195 | Locally valid plan retained only the primary action; native call eventually returned `expired`. |

The failed plan's catalogue, revision and one compatible EmailInput selection
were valid. Its primary button belonged to a native form, while both secondary
buttons had null form owners. The unchanged compiler rejected those associations.
Removing the two alternatives made the plan compile against the exact same
observation. The rejection-to-corrected-presentation interval was **17.000 s**;
the rejection itself took roughly 0.1 s. This establishes a structural planning
error, not a slow compiler or failed email submission. No authentication claim
follows from a valid plan or a pending private form.

The readiness call itself took **194 ms**. There were **7.213 s** between the
first action's result and that call, then **10.931 s** before the next action.
It performed a useful fresh read after delayed hydration; it did not spend
those seconds sleeping in the broker. The trace does not expose the model's
reasoning, so its motivation for choosing `wait` is not asserted.

The first latency source change keeps the immediate post-action read. Only an unchanged
sanitized scene, stage and progress enable an additional read-only window of
at most two seconds, polling every 100 ms within the existing locks and broker
deadline. A changed scene returns immediately. No click, navigation or model
call is repeated. Cancel/close/private-input transitions retain their guards.
The tool description now explicitly requires every secondary action's native
owner to equal the anchor's owner; null is not a wildcard. No compiler permission
or broker/gateway error schema was expanded.

The accompanying source change adds bounded `delegation_steps` and
`delegation_calls` counters to future native escalation results. Planner
abstention becomes the fixed `no_candidate` reason, while a trusted blocked or
challenge stage still reports `unsupported`. No raw inference or page payload
is logged. These new diagnostics cannot retroactively resolve this older trace.

Intercepted Chromium regressions cover delayed form hydration after exactly
one click, unchanged-scene timeout, a stalled additional read, cancellation, close, concurrent stale private
input and the mismatched-secondary-action rejection/recovery. These local
source checks alone do not establish a live latency improvement.

For a separately authorized model experiment, one pre-input, broker-validated
observation was preserved only in a private directory outside Git, with its
opaque references and revision replaced by canonical synthetic references.
It has 1 email control, 64 actions, 21 text entries and 22 overflow actions;
58 eligible anchors produce 118 next-step criteria. No input values, option
values, request/run/owner identifiers or other database contents were copied.

**This is not a proven replay of Jev's original input.** The gateway obtains a
fresh observation after the delegated task stops. The task's earlier planning
input and call count are absent from these website-tool results. The fixed
`unsupported` error therefore does not by itself distinguish a model abstention
from an earlier trusted unsupported stage. The reconstruction preserves realistic
candidate coverage for a fresh experiment, without asserting byte identity to
the original inference or explaining its decision from unavailable evidence.

## First deployed OpenRouter regression

One new trial on release `f4fd760` used the public homepage, the real
subscription-backed native model, production MCP/broker, and shipped HTTPS
private renderer. Runtime digest:
`90605736f2f77227cbe23d47897359ba34717cb78587ec543ecb51794339d916`.
The owner-approved anonymous-resource configuration was unchanged. The frozen
external v4 harness differs from v2 only in its protocol label, explicit enabled
resource-policy check/metadata, and forwarding the new escalation counters.
It approximates the gateway's dispatch around a synthetic scoped workspace;
it does not verify the actual Telegram gateway database or owner flow.

The [unchanged first receipt](native-mcp-openrouter-latency-v4-first.jsonl)
records an empty Email address field and Continue button appearing after
**59.728528 s** from the native turn request. The empty screenshot was inspected
independently. This was **GPT fallback**, not Jev-only success or authentication:

- Jev made 1 call, took 494 ms, cost $0.000424578, and returned `no_candidate`.
- The parent made 7 tool calls: 1 delegation and 6 fallback calls.
- There were **0 `invalid_plan`** errors and **1 `stale_form`** error.
- Model gaps, including runtime transport, totaled **48.932144 s**.
- Human inputs and website submissions both remained **0**.
- Native/MCP/lease/browser/context cleanup was confirmed; runtime and config
  hashes were unchanged. Total time including cleanup was 68.124139 s.

Receipt SHA256:
`02dc03729c7400dcec41a9a498855733681baddd1d4d2934dbe75b4e6628e83e`.
Empty screenshot SHA256:
`f4f5bd0b5dd41a6006a9dbdc535d979145c2892031b67a4955d71832cfe391ca`.
This single sample does not establish a speed improvement, and is not claimed
to outperform an earlier approximately 35-second harness result. Different
model decisions and page transitions remain visible in the preserved receipts.

An accepted navigation returned a **changed but empty** `unsupported_layout`
scene. The first readiness fix waited only on unchanged scenes, so it returned
that transient state immediately. A later explicit observation found the modal
with 5 controls and 10 actions. The subsequent source correction extends the
same two-second window to empty scenes only when the outer stage remains
`agent_working` or `unsupported_layout`. Intermediate changes do not renew the
deadline. Trusted blocked/challenge/authentication/human states return immediately;
the existing close, cancellation and private-input guards still apply. Actual
Chromium fixtures cover old page → empty DOM → input form and immediate return
from an empty provider-blocked page. No action or model call is repeated, and
the first trial above did not evaluate this follow-up. Its later deployed
regression is recorded separately below.

## First deployed recovery regression

One trial on release `a774e7e`, runtime
`486bd9b9bce74e349000900bc0c519ebbaf09f91e19f051e07602a5219559e9d`,
used the **same unchanged frozen v4 harness** and owner-approved configuration.
There was no retry. The [first recovery receipt](native-mcp-openrouter-recovery-first.jsonl)
preserves the actual result and all operation outcomes.

| Measurement | Earlier v4 first trial | Recovery first trial |
| --- | ---: | ---: |
| Native task send → first rendered input | 59.728528 s | 40.291048 s |
| Parent tool calls, including delegation | 7 | 6 |
| Fallback tool calls | 6 | 5 |
| Separate fallback `observe` / `wait` calls | 2 / 0 | 0 / 0 |
| `stale_form` errors carrying a fresh observation | 0 of 1 | 2 of 2 |
| `invalid_plan` errors | 0 | 0 |
| Model gaps, including runtime transport | 48.932144 s | 30.446128 s |

Accepted navigation returned the hydrated modal; both stale errors returned
fresh observations without bypassing revision checks. GPT continued with new
proposals and no separate read calls. Jev still abstained: **1 call, 445 ms,
$0.000428022**, then `no_candidate`. Total time including cleanup was 48.841073 s.
Inputs/submissions were zero; native/MCP/lease/browser/context cleanup and
unchanged runtime/configuration were confirmed. These single samples do not
isolate a general speedup from varying model gaps/page timing, or outperform the
earlier approximately 35-second result.

**Visibility limitation:** despite its name, `first_usable_input_ui` checks
visibility, not enabled/interactable controls. The final screenshot shows empty
Email address + Continue with apparently disabled buttons; polling is a possible
but unverified cause. Report this as **first rendered input**. The older reserved-
identifier submission/continuation test is separate evidence from another
deployment. Neither screenshot establishes current owner authentication or actual
Telegram gateway acceptance.

Receipt SHA256:
`62a6b4ec02a7e3f70b3d9cf1a1d613005f5034060b7a19a3386bd04adc2f89c8`.
Empty screenshot SHA256:
`3ea91badf610a262b1a7ede0c73e9f3e43697bf2bfaa5be6732d9902a6c2ac13`.
No additional live trial replaced either first result.

## Separate current-runtime interactivity and negative continuation check

One [v3.2 functional trial](native-mcp-openrouter-interactive-negative-v3.2.jsonl)
on the same `a774e7e` runtime verified more than visibility. Before both typing
and clicking, the private renderer reported `input_enabled=true`,
`cancel_enabled=true`, `action_enabled=true` and `login_aria_busy=false`.
The first empty form rendered at **43.300394 s**. Exactly one reserved
non-account identifier was entered through the private UI and exactly **one
POST** was sent, with no retry. HTTP 200 plus broker `event_seq=1`, `submitted`
and `after_input=true` confirmed acceptance. GPT then presented a new empty
email/Continue form with the site's validation error, **13.351434 s after POST**.

Jev made **2 calls totaling 870 ms and $0.000869190**, then abstained; GPT
fallback handled presentation and continuation in the same workspace. The CDP
response-body read was unavailable; HTTP status and broker progress supplied
the independent acceptance evidence without replaying submission. No owner
credentials were used. The reserved identifier was absent from native transport
and the metadata receipt; no raw Jev payload scan was performed. All test
native/MCP/lease/browser scopes closed, with runtime/configuration unchanged.

This separate trial resolves interactability for **this tested flow only**. It
does not replace the preserved visibility-only result, prove account
authentication, or establish actual Telegram gateway acceptance.

Harness SHA256:
`043ac8f42e253ee78e8c35f50731f2d3bd2cb1c4d25afcbd5e3be1313f3bf20b`.
Unmodified receipt SHA256:
`21aa8daf9ea72d569be6e6b90a88a665369bf8ad06bffbe5c9b76a1ede118213`.
Empty post-validation screenshot SHA256:
`61298a3f60abf0424be6732fe3cd075b8566b9b371a8deaa50067b1580fab0ff`.

## Subsequent owner failure

The owner subsequently reported that the second step of their own login showed
only generic completion confirmation and cancellation, and that alternative
sign-in methods were missing. This is a failed owner continuation. The reserved
invalid-identifier regression above covered site validation and re-entry, not
the valid-account next step, and cannot establish that this failure was fixed.
Full-surface action binding and the premature review transition require further
investigation and implementation.
