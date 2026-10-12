# Real Jev evaluation, 2026-10-12 client day

Actual UTC timestamps are retained in receipts. `typesafe/jev-1.13` resolved to
`typesafe/jev-1.13-20260917` through the real Decisions endpoint. All earlier
failures remain here. No owner credentials, existing profile or mock model was
used. Key provision, model selection, form dispatch and owner authentication
are separate results.

| Evaluation | Result | Scope |
| --- | --- | --- |
| First four structural fixtures | 3 selected as expected, chooser abstained | Synthetic descriptors, real model |
| First public check | Google composition unavailable | Real anonymous page, old structural prompt |
| Public ToScrape demo before semantic change | Actual POST and logout control confirmed | Public dummy login, no owner account |
| Original12 through structural-only live broker | 2/12 exposed input fields | Real broker; no owner input; actual API telemetry unavailable |
| Intermediate V1 eight cases | 7/8 expected; OTP abstained | Synthetic descriptors; 3 expected abstentions |
| Intermediate V2 same eight | 8/8 expected | Regression, not unseen sites |
| Bounded-text V3c named discovery | Google identifier; TikTok text/password; Instagram text/password after consent | Real model and pages; no owner login |
| V3c public ToScrape demo | Actual POST, logout control, disclosure gate closed | Real model and browser; public dummy account |
| V3c private-renderer loop | 4/4 real decisions; 2 post-input payload guards; validation/select/OTP completed | Intercepted synthetic site and broker shim; actual shared renderer |

The named probe's five real requests cost $0.000264012 in total. Instagram first
changed into a consent prompt during inference; that stale result was rejected,
the optional consent was dismissed, and a later read returned the login group.
The renderer loop's four requests cost $0.000187782; it made two fixture POSTs,
cleared the secret widgets and closed the browser/context/driver. Its public
context was absent on both model calls after human input. These receipts do not
prove mobile Telegram behavior or authentication with Google, Instagram or TikTok.

V3c is pinned by the renderer manifest to code digest
`f252874415919f6409ff34fccd795dd331707e2ffcd031d85d7fef618051405c`.
The prepared runner defaults to no execution; its explicit real-run flag needs a
private key-file reference, matching code digest and a new exclusive receipt.
Do not reuse its synthetic labels as unseen-site quality evidence.

Pre-input labels/headings are a bounded disclosure from a fresh owned browser,
not proof that page content is public. After any nonempty values mapping, all
free text is excluded permanently. DOM-source and disclosure signatures are
separate so redacting a newly filled value cannot itself stale the local action;
actual control/caption mutation still invalidates it. Identifier-only JS forms
with absent method/action/target use the existing native-submit CSP guard.

The first full-suite attempt exposed the select/redaction transaction regression
and a timing-dependent early CSP abort in a test. Both are corrected in the
candidate; the earlier failed run is not counted as passing validation.
Independent new-site and post-deployment results will be recorded separately.

Final candidate verification: **535 tests passed, 0 skipped, 170.564 seconds**,
using `OAK_PROVIDER_BROWSER_TESTS=1` and the installed Chromium runtime. This
includes intercepted browser fixtures, mocked protocol tests and local tools;
real model/live-page evidence is listed separately above.

## Deployed broker: independent sites and regression

Commit `cf7ba70` was deployed through the canonical execution/broker installers.
The current-boot execution attestation passed all ten checks; both process
identities passed the actual boundary check. The canonical gateway has one
supervisor/worker and no restart. Existing five uncertain intake records remain
unchanged; no durable queues, owner profiles or account state were copied/replayed.
The installed composer and exact TikTok resource policy were preserved.

The frozen complement holdout exposed fields on **4/12** sites: GitHub, Spotify,
eBay and Wise. These12 URLs were selected before probes, were never replaced,
and had no prior per-site runtime baseline. The original12 regression exposed
fields on **3/12**: Stack Overflow, Netflix and Airbnb (up from0 without composer,
and2 with the earlier structural-only composer). That is **7/24** exposed-input
sites overall, not24 successful logins. Categories form a convenience sample,
not a traffic-weighted population or a statistical universal-support estimate.

| Set | Sites | Attached | Form reads | Valid observations | Input fields exposed | Confirmed cleanup | Actions submitted |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| New complement holdout | 12 | 10 | 20 | 17 | 4 | 12 | 0 |
| Original regression | 12 | 11 | 22 | 18 | 3 | 12 | 0 |

All failures remain in the denominator. The identical optional zero-field method
rule found no eligible action, so no action or account input was submitted.
Actual API call count, model judgments and cost are **not exposed by this broker
API** and remain unknown. With two snapshot paths per form read and at most one
optional extra read, the theoretical configured ceiling was72 compositions per
12-site set; a stable revision's cache usually avoids repeated paid inference.

Remaining failures include access blocks, attach failure/redirects, empty review
pages and `composition_unavailable`. These outcomes alone do not establish their
causes. Independent read-only diagnostics and private owner-login acceptance are
separate follow-up evidence. No Google/Instagram/TikTok owner login is claimed.
The request for the owner's private-form step was posted to Coordination Hub
report5184; credentials and session material must remain outside Hub.

## Named deployment readiness and diagnosis

A separate bounded probe used actual deployed broker leases and the real
configured helper: all **9/9** scheduled form reads succeeded. Google exposed its
identifier on all3 reads; TikTok exposed identifier/password on all3; Instagram
exposed identifier/password, then optional consent, then the credentials again
after one accepted fixed `a0` action with empty values. All3 scopes independently
confirmed cleanup. The probe read no key or account input; authentication remains
untested. This is stronger than a direct adapter test but still only readiness.

Read-only diagnosis visited the17 sites without exposed fields, in fresh anonymous
contexts with no composer, owner input or model calls. The receipts retain status,
host/type resource counts and structural group descriptors, never body text,
input values or token-bearing URLs. A failed form read or attach timing alone is
not enough to establish causality.

- Main document failures: Etsy, Coinbase, Medium, GitLab, PayPal and WordPress403;
  Amazon404; Expedia429. Adding scripts cannot itself establish a fix for these.
- HTTP200 plus denied frontend dependencies: Slack (`a.slack-edge.com`,
  `b.slack-edge.com`), Dropbox (`cfl.dropboxstatic.com`), Booking.com
  (`cf.bstatic.com`, `www.booking.com`), Atlassian
  (`id-frontend.prod-east.frontend.public.atl-paas.net`), Twitch
  (`assets.twitch.tv`). This supports a loading-dependency hypothesis; allowing
  them was not tested or activated.
- Zoom has action-only groups and denied frontend scripts; the recorded
  composition failure is not a reproduced local payload-validation error.
- Notion redirects from the frozen old hostname to `app.notion.com`, followed by
  request failure. The initial-host DNS pin is a plausible cause, not proven by
  the receipt. Discord remains unresolved; the separate diagnostic GET-only
  guard stopped two background non-GET XHRs. Reddit has only one zero-field
  action group after an initial failed read.

The specific approved TikTok resource exception was not broadened. These
network/access/entry-point limits are separate from reusable form composition.
Diagnosis also exposed misleading initial blank-page completion; the subsequent
completion guard and its own validation are recorded separately from cf7 metrics.

## Blank-page completion follow-up

An initial empty generic page now reports `unsupported_layout` without a
completion action. An accepted zero-field chooser action cannot enable completion
either. Only an accepted value-bearing submission enables later explicit owner
confirmation; provider authentication evidence is unchanged. The three-line
production change passed **63 tests, 0 skipped, 58.238 seconds** across the five
affected suites with local intercepted Chromium fixtures. This check made no
external API/provider calls. Its source hashes and test scope are retained in
`blank-confirmation-fix-checks.json`. The cf7 live-site results above remain pinned
to cf7 and are not reassigned to this follow-up.

## Actual Google identifier dispatch: negative control

Against deployed `04dea145`, a fresh Google broker lease submitted exactly one
synthetic identifier under the reserved nonexistent `.invalid` domain. No account,
password or OTP was used. Initial form and post-submit form were both valid
HTTP200 responses; the only submit returned `accepted:true` in107ms. After two
seconds the provider state was `provider_blocked` with no fields/actions. There
were two form reads, no retry, no authentication/owner confirmation, and confirmed
cancel/status cleanup. The exclusive receipt's SHA-256 is
`b34007243d153ef4748ea91d846a78f458a51f577f3836c2e620b76a0a2f6cc4`.
Exit0 records controlled dispatch/error handling and cleanup, not successful
Google login. The metadata does not establish the exact blocking cause.

Google's [supported-browser guidance](https://support.google.com/accounts/answer/7675428?hl=en)
explicitly includes software-controlled and embedded browsers among environments
whose sign-ins it may stop. Its [OAuth policy](https://developers.google.com/identity/protocols/oauth2/policies)
requires secure external authorization browsing rather than developer-controlled
embedded agents. These explain a provider-level compatibility constraint; they
do not prove the cause of this particular negative-control result. Jev selection
cannot establish provider acceptance or bypass such a decision.
