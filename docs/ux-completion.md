# Oak UX completion record

All UX-00–UX-19 implementation units are merged and application release `53cc8ce7909f0e9a6e9db8f97fd8224bc7749b1d` is deployed (2026-10-18, Europe/Kyiv). This record separates source checks, synthetic browser checks and deployed-service checks; it does not claim owner-device or provider execution.

## Verification evidence

| Check | Source and result |
| --- | --- |
| Full regression | `eee62aa5b001cfe374a12a2e7415d29578d459fc`: 1,285 discovered, 1,039 executed successfully, 246 opt-in skips, no failures/errors. Python 3.12.15; existing unittest and Node fixtures. |
| Final theme correction | Release `53cc8ce7` differs from the full-regression source by one CSS override only. 11 panel UI tests passed; actual Chromium measured warning contrast 6.79:1 on white and at least 4.85:1 on all accepted light backgrounds. Dark appearance is preserved. |
| Protected feature verification | Release `53cc8ce7`: 89 protocol tests, no skips/failures/errors. Actual `scripts/verify-bootstrap-features.py` returned verified. |
| Intercepted Chromium regression | 51 tests across A2UI renderer (10), ordinary website forms (29), origin approval (4) and private sign-in shell (8). A2UI used `6de112a`; origin/sign-in used `89d4ab2`. Website form sources stayed identical while unrelated request-launcher UI advanced. No live provider accounts were used. |
| Owner-panel Chromium flows | Authenticated synthetic owner/topic: memory add/search/delete; schedule create/reschedule/pause/resume; approval decisions; ordinary form submission; native Back/cancel; delayed old-topic response isolation; polling with drafts/focus; Recovery review and exactly one fresh retry; authenticated file preview/download; bounded activity history. Functional source `eee62aa5`; JavaScript/Python identical in the release. |
| Final visual inspection | Ten desktop/portrait/landscape screenshots in light/dark, including 390×844 and 844×390 viewports; overflow, literal-text safety, keyboard focus, reduced motion and warning contrast checked. These are Chromium viewport checks. |
| Repository state | Small PRs linked below are merged; concurrent artifact review/experience and executor socket-drain fixes are retained. |

Final protected code digest:
`f1cee61f405270537224d4c252ffb82feeb983211a60381d90e1b99c151005d3`.

Portable reproduction commands, with the checkout's dependencies and Chromium installed:

```bash
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python scripts/verify-bootstrap-features.py
OAK_PROVIDER_BROWSER_TESTS=1 .venv/bin/python -m unittest discover -s tests -p 'test_a2ui_renderer.py' -v
OAK_PROVIDER_BROWSER_TESTS=1 .venv/bin/python -m unittest discover -s tests -p 'test_website_forms.py' -v
OAK_PROVIDER_BROWSER_TESTS=1 .venv/bin/python -m unittest discover -s tests -p 'test_website_origin_approval_browser.py' -v
OAK_PROVIDER_BROWSER_TESTS=1 .venv/bin/python -m unittest discover -s tests -p 'test_signin_shell_browser.py' -v
git diff --check
```

## Verification limits

Physical Telegram mobile keyboard behavior, a user-device Mini App launch, live
external provider sign-in, model task success and VM reboot were not established
by these fixtures or HTTP checks. The default remains subscription-backed
`gpt-6.1-sol`; no authentication or billing fallback was introduced.

The catalogue is intentionally incremental and currently provides Ukrainian
only, with an explicit Ukrainian fallback. Group-chat topics, direct Telegram
answer buttons/ForceReply and `sendMessageDraft` remain outside this roadmap.
A completed execution is still an unverified user outcome. Unknown external
operations keep their original evidence; no rollout or UI refresh replays them.

## Deployment result

The existing managed gateway was updated to `53cc8ce7` after all active work and
outgoing queues were empty. Its private profile was archived while stopped;
the previous source branch is retained. Existing uncertain records were
preserved without replay. Executor/broker releases, dependencies, account
directories and configuration were preserved.

Actual post-start checks passed: one managed gateway with its worker, running
phase and no supervisor restarts; unauthenticated protected APIs return 401;
owner-authenticated panel, locale, tasks, memory, Recovery, artifacts and activity
APIs return 200; the protected manifest matches the checkout. Read-only Telegram
checks confirmed the expected bot, empty webhook, all 21 owner-scoped commands
and the Mini App menu. The existing stable HTTPS endpoint returned 200 and
served JavaScript matching the deployed source.

Rollback preserves the latest durable state: drain work, stop this same managed
gateway, switch to the retained previous source branch and start with the same
configuration. Do not restore an older database after new actions or receipts
have been recorded. The private archive and exact operational paths remain
outside Git.

## Merged implementation units

| Item | Implemented behavior | Merged PRs |
| --- | --- | --- |
| UX-00 | Plan/specification, reconciliation and release identity | [#25](https://github.com/xliberty2008x/oak-bot/pull/25), [#75](https://github.com/xliberty2008x/oak-bot/pull/75) |
| UX-01 | Readable schedule | [#26](https://github.com/xliberty2008x/oak-bot/pull/26) |
| UX-02 | Command menu and grouped help | [#27](https://github.com/xliberty2008x/oak-bot/pull/27) |
| UX-03 | Durable approval decisions and card settlement | [#29](https://github.com/xliberty2008x/oak-bot/pull/29) |
| UX-04 | Approval deadlines, reminder and expiry | [#30](https://github.com/xliberty2008x/oak-bot/pull/30) |
| UX-05a | Private-safe activity and elapsed time | [#33](https://github.com/xliberty2008x/oak-bot/pull/33) |
| UX-05b | Run-bound stop | [#34](https://github.com/xliberty2008x/oak-bot/pull/34) |
| UX-06a | Durable received/completed reactions | [#39](https://github.com/xliberty2008x/oak-bot/pull/39) |
| UX-06b | Explicit edit policy | [#40](https://github.com/xliberty2008x/oak-bot/pull/40) |
| UX-07 | Human time grammar and DST | [#28](https://github.com/xliberty2008x/oak-bot/pull/28), [#72](https://github.com/xliberty2008x/oak-bot/pull/72) |
| UX-08a | Occurrence-bound snooze/done | [#55](https://github.com/xliberty2008x/oak-bot/pull/55), [#70](https://github.com/xliberty2008x/oak-bot/pull/70) |
| UX-08b | Owned cancellation and named failures | [#56](https://github.com/xliberty2008x/oak-bot/pull/56) |
| UX-09 | Read-only conversation status | [#38](https://github.com/xliberty2008x/oak-bot/pull/38) |
| UX-10 | Scoped polling and preserved drafts/focus | [#31](https://github.com/xliberty2008x/oak-bot/pull/31), [#65](https://github.com/xliberty2008x/oak-bot/pull/65), [#73](https://github.com/xliberty2008x/oak-bot/pull/73) |
| UX-11 | Mini App approval decisions | [#35](https://github.com/xliberty2008x/oak-bot/pull/35) |
| UX-12a | Scoped /forget | [#37](https://github.com/xliberty2008x/oak-bot/pull/37) |
| UX-12b | Memory list/search/add/delete | [#41](https://github.com/xliberty2008x/oak-bot/pull/41), [#74](https://github.com/xliberty2008x/oak-bot/pull/74) |
| UX-13a | Bounded owner task previews | [#42](https://github.com/xliberty2008x/oak-bot/pull/42), [#44](https://github.com/xliberty2008x/oak-bot/pull/44) |
| UX-13b | Create/reschedule/pause with revision guards | [#46](https://github.com/xliberty2008x/oak-bot/pull/46), [#47](https://github.com/xliberty2008x/oak-bot/pull/47), [#48](https://github.com/xliberty2008x/oak-bot/pull/48), [#50](https://github.com/xliberty2008x/oak-bot/pull/50), [#51](https://github.com/xliberty2008x/oak-bot/pull/51), [#52](https://github.com/xliberty2008x/oak-bot/pull/52), [#65](https://github.com/xliberty2008x/oak-bot/pull/65), [#67](https://github.com/xliberty2008x/oak-bot/pull/67), [#69](https://github.com/xliberty2008x/oak-bot/pull/69), [#71](https://github.com/xliberty2008x/oak-bot/pull/71) |
| UX-14a | Uncertainty inspection/acknowledgement | [#32](https://github.com/xliberty2008x/oak-bot/pull/32), [#58](https://github.com/xliberty2008x/oak-bot/pull/58), [#74](https://github.com/xliberty2008x/oak-bot/pull/74) |
| UX-14b | Explicit fresh retry | [#36](https://github.com/xliberty2008x/oak-bot/pull/36), [#59](https://github.com/xliberty2008x/oak-bot/pull/59) |
| UX-15 | Scoped Telegram model selection | [#68](https://github.com/xliberty2008x/oak-bot/pull/68) |
| UX-16a | Durable albums | [#53](https://github.com/xliberty2008x/oak-bot/pull/53) |
| UX-16b | Optional single transcript echo | [#54](https://github.com/xliberty2008x/oak-bot/pull/54) |
| UX-17a | Telegram theme | [#49](https://github.com/xliberty2008x/oak-bot/pull/49), [#76](https://github.com/xliberty2008x/oak-bot/pull/76) |
| UX-17b | Native navigation, insets and haptics | [#57](https://github.com/xliberty2008x/oak-bot/pull/57) |
| UX-18a | Scoped file discovery and bounded downloads | [#43](https://github.com/xliberty2008x/oak-bot/pull/43), [#60](https://github.com/xliberty2008x/oak-bot/pull/60), [#66](https://github.com/xliberty2008x/oak-bot/pull/66) |
| UX-18b | Activity history and Diagnostics | [#45](https://github.com/xliberty2008x/oak-bot/pull/45), [#61](https://github.com/xliberty2008x/oak-bot/pull/61) |
| UX-19 | Shared incremental Ukrainian catalogue | [#62](https://github.com/xliberty2008x/oak-bot/pull/62), [#63](https://github.com/xliberty2008x/oak-bot/pull/63) |
