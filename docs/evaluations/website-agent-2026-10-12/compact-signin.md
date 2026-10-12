# Compact dynamic sign-in

The previous website form repeated the task heading and destination inside two
containers and reserved at least 710 pixels for its private iframe. The compact
surface now has one trusted current-domain header, the fields and actions of the
current step, local password reveal, and close/cancel controls. The outer Mini
App dialog supplies the sole border and sizes itself from a numeric-only private
layout message. All alternatives and unavailable choices remain visible.

Explicitly opening an authenticated pending request launches its available
private channel once. A loading failure keeps cancellation available and times
out without retrying the request. Long forms scroll; their height reports cap at
4096 pixels. A 320×260 simulated viewport still exposes cancellation through the
iframe's own scroll area.

Live sampling also identified two general defects, independent of any domain:

- A model's EmailInput choice could impose email validation and a 254-character
  limit on a native text field accepting usernames. TextInput/EmailInput choices
  now preserve the observed native type and length in both compilers. An actual
  Chromium/private-broker/MCP case submits a 316-character username without `@`,
  then verifies native email validation on the next step.
- Some CDNs ignore `Accept-Encoding: identity`. The resource relay now decodes
  supported compressed scripts under both encoded and decoded size budgets;
  network origins, credentials, input freeze and browser permissions retain their
  existing scopes. The separate compression evaluation records exact coverage.

## Verification

The combined UI, scene/compiler, planner, agent, request, broker and browser
regression passed **216 tests in 93.679 seconds**, with Chromium and Node enabled.
After the bounded long-form layout correction and final cancellation race fix,
all **eight** shell Chromium tests passed in **17.758 seconds**. These runs
overlap; their counts are not additive.

The shell tests use the actual parent dialog/CSS/request module and private
HTML/CSS/JavaScript with intercepted HTTP. They verify one launch, exact-source
and origin layout checks, rejection of additional fields/invalid heights, reset
cleanup, private draft exclusion from parent cancellation, a failed handshake
with no retry, and all 32 fields/64 actions in a short viewport. Deferred launch
responses cannot recreate an iframe after cancellation starts or completes, or
after terminal polling; a late launch error cannot overwrite cancelled status.

The [dynamic step evaluation](dynamic-step-rebuild.md) covers chooser, email,
password, OTP, validation error, async hydration and stale shadow-DOM replacement
through real local broker HTTP/MCP and the shipped private renderer. Its typed
model responses are mocked. [The varied-site evaluation](compact-sites.md)
separately records actual public pages, real Jev decisions and public demo-account
submissions, retaining failures alongside successful rebuilds.

The first desktop/mobile screenshot pair used the actual embedded assets and a
synthetic password step: 560×409 desktop shell and 374×413 mobile shell, neither
with horizontal overflow. The capture script initially called a nonexistent
unittest cleanup helper after saving the screenshots; no browser process remained.
That harness cleanup was corrected. The screenshots establish appearance, not
physical Telegram keyboard behavior or account authentication.

The final confirmation pair after raising the privacy text to 12px measured
560×411 desktop and 374×416 mobile, with no horizontal overflow. The corrected
capture exited cleanly. The mechanical detector's other finding concerned an
unchanged decorative background elsewhere in the control panel.
[Desktop](compact-signin-desktop.png), [mobile](compact-signin-mobile.png).

The bootstrap feature suite passed 19 cases and initially skipped its optional
browser CLI smoke because that command lacked the installed browser path. That
case subsequently passed with Chromium enabled in **12.283 seconds**.

Owner-account authentication, CAPTCHA, passkeys and unsupported popup/iframe
paths remain separate acceptance limits. No site-specific login template was
introduced by this change.
