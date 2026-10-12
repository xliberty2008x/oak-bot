# Private website-origin approval

The owner's OpenRouter Google action ended with the fixed message “Oak зупинив
перехід за межі схваленої адреси”. This identifies Oak's own navigation denial;
it does not establish a refusal by Google. The original destination previously
served both as task identity and as the only permitted document origin, and
Chromium DNS was pinned only to that host. A larger hostname list alone would
not safely preserve browser redirects and private credentials.

The generic agentic adapter now pauses a new HTTPS main-document GET/HEAD and
shows a fixed private origin-consent card. Only its fresh private revision can
approve the server-held transition. The native agent and Jev receive an opaque
waiting scene, never the approval origin or OAuth URL. After DNS and lease checks,
the same Chromium context continues the original request. No provider-specific
navigation template or automatic destination grant is added.

The browser uses a bounded authenticated CONNECT proxy with numeric public DNS
pins, end-to-end TLS and CDP Fetch request interception. This is necessary because
Playwright routing does not revisit every followed redirect. Native browser
cookies, HTTP method changes and TLS verification remain browser-owned. Redirected
POST bodies are denied, not reconstructed or replayed. A rejected stale private
dispatch loses its pending native POST admission. Initial redirect consent can
be displayed before initial navigation finishes; a consent appearing during a
read interrupts the load wait without repeating the click.

The private form always displays the current credential recipient when it differs
from the root task. Origin changes and consent cards clear drafts and invalidate
old plans. Owner completion is suppressed while on the identity-service origin.
After a validated return to the root, trusted broker promotion extends the same
transport once for the retained session and revokes identity-service tunnels.
Root-only content POSTs remain usable; cumulative transport quotas are preserved.
The complete contract and limits are in [DESIGN.md](../../../DESIGN.md).

The first anonymous Google observation still reported one omitted control.
A separate six-sample diagnostic over five seconds identified an active native
select with 83 enabled options; its purpose was not read. The previous option
limit was 32. The full-coverage harness therefore did not attempt presentation;
the compiler's existing partial-form notice was not a demonstrated compiler
failure. The reusable surface Select now supports at most 256 options end to
end, while legacy forms retain 32 and the scene's 64 KiB budget is unchanged.
Models still receive option counts rather than private captions or native values.
The original observation and diagnostic remain separate preserved outcomes.

## Verification

A focused regression run passed 281 tests in 102.798 seconds, with Chromium and
Node checks enabled. It covered the agent, scene browser, private broker/UI,
gateway, connected browser, task, navigation transport and origin-consent
adapter. Counts below describe parts of that run and are not additive.

The subsequent combined run after Select support exercised 455 cases in 191.831
seconds: 454 passed, and one privacy-test assertion incorrectly matched the new
public catalogue key `select_options` as a forbidden substring. The assertion
now checks that actual projected controls lack an `options` property. All seven
projection cases then passed, including a new 83/256/257 and legacy-33 boundary
case. Runtime code did not change for that test correction. This is not claimed
as a single clean 455-case run.

An earlier 109-case compatibility run had two failures in an obsolete mocked
surface response and startup fixture. The actual Chromium/MCP fixture now starts
the guarded transport and reads surface evidence in its current location; its
three cases passed independently and in the final combined run. No production
flag was disabled or timeout increased. First-run outcomes and log hashes are
preserved in [verification metadata](private-origin-verification.json).

Four actual local TLS/Chromium adapter cases passed in 7.882 seconds:

- A visible cross-origin link pauses before any target connection. Private
  revision approval resumes it once. Two native private POST steps then return
  to the root through a 302 in the same context. Root cookies survive and are
  absent at the identity service; routing parameters, synthetic credentials and
  entered values are absent from model observations and diagnostics. Trusted
  retention revokes the identity tunnel and permits a subsequent root content POST.
- Initial redirect consent becomes readable while the startup navigation is
  still pending. This exercises the configured adapter helper, not a complete
  cold production broker attachment.
- Cancellation closes a paused redirect without connecting to the target and
  without waiting for the tunnel's idle timeout.
- A real DOM change immediately before dispatch causes a definite stale rejection.
  A later unrelated page POST is denied; no POST reaches the local fixture.

The transport suite passed 36 distinct cases in 13.971 seconds: 20 bounded proxy
cases, nine interception lifecycle cases and seven actual local Chromium/TLS
cases. These include every 302/307 document hop, native cookie isolation, pending
consent, mixed/private DNS rejection, redirected POST denial, redirected resource
denial, proxy-credential protection against server authentication challenges,
bounded cleanup and one-use retained-session expiry/tunnel revocation.

The private UI's 32 cases and broker's 41 cases passed. Eleven additional
offline adapter cases use the real private HTTP endpoint and a trusted transport
seam. They cover private revisions, duplicate/stale consent, no model URL leakage,
recipient changes, root-only completion, DNS rejection, cancellation and expiry
during DNS before the paused request can resume.

After extending Select, 14 focused option cases passed, including actual
Chromium private-widget-to-native-control round trips at 83 and 256 options,
explicit overflow at 257, legacy limits, the existing byte cap and absence of
option captions/native values in model data. The private UI suite then passed
34 cases including 83-option rendering, exact per-action serialization and
256/257 boundaries. These later runs overlap earlier UI checks.

Local fixture TLS uses an explicitly isolated self-signed test context. Production
never disables certificate verification. These tests use no model, external
account, owner credentials or production session. The earlier V6 real native/Jev
acceptance receipt is preserved at its original code revision; it is not a fresh
acceptance of this navigation extension.

## Limits

At most four document origins, including the root, can be approved in a lease.
Approval waits at most 120 seconds and remains subject to all broker deadlines.
Redirected subresources, popup/iframe authentication, non-HTTPS destinations,
cross-origin native POST and redirected POST bodies remain unsupported. Anonymous
resource discovery retains its existing cumulative quotas and irreversible input
freeze. Additional browser verification, CAPTCHA and passkeys are not bypassed.
Successful owner authentication is a separate unverified gate until the owner
completes the private flow; reaching an empty Google form alone is not login proof.

## Anonymous live presentation

The first direct-adapter trial reached `accounts.google.com` in 11.700 seconds
after one observed Google action and one revision-bound exact-origin approval.
It supplied no input and observed the original omitted select. The second trial
preserved six observations of that 83-option control rather than treating it as
transient hydration. Both receipts remain unchanged.

After the generic Select extension, a third separately frozen anonymous trial
presented the complete observed document surface: username text input, the
83-option Select and all eight collected actions, with zero overflow. Four
actions were available; policy-denied alternatives remained visible. The Next
action bound only the text input. The same context and two document pins were
retained, no value was submitted, completion stayed unavailable, and cleanup
was confirmed. Its 17.611 seconds include five seconds of diagnostic sampling;
this is not a product latency claim. No model, native/MCP orchestration,
Telegram client or account authentication was exercised by this trial.

Receipts: [initial navigation](private-origin-live.jsonl),
[control diagnostic](private-origin-live-diagnostic.jsonl), and
[full private presentation](private-origin-live-presentation.jsonl).

## Deployment

Code commit `37810a5463cb7a6b6017ed5390b70517ae9f8fbc` was deployed on
2026-10-12 after verifying no active turns, website attempts or pending actions.
Existing uncertain intake records were preserved. The immutable execution and
broker installers produced the same reviewed profile; the broker configuration,
firewall rules, workspace and runtime roots were unchanged.

- Tools version: `oak-v13-website-origin-approval`.
- Product digest: `639cdfc4120fd4b75c39571b093d444a2ee8950fb7b83f8558b4103095f732f6`.
- Execution policy: `938afff9e7532ea859c6a961caea1e2b1719a8a6d33ce4d5f5ded10b594f94cc`.
- Profile SHA-256: `bdd3a9c6cb3722b173e9168dbf1f62dde1fe5d4352f3495e452762a0a46147eb`;
  144 manifest entries, including the new navigation module.
- Broker configuration SHA-256 remains
  `456adca7c35e6ae0f23be403b31ec620c82fb0bcae0ca2f4be2fb7dba5fd87bf`.
- Fresh current-boot boundary attestation passed all ten isolation checks.
- Broker health reported ready with delegated website tasks enabled; the
  gateway and worker were running with zero restarts after cutover.
- Public HTTPS `/login.js` returned 200 and matched the frozen private renderer
  SHA-256 `28107c2b7026768c93b18c95b77a57466dfa31586f8ae6e18f60afd410c981b9`.

These are deployment and asset checks, not a production owner login. The owner
was asked to start a fresh Google flow and report valid-account continuation
through the private form; that acceptance remains pending.
