# Homepage-to-form latency experiment — 2026-10-12

This is a new timing experiment, separate from the preserved first acceptance
trials. A usable input handoff means that an accepted component plan produces
at least one empty identifier/password input and an action in the real private
renderer, without an error or a new-password role. Identifier-first login is
eligible. This does **not** establish successful authentication, a correct
credential submission, or owner Telegram/iOS behavior.

Each mode gets one fresh anonymous attempt from each public homepage,
`https://openrouter.ai/` and `https://github.com/`, in that order. There are no
credentials, form submissions, manually supplied selectors or login paths.
The installed broker, website adapter, HTTPS renderer, resource policy and
430 × 900 Chromium viewport are the same. Anonymous static discovery stays
off; the existing OpenRouter → Clerk authentication-host grant stays unchanged.
Installed code is `62b2305`, runtime digest
`a9dcdae2e9b8d8f223686e53b58ba3e3f84f94e157b9d77312a44f8422799dbd`,
configuration digest
`d0b5491842eda528b61cfbae7126c46b19467fec1a8b2106f740cdb47f1411fe`.

## GPT baseline

The subscription-backed `gpt-6.1-sol` receives the actual deployed dynamic-tool
description and reasons over successive observations. Time zero for the task
is the native `turn/start` write, after runtime startup/authentication and
thread creation. Both trials exited zero, completed their native turn, matched
the installed renderer hashes, confirmed lease cleanup, closed both browser
objects and left code/configuration unchanged.

| Measurement (seconds) | OpenRouter | GitHub |
| --- | ---: | ---: |
| Task sent → first usable input UI | 81.214 | 21.334 |
| Main harness start → first input UI | 83.379 | 28.118 |
| Sum of model/transport gaps before tool calls | 76.261 | 18.020 |
| Model tool calls | 9 | 4 |
| Native startup, including authentication | 2.069 | 6.714 |
| Authentication check within startup | 0.227 | 5.193 |
| Private renderer launch → iframe mounted | 1.130 | 1.033 |
| Private attach HTTP request, including site browser setup | 3.561 | 1.675 |
| Broker navigation operations, sum | 0.698 | 0.572 |
| Broker accepted presentation | 0.163 | 0.067 |
| Accepted presentation → first visible input | 1.373 | 1.297 |
| Test-only human wait, separately measured | 20.366 | 20.017 |

The 20-second wait occurs after presentation; the first-input timestamp is
observed while it is pending and does not include its remaining duration.
UI sampling is approximately every 500 ms. Timing spans overlap and must not
be added together. Model gaps include runtime transport and instrumentation,
not only model computation. Browser work can overlap those gaps.

OpenRouter recovered one `stale_form` navigation response by observing again;
that cost remains in this first timing result. Its empty screenshot showed
Email address and Continue. GitHub showed Username or email address, Password
and Sign in. No new-password field was presented. These observations verify
rendering, not completion of account login.

Raw monotonic records: [OpenRouter](timing-gpt-openrouter.jsonl) and
[GitHub](timing-gpt-github.jsonl). Frozen timing harness SHA256:
`c45bb33382a531d709c0f28888730cf7e76c4b8e7684fe46a3750254e62fb6c5`.
Receipt SHA256 values are respectively
`2bb4dbca974911c110b4cfd505e368330dd5bc954f8dd693e5cacff5cc336cad` and
`36542d2cd76b9caeefa646dafd9ec9c0d3be1a298b18ec7f4e5a02235422f4d8`.

## Delegated Jev comparison

The experimental `typesafe/jev-1.13-20260917` planner used fresh leases on the
same installed broker and renderer after both GPT trials had closed. It chose
navigation actions and component plans from the observed scene. The external
harness delegated once to its bounded planner loop; it did not exercise a
production GPT-to-Jev delegation tool. No Jev integration was deployed by this
experiment.

Its task clock starts when the harness sends `/create`; GPT's starts when the
native turn is sent. Jev has no corresponding native ChatGPT startup or model
decision to begin the lease. The different task origins, startup and component
timings remain explicit; this compares two execution paths, not isolated model
throughput. GPT had a 180-second turn timeout and 18 tool calls; the Jev harness
had a 220-second trial limit and 12 paid calls. None of these limits was reached.

| First timing attempt | GPT | Delegated Jev |
| --- | --- | --- |
| OpenRouter | Email-first handoff in 81.214 s | Failed `invalid_response` on first API call; no form |
| GitHub | Username/password handoff in 21.334 s | Username/password handoff in 7.210 s |

Jev's GitHub planner spans totalled **1.244 seconds**, with three API calls
reporting a total cost of **$0.000580776**. The broker navigation took 0.557
seconds, presentation 0.051 seconds, and accepted presentation to visible inputs
1.148 seconds. Its iframe mounted in 1.814 seconds. It stopped on the first
usable input UI and had no artificial human wait. Visual inspection confirmed
the empty identifier/password fields and Sign in action.

The failed OpenRouter request took 0.631 seconds in the planner; its cost was
unknown. That short failure is **not** a latency win or a completed handoff.
Both Jev trials confirmed cleanup, closed the browser/context and left the
installed runtime/configuration unchanged. Their served assets matched the
same renderer hashes. GitHub's attach HTTP request emitted a failure event
despite the later active broker state and successful rendering. Its cause is
unknown; the raw failed span is retained and is not counted as a successful
attach-duration measurement.

Raw first-attempt records: [OpenRouter](timing-jev-openrouter-first.jsonl) and
[GitHub](timing-jev-github-first.jsonl). Frozen external harness SHA256:
`1ac98130cfa5ff7ee88426152145fa29807e12f667db6179dd7b1dc790398c32`.
Planner SHA256:
`48166d503218cdc38339505b83248bc241ce966d842bac2e427af8b14e2bbd93`.
Receipt SHA256 values are respectively
`d5a34f9ed3ff25db278ef5a491128fa9f359740501774b126ad3e0b35fa661d2` and
`39c507572ed00fd39d4b004721adb465fbd424b9d132f7c8d6697e6dc6638c8c`.
Any follow-up OpenRouter diagnosis or corrected-planner regression is separate
evidence and must not replace this first comparison failure.

The subsequent [metadata-only diagnostic](jev-openrouter-validation-diagnostic.jsonl)
identified a generic numeric-validation defect: the returned probability sum
was 0.99, but binary rounding put its distance from 1 just outside the existing
0.01 tolerance. After correcting that boundary in isolated planner modules,
the [separate regression](jev-openrouter-corrected-regression.jsonl) accepted
four Jev responses and executed four guarded navigation actions, then ended
`planner_unsupported` with zero controls/actions and **no form**. Its planner
time was 1.627 seconds and reported API cost $0.00101094. The final observation
stage and selected labels were not recorded, so that receipt does not establish
why it reached the unsupported state. Cleanup was confirmed. The first failure
and corrected failure both remain failures; neither supplies an OpenRouter
Jev time-to-form value.

A further [navigation diagnostic](jev-openrouter-navigation-diagnostic.jsonl)
showed the planner selecting a social-provider action while an identifier input
was available, then reaching a blocked provider. A subsequent candidate
[WebsiteTask loop regression](jev-openrouter-task-regression.jsonl) used the
real delegated loop through an isolated proxy, but stopped after an abstention
with `requires_agent` / `unsupported`, still without input UI. These remain
separate failed diagnostics; neither tested deployed native-to-MCP dispatch.
They support continued work on generic planner decisions and observation
timing, not an OpenRouter success claim.
A later [readiness probe](jev-openrouter-readiness-diagnostic.jsonl) also
abstained on its first homepage observation, before navigation; it supplied no
post-navigation readiness samples and no input handoff. Its one call reported
$0.000424578, with cleanup confirmed.

## Installed native → MCP trials

After deployment of source `fb0af10`, two new first attempts exercised actual
subscription-backed GPT → installed MCP client/server → broker-owned Jev → the
private HTTPS renderer. There was no external planner worker or key-reading
harness. Runtime digest was
`3119e344afe4495d687b3e98d67ca2087d9e31d00e50288d89b0ea84f1ee23c2`;
the configuration and renderer hashes were unchanged. Anonymous resource
discovery remained disabled, with the existing explicit Clerk grant retained.

| Target and protocol | Result | Native task → first input | MCP task start → first input | Main-model calls |
| --- | --- | ---: | ---: | ---: |
| [OpenRouter, fallback-enabled v2](native-mcp-openrouter-fallback-first.jsonl) | Email-first handoff through GPT continuation | 35.492 s | 24.747 s, including fallback | 1 delegate + 4 continuation |
| [GitHub, Jev-only v1](native-mcp-github-jev-first.jsonl) | Username/password handoff through Jev | 14.332 s | 3.551 s | 1 delegate |

OpenRouter's Jev call abstained and returned `requires_agent` / `unsupported`.
The MCP session closed, and GPT received the sanitized observation for the same
active lease, then chose `act → observe → act → present`. No new task, lease or
hardcoded navigation route was introduced. Jev used 0.525 seconds and reported
$0.000428022; the fallback took 22.540 seconds from handoff to visible input,
including 20.541 seconds between native tool responses and subsequent calls.
This is a fallback success, **not a Jev-only OpenRouter success**.

GitHub needed one parent delegation and three broker Jev requests, totalling
1.183 seconds and $0.000605304. GPT received no page observations and made no
navigation or component decisions. The observer made one read-only verification
after rendering, which confirmed native username/current-password roles; this
verification was not returned to GPT.

The initial GPT decision gaps were 4.821 seconds on OpenRouter and 7.610 seconds
on GitHub. GPT process/authentication startup took 1.757 and 1.834 seconds,
respectively, outside the native-task-to-input interval. Renderer attach HTTP
spans were 3.534 and 1.722 seconds. These overlapping spans must not be added.
The harness ended its wait when empty usable input appeared; it did not wait
for or simulate owner input. Both native turns completed, served renderer hashes
matched, all MCP/browser/lease cleanup was confirmed, and runtime/configuration
remained unchanged.

Both records have `authentication_tested: false`, zero owner inputs and zero
submissions. They exercise a native model and deployed MCP/broker/renderer, but
use an external tool handler instead of the production gateway database and
Telegram dispatch. Production gateway dispatch is covered separately by tests;
these runs do not establish Telegram/iOS operation or account authentication.
The protocols also differ intentionally: OpenRouter permits native fallback,
whereas GitHub gives GPT only the delegate tool. They are individual integration
observations, not a controlled cross-site speed comparison.

Frozen harness SHA256 values: v1
`9a37ec5058e31d4956657853e11befcc323d95ec80d725b8a10b53dfbd797730`;
v2 `7f979d6984fa33dd215b5dc214c632e979391e3b6e29f0b541a49b81f60415f9`.
Receipt SHA256 values: OpenRouter
`ef185c2b25ccf440399df4e7d3ca0abaeddd9eb681ad9e45e7019100c9ee7700`;
GitHub `c5c366c6fc5cc641aa229c590061b0ad9c2bedc983190292a1b81f6bc7ca080e`.

A later [negative-identifier continuation attempt](native-mcp-openrouter-negative-first.jsonl)
used the separately approved anonymous-resource configuration. It reached a
verified email-first sign-in UI, dispatched exactly one synthetic non-account
identifier through that private UI, then failed in the harness before recording
the acknowledgement or a second form. The failed attempt remains failed; it is
not evidence of account authentication or successful continuation. Cleanup was
confirmed. A [local Chromium probe](negative-ack-local-probe.json), with no
external traffic or model calls, reproduced `response.body()` observation
failures in two of ten requests despite HTTP 200 and successful acknowledgement
parsing inside the iframe in all ten. The frozen live receipt lacks a safe
exception line, so that probe explains a plausible instrumentation failure,
not a proven live exception location. No submission was retried.

The separately frozen [v3.1 regression](native-mcp-openrouter-negative-regression.jsonl)
then succeeded on the unchanged deployed code/configuration. It recorded an
actual Playwright `Error` at the optional response-body read, kept the pending
native tool open, and required independent HTTP 200 plus broker `submitted` /
`after_input` evidence. Exactly one private synthetic submission led to an
observed site validation error and a fresh empty Email address / Continue form.
Visual review confirmed “Identifier is invalid.” above that empty field. First
input appeared after 34.623 seconds; the second form after 48.197 seconds, or
13.188 seconds after submission. Jev abstained once (0.476 seconds,
$0.000424578), then GPT performed six continuation calls in the same lease.
This validates negative-input continuity through GPT fallback, not Jev-only
continuation or account authentication. Native transport and receipt canary
checks passed; raw Jev payloads were not inspected. All cleanup was confirmed.
Frozen v3.1 harness SHA256 is
`55e4de00aa5570d1541504a28ab4660b276ebf630ec0e578a39f37d30ced3353`;
receipt SHA256 is
`c7481ef1496544c494bdb6b661be7b4fb12109dec541697308a2b54ffa6d87c2`.
The earlier failed v3 receipt remains unchanged.

## Limits and retained failures

One attempt per site per mode cannot establish a percentile, reliability rate
or general superiority. These are previously exposed public sites, not a new
held-out compatibility sample. Preserve failures without substituting retries.
The earlier [OpenRouter first trial](openrouter-first.jsonl) failed before
showing input; its [regression](openrouter-regression.jsonl) retained a failed
combined-field criterion despite rendering an email-first step. In the
predeclared random first sample, [Reddit](reddit-first.jsonl) was blocked,
[Dropbox](dropbox-first.jsonl) could not render its login dependencies, and
[GitHub](github-first.jsonl) rendered input. None of those outcomes is replaced
by this timing experiment. The separate negative-identifier continuation test
is also not a successful account login.
