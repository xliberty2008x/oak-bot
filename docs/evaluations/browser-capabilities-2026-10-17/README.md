# Browser capability and task execution repair

The report in issue [#24](https://github.com/xliberty2008x/oak-bot/issues/24)
identified an image-to-video task that stopped after a paid provider refusal,
a broken discovery connector and unsuccessful attempts at a website file chooser.
The working ElevenLabs reference upload did not establish video generation access.
This change repairs reusable execution capabilities; it does not add a site-specific
Wan workflow or claim that a provider supplies free generation.

## What changed

- Managed visual and semantic browser tools now operate the same headed Chromium,
  using this deployment's existing desktop profile through a private Playwright
  pipe. No browser credentials are copied or debugging port exposed.
- Observations include frame/tab references and hidden file inputs. Native upload
  handles both inputs and dynamically opened choosers. Download captures a real
  browser event and produces a file receipt with size and SHA-256.
- Observations omit field values and URL query strings. Recoverable errors contain
  current controls and distinguish an unattempted action from an uncertain one.
  The tool never repeats an uncertain external action automatically.
- Owner-selected `browser_action_approval: "never"` removes repeated prompts for
  routine browser work. Manual desktop control pauses semantic access too. Local
  screenshots and downloads require explicit file delivery to reach Telegram.
- The model receives general guidance to inspect actual capabilities, recover
  from broken tools, use documented APIs when appropriate, verify output contents
  and deliver artifacts. A static MP4 preview is explicitly not image animation.
  A completed runtime turn no longer emits an automatic task-success outcome.

## Evidence boundaries

Protocol/unit checks, actual Chromium fixtures, native model execution, public
website interactions and deployed worker checks are distinct. None establishes
successful paid generation, an authenticated provider account, live Telegram
client interaction or the visual quality of a generated animation.

The public checks use only synthetic test data. The real model probe uses an
independently authored local fixture and an in-memory delivery sink; it does not
send a Telegram message. Browser context tests use a disposable profile with a
synthetic cookie, never a copied production profile.

## Validation

- Standard suite: **1102 tests**, 245 opt-in tests skipped. Later focused checks
  cover the final reactive-type, approval-metadata and attachment-migration fixes.
- Final browser capability suite: **22 tests passed**, including actual Chromium.
  Fixtures cover cross-origin iframe uploads, dynamic choosers,
  replaced/reset controls, delayed state, popup/tab selection, downloads and
  uncertain-action behavior. The managed desktop check also verifies visual and
  semantic control of the same page, cookie persistence, manual takeover and
  recovery after closing the last window without restarting X11.
- [Real model receipt](model-receipt.json): subscription `gpt-6.1-sol`, high effort,
  32.215 seconds. The model chose the hidden input in an iframe, uploaded, observed
  the delayed result, downloaded a 70-byte JSON file and invoked `oak_send_file`.
  An independent sink verified exact bytes and SHA-256. No approval prompt occurred.
  A subsequent attempt to open a local artifact as a web URL failed; the model
  recovered without repeating the upload or download. The receipt records this
  failure and separates model preview inspection from independent byte validation.
- [Public website checks](public-sites.json): the-internet's server confirmed a
  synthetic upload (8.11 seconds for the full check); DemoQA accepted a selection
  and downloaded an actual 4096-byte JPEG (4.82 seconds). The live Wan/Gradio page
  accepted a synthetic 32×32 PNG and rendered its server-file preview (10.82 seconds).
  No video generation was requested.

The first live Gradio check exposed a general reactive-control bug: the server
accepted the image, but the wrapper re-resolved an input that had been replaced
and reported failure. The fix distinguishes completed browser file selection
from server acceptance and observes the resulting page without reusing the old
control. Both a repeated live check and independent replacement/reset fixtures
passed. The same issue in post-fill verification was fixed and checked in real
Chromium. No site-specific branch was introduced.

The tool-version upgrade also preserves bounded references to owner images and
files previously queued for delivery in the same conversation. It does not read
those paths in the gateway or copy arbitrary tool results. Focused migration
checks verify references and selected model/effort survive the upgrade.

## Limits

The tool does not grant provider subscriptions, credits or GPU quota. A missing
connector still requires model recovery through an available interface. Uploads
accept up to eight ordinary workspace files totalling 128 MiB, without symlinks;
download artifacts have a 2 GiB copy limit. Site anti-bot controls and unsupported
widgets can still require a different method or the owner's input. These checks
do not establish universal website compatibility.

## References

The implementation uses Playwright's documented
[file inputs and chooser events](https://playwright.dev/python/docs/input#upload-files),
[download events](https://playwright.dev/python/docs/downloads) and
[persistent contexts](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context).
The investigated Gradio Space exposes its actual schema under `/gradio_api/info`;
that schema is capability evidence, not proof of available generation quota.
