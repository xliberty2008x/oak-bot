# Independent website-form baseline, 2026-10-11

The deployed generic broker exposed input fields on **0 of 12 independently
selected sites**. It attached 11 sessions, failed attachment on Notion, and
confirmed cleanup for all 12 requests. This does **not** satisfy the requirement
for reusable dynamic login forms.

This measured anonymous initial-form discovery through the real deployed
broker and private HTTPS API at source commit
`71f966e57230b68971a44347a78882e31295ba9b`. It did not attempt account
authentication. Jev was not configured or called, and no model responses were
mocked. The report's later Git commit does not change the evaluated runtime.

## Dataset and protocol

[selection.json](selection.json) freezes 24 recognizable candidates across
eight categories, then selects one per category and four additional sites
without replacement using Python `random.Random` and seed
`oak-independent-sites-2026-10-11-v1`. This is a diverse convenience sample,
not a traffic-weighted sample of the web. Google, Instagram and TikTok are
excluded. The selection was frozen before observing Oak results; official entry
verification followed, with no replacements for redirects or failures.

Each site used the same generic `website-v1` path and a fresh anonymous browser.
The serial broker pass requested two observations, two seconds apart, with a
35-second timeout per API operation. It cancelled every created session and
checked cleanup. The runtime, network policy and configuration were unchanged.
No owner browser profile, account values, registrations or credentials were used.

An optional public method step was enabled under a fixed rule: only a `form`
with no fields and exactly one total action whose entire casefolded label was
one of six email/password-method labels could be submitted once with empty
values. No site qualified, so **no action was submitted**. Generic Continue,
SSO, signup and completion-confirmation actions were not clicked. Consequently,
this is not an exhaustive exploration of every possible chooser branch.

[acceptance-plan.json](acceptance-plan.json) records the protocol and its timing:
the written plan was saved after the separate diagnostic pass had started and
its first findings were available, but before the deployed broker pass. It must
not be described as entirely preregistered before every observation.

## Results

| Site | Final real-broker result | Independent diagnostic evidence |
| --- | --- | --- |
| Stack Overflow | `unsupported_layout`, no fields | Email/password POST controls exist; the diagnostic collector finds three eligible groups. The configured path has no composer to select one. |
| Discord | `review`, no fields | First broker read returns recoverable `navigation_pending`; second is empty. Diagnostic main response is HTTP 200, with no measured cross-origin abort. The precise loading cause is unresolved. |
| Netflix | `unsupported_layout`, no fields | Text/password POST controls exist; two eligible groups are observed. The configured path has no composer to select one. |
| Etsy | `review`, no fields | Diagnostic main response is HTTP 403; a captcha-delivery script is rejected by the existing policy. Empty review is not login readiness. |
| Notion | Attachment fails, API HTTP 409 | Diagnostic main response is HTTP 301, followed by start failure. The official entry redirects to another host, but the diagnostic did not observe a cross-origin route abort; exact cause is unproved. |
| Coinbase | `form`, one action, no fields | Diagnostic document responses include HTTP 403 and a rejected challenge script. The action-only result is not a usable credential step. |
| Airbnb | `form`, six actions, no fields | A visible text input belongs to a default-GET form without method/action attributes or password/OTP. That shape is excluded; a different zero-field group is exposed. The separate diagnostic pass saw three actions, so action counts vary across visits. |
| Medium | `form`, one action, no fields | Diagnostic main response is HTTP 403. The action-only result is not a usable credential step. |
| Atlassian | `review`, no fields | Diagnostic HTTP 200 but no visible controls; external frontend script and stylesheets are rejected. Their causal effect was not isolated. |
| GitLab | `form`, one action, no fields | Diagnostic document responses include HTTP 403 and a rejected challenge script. The action-only result is not a usable credential step. |
| Reddit | `form`, one action, no fields | Diagnostic HTTP 301/200 and a zero-field Continue group. The precise interstitial/loading cause remains unresolved. |
| Twitch | `review`, no fields | Diagnostic HTTP 200 but no visible controls; 29 scripts and one stylesheet from the asset host are rejected. Their causal effect was not isolated. |

All 12 sites remain in the denominator. There were 24 planned baseline reads,
22 attempted reads, and 21 schema-valid responses. Notion accounts for two
unattempted reads; Discord accounts for one recoverable response. A valid schema
includes `review` and `unsupported_layout`, so **21 valid schemas is not 21
working forms**. Likewise, nine sites returned some action, including four
review-confirmation buttons; this is not a login-success metric.

The separate diagnostic pass also used the installed code and unchanged route
policy. It ran directly as the broker service account with that account's
default group, whereas the production broker uses its configured execution
group. It counted actual network decisions and safe DOM metadata. A separate
group-mode observation projected only bounded types/roles/intents to diagnose
ambiguity; it did not select a group, call Jev, or exercise the configured
composition path. Its findings supplement the real broker results.

## What the evidence establishes

There are several independent gaps: ambiguous form selection without a
configured composer, exclusion of identifier-only script-driven forms, rejected
frontend resources, and HTTP error/challenge pages that still become empty
`review` or action-only forms. Jev activation alone is not demonstrated to solve
all of these. The four HTTP 403 results describe this deployment and these
anonymous visits; they do not prove every browser or network would be rejected.

No fields reached the private API in the bounded protocol, so this run cannot
validate typing, submit transitions, OTP, successful authentication, or the
frontend renderer. Real Jev behavior and full account login are **not tested**,
not twelve failed authentications. Later-loading content and unvisited chooser
branches remain outside the observed paths.

Future changes should rerun this fixed regression set without discarding its
failures, and use an additional fresh selection to evaluate generalization.
Once these observations guide implementation, these 12 sites are no longer an
unseen holdout. Report page loading, selected form, real model decisions,
action transitions and verified authentication as separate stages. Preserve
the original receipts; do not replace this failed baseline with later results.

## Evidence and reproduction

- [Dataset and official references](selection.json)
- [Real broker receipts](broker-results.jsonl)
- [Sanitized independent diagnostics](diagnostic-results.jsonl)
- [Metrics and scope](metrics.json)
- [Source hashes and runner policy](runner-manifest.json)
- [Artifact provenance](provenance.json)
- [Executed broker runner](run-broker-baseline.py)

The broker runner is an opt-in snapshot for this **composer-disabled baseline**,
not a model evaluation or an automatic CI test. It checks the configured gateway
UID, installed module hashes, execution boundary and absent composer, and refuses
changed code/configuration. Under that deployment's configured gateway account,
run the runner through its Python environment with existing `aiohttp` installed:

```sh
python run-broker-baseline.py
python run-broker-baseline.py --run --public-method-step > new-broker-results.jsonl
```

The first command performs local validation only. The second contacts the live
broker and public sites; it must write a new receipt. Exit zero means the run
completed with cleanup, **not** that any login worked. The pinned runner will
refuse a newly configured composer or changed runtime; a future model-enabled
evaluation needs an explicitly versioned protocol instead of bypassing guards.

Receipts contain counts, enum states, public hostnames and code hashes. They
exclude field values, raw page text, cookies, tickets, leases, revisions and
token-bearing URLs. Deployment-specific paths, execution IDs and private config
fingerprints were removed from the committed diagnostic header; site observation
rows were retained unchanged. The original diagnostic script and raw receipt
remain in local task evidence. No application code or production configuration
was changed for this evaluation.
