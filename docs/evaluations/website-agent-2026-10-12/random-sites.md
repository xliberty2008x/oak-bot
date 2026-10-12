# Additional public-homepage evaluation — 2026-10-12

The unchanged native GPT → production MCP Jev task harness was run once per
selected site, without credentials, form submissions, manual navigation choices,
login-path hints or additional host grants. Each lease and browser was closed
before the next trial. These are form-preparation checks, not account login or
live Telegram verification. All failures below are retained.

## Reproducible selection and pinned conditions

`random.Random(20261012).sample(['gitlab', 'notion', 'stackoverflow', 'wikipedia'], 3)`
selected **Wikipedia → GitLab → Stack Overflow**. Entry URLs were
`https://www.wikipedia.org/`, `https://gitlab.com/` and `https://stackoverflow.com/`.

The dataset harness differs from the previously frozen MCP v2 harness only in
its target mapping, default target and protocol label. Its SHA256 is
`dd41eca8ffd778cf2d0e0b93ea09d94590ecb6f27143159c2489e3869ad2e78e`.
The production runtime digest was
`3119e344afe4495d687b3e98d67ca2087d9e31d00e50288d89b0ea84f1ee23c2`.
All three trials used config
`d0b5491842eda528b61cfbae7126c46b19467fec1a8b2106f740cdb47f1411fe`,
with anonymous resource discovery disabled and the existing Clerk/TikTok policies.
Per trial: 220-second overall deadline, at most twelve paid Jev calls, and at most
eighteen GPT continuation calls in the same workspace. No deadline was reached.

## First results: zero of three input handoffs

| Public homepage | Observed outcome | Jev calls / known cost | Total including cleanup |
| --- | --- | --- | --- |
| [Wikipedia](native-mcp-random-wikipedia-first.jsonl) | Jev abstained; GPT continued the same workspace. After navigation and a stale revision, a fresh observation was followed by `navigation_requires_owner`, then `invalid_plan`. No input fields appeared. | 1 / $0.000134862 | 43.997 s |
| [GitLab](native-mcp-random-gitlab-first.jsonl) | The browser lease ended during attachment, before MCP task creation. Delegation reported `lease_not_active`; no Jev call occurred. | 0 / $0 | 14.417 s |
| [Stack Overflow](native-mcp-random-stackoverflow-first.jsonl) | The task returned `unsupported` before inference. GPT observed the page, made one guarded navigation and received `provider_blocked`, confirmed by another observation. | 0 / $0 | 30.707 s |

No receipt identifies a more specific cause for GitLab attachment failure.
The Wikipedia errors do not demonstrate a static-resource failure. No claim is
made that broader CDN loading fixes cross-origin navigation or provider blocks.
Screenshots were inspected: Wikipedia showed only the working message, GitLab
showed a closed/unavailable channel, and Stack Overflow showed provider blocking.
All inputs were empty; screenshot hashes are retained in the receipts.

## Separately approved anonymous-resource Dropbox regression

After explicit owner approval, the broker configuration enabled anonymous
resource discovery. This is a separate policy experiment, not a retry replacing
the three first results. The runtime digest stayed unchanged; the new config hash
was `456adca7c35e6ae0f23be403b31ec620c82fb0bcae0ca2f4be2fb7dba5fd87bf`.
The Dropbox harness hash is
`89b52b203384aa5007044645d1b33ad533613b70c23606e6a8369ca9c61c635e`.

Starting from `https://www.dropbox.com/`, the
[first true-policy trial](native-mcp-anonymous-dropbox-first.jsonl) still failed:
Jev navigated once, then abstained; GPT observed and waited twenty seconds in the
same workspace. The page remained `unsupported_layout` with zero controls or
actions. Two Jev calls cost $0.00045969; the whole trial including cleanup took
50.200 seconds. The private renderer showed unsupported layout and Cancel only.
The broker's existing diagnostic endpoint exposes no resource counters, so this
receipt explicitly marks those counters unavailable rather than recording zeros.

## Unbilled Dropbox resource diagnosis

A separately approved [diagnostic](dropbox-anonymous-resource-diagnostic.jsonl)
used the same deployed `WebsiteAgentForms` and anonymous resource implementation
in a fresh browser under the broker UID. It made no model call and entered or
submitted no data. It selected one unambiguous **observed** Log in link; a stale
revision was rejected before dispatch, then one action on a fresh revision was
accepted. No second accepted click was made. Read-only samples followed at
0, 1, 3 and 5 seconds. Cleanup completed; total time was 10.881 seconds.

Temporary instrumentation in this isolated process preserved all production
routing and transport decisions. It recorded only public hosts, request types,
query-presence flags, HTTP statuses, bounded denial categories and numeric
counts; no URL paths, queries, headers, cookies or HTML were saved. The harness
hash is `f3ddbc24a88825b11cee096dca3481cbeab1a7caf62ee76d116f1c543cb5b95b`.

The evidence identifies a concrete remaining limit:

- The relay consumed exactly its **96-request lifetime cap**, across the homepage
  and sign-in navigation, while transferring 5,716,068 bytes. Three public hosts
  were pinned, 84 completed URLs recorded, and input freezing remained false.
- `cfl.dropboxstatic.com` successfully returned HTTP 200 for 22 scripts,
  62 stylesheets and 10 fonts. The host is therefore reachable under the new policy.
- Another 36 script and 36 stylesheet requests were aborted at the relay's
  pin/request-budget/freeze check. The host was already pinned and freezing was
  false; the exhausted request cap explains this rejection branch.
- One additional stylesheet was rejected for its response MIME type, and one
  script request to `accounts.google.com` returned HTTP 403. These are separate
  observed restrictions; this probe does not establish their individual impact.
- The sign-in document itself returned HTTP 200 and reached `readyState=complete`,
  but remained empty throughout the samples: zero body text, controls, actions,
  shadow roots and iframes. No cross-origin navigation was blocked.

The diagnosis supports correcting resource-budget behavior before another
acceptance trial. It does not establish that changing the request cap alone will
complete login, and no policy or limit was changed by the diagnostic.

All five receipts retain confirmed lease/browser cleanup (or, for the standalone
diagnostic, adapter cleanup), unchanged runtime/config hashes, and zero owner
input or submissions. Original receipts remain byte-identical to these copies.

## Source correction prepared after the diagnostic

The anonymous relay now has a 512-request lifetime budget; the legacy exact-host
relay retains 96. The shared 24 MiB total-byte cap, 4 MiB per-resource cap, six
concurrent requests, eight discovered hosts, timeout/redirect limits and all
post-input URL-freezing checks remain unchanged. This changes neither navigation
authority nor authenticated request permissions.

The existing broker diagnostics now include resource counters: `requests`,
`max_requests`, `bytes`, `hosts`, `completed_urls`, `frozen`, and the bounded
`failures.request_quota` count. The failure counter measures only request-quota
rejections; it is not a total of all blocked resource requests. No resource URLs,
host names, headers, response bodies or entered values are included.

Local regression checks exercise 512 successful anonymous downloads across two
document dependency sets, then rejection at the exact request limit; they also
retain the legacy 96-request limit and verify byte, host, freeze and privacy
boundaries. All 51 resource/discovery/hook checks and 20 agent checks (including
opt-in intercepted Chromium) passed. The change is a source correction at this report's preparation time;
no new live Dropbox success is claimed without a separate deployed regression.


## Deployed resource-budget regression: empty input handoff

After deploying commit `92b016a`, a separate real native GPT → production MCP
trial again started from the Dropbox homepage. The runtime digest was
`f166fa722baf36d27c955229acd4cd86499a6d5b957c37d7650b720fcc504841`;
the approved anonymous-resource configuration remained
`456adca7c35e6ae0f23be403b31ec620c82fb0bcae0ca2f4be2fb7dba5fd87bf`.
The frozen harness changed only its protocol label and added a final observer-only
numeric resource diagnostic projection; SHA256:
`332ee0e982539a01f5c4ff1833f0cca22c603add0ebf3c6d2b2f3b0101c68ea5`.

The [deployed regression receipt](native-mcp-dropbox-resource-regression.jsonl)
records **an empty Email field and Continue**, also confirmed by inspecting the
private renderer screenshot. Jev selected two navigation actions, then abstained.
The same lease returned to GPT, which made one `present` call. There was one
native delegated task and one MCP start; no manual navigation, entered values,
form submission, new host grant or restart occurred. This is a successful input
handoff through fallback, not a Jev-only success or account authentication.

Measured native-task-send → first usable UI was **30.501 seconds**, excluding
4.332 seconds of native startup. MCP task-start → first UI was 16.831 seconds;
GPT fallback handoff → first UI was 6.115 seconds. Total evaluation including
cleanup was 40.938 seconds. The three actual Jev requests took 1.451 seconds and
cost $0.000865998, with no unknown-cost calls. These are one live sample's
monotonic measurements, separate from the mocked/local regression checks above;
overlapping spans must not be added together.

The actual broker resource counters were:

| Counter | Observed value |
| --- | ---: |
| Requests / lifetime limit | 183 / 512 |
| Completed anonymous URLs | 151 |
| Downloaded bytes | 13,735,686 |
| Pinned hosts | 3 |
| Request-quota rejections | 0 |
| Private-input freeze | false |

The successful run used more requests than the earlier 96-request limit. The
receipt establishes that the new limit was not exhausted; it does not measure
all possible resource denials or imply universal website compatibility. Runtime
and configuration hashes stayed unchanged. MCP, lease, renderer context and
browser cleanup were confirmed. Telegram/iOS, production gateway dispatch and
actual authentication were not tested. All earlier failures remain unchanged.

Receipt SHA256:
`576fbcddada1a80cf67196767a3a19167902bff9a87c99779d35f59482dae86b`.
Empty private-form screenshot SHA256:
`d6e4395322d88885e2dcaa430675262b93e0d988f7bf9265c6265bec89cb110b`.
