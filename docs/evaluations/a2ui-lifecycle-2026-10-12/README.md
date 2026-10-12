# Ordinary A2UI page lifecycle regression

Base: `1325ab5bd898d5e9aa758c1e3be267e0bee472d0`. This change is a PR candidate;
it has not been merged or deployed. It does not change private website forms,
credentials, network policy, renderer dependencies or catalogue permissions.

## Cause and correction

The old `pagehide` handler permanently cleared the polling interval, removed
the visibility listener and disposed the card. No `pageshow` handler restored
them. A browser restoring the same document from its back/forward cache therefore
returned to an empty card section with no polling. Resetting during a pending
action also invalidated its busy cleanup and discarded the local replay guard.

The panel now suspends reads and disables controls while retaining ordinary
drafts, renderer state and consumed actions. Visibility return and `pageshow`
restart one interval and fetch a fresh snapshot before enabling controls.
Read invalidation is separate from owner/topic reset and action completion.
Callbacks from a disposed renderer cannot target a new same-ID surface.

## Verification

Command (existing pinned Python dependencies and Chromium installed):

```sh
OAK_PROVIDER_BROWSER_TESTS=1 python -m unittest \
  tests.test_a2ui_renderer tests.test_a2ui tests.test_web -v
```

Final result: **58 tests passed, 0 skipped, 15.913 seconds** on Python 3.13,
Playwright 1.63.0 and Chromium 153.0.8010.12. This includes 17 renderer/asset
tests and 41 A2UI/web tests. No full application suite or production service
was run for this frontend-only change.

The real browser test serves the actual local renderer and styles over loopback
under the existing CSP, without network interception. API responses and owner
credentials are synthetic. It enables Chromium's back/forward cache (normally
disabled by Playwright), navigates away and back twice, and asserts:

- the same document identity survives, rather than accepting a reload;
- two trusted, persisted `pagehide` and `pageshow` event pairs occur;
- trusted hidden/visible transitions occur;
- the draft survives, one interval remains, and polling resumes in real time;
- a subsequent action is sent once, preserving the production API function's
  conversation query and synthetic owner authorization.

Additional intercepted-browser regressions exercise visibility-only suspension,
duplicate resume events, stale snapshots, a pending uncertain action, owner/topic
reset, a retained old callback, expired/consumed/deleted cards, and failed snapshot
revalidation. Existing focus, validation, image cleanup, catalogue and CSP checks
remain green. The local mobile-width screenshot after cached return and action
was inspected; physical mobile keyboard and Telegram WebView behavior were not.

Negative controls substitute source only in test memory:

- Both card-retention and real cached-document return regressions fail against
  the unmodified base renderer.
- Removing only the old-renderer callback generation guard causes the owner/topic
  regression to send a second action (`2 != 1`). The corrected source passes.

A fresh read-only agent review found no blocking implementation issues. Its
stale-callback coverage concern was addressed by retaining the component action
past the disabled DOM gate and checking the targeted negative control above.

## Separate deployed-asset verification

Before this patch, the deployed PR 22 follow-up `6bf3c18` was checked independently
against its receipt on `main`. Eight public assets returned complete HTTP 200
responses. The public renderer was 444,318 bytes in 0.351 seconds, SHA-256
`1edacc2604b5f5354be7cd0e6fc87d3d8498985312ae6f91aafadc6eb34c766e`, matching
the deployment receipt. Public Chromium rendering of 14 components, action/hint
updates, fresh reload and explicit expired-target messaging passed with synthetic
API/SDK responses. That run reproduced the lifecycle defect fixed here.

This asset evidence does not deploy or validate this new patch on production.
Live-model card generation and a real owner's Telegram launch/action remain
separate acceptance gates. After a later authorized deployment, the smallest
live check is a fresh ordinary card, open, click to change its hint, then return
to that same nonexpired card. No owner retest was requested by this PR work.
