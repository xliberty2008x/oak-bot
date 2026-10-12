# Narrow official A2UI Lit migration gate — 2026-10-12

This implements the renderer portion of the amended independent audit. It is
source and real local Chromium evidence. Production deployment, Telegram
WebView, iOS/WebKit, actual provider login and owner authentication remain
separate checks. This does not establish compatibility with every website.

Published npm registry metadata and the installed package manifests agree on
`@a2ui/lit@0.13.0` and `@a2ui/web_core@0.13.0`. Both use Apache-2.0. Exact archive
integrities and all bundled dependency versions/licences are recorded in
[packages.json](../../../oak/web/vendor/a2ui/packages.json) and the
[lockfile](../../../frontend/package-lock.json); the
[local build contract](../../../oak/web/vendor/a2ui/OAK-VENDOR.md) gives the
reproducible commands. The official version adapter accepts both 0.9 and 0.9.1;
the gate used Oak's existing canonical v0.9.1 fixtures, not a new wire protocol.

The old `oak/web/a2ui.js` component-tree recursion, node cache, JSON-pointer
read/write, data-model store and action-context resolution have been removed.
The ordinary adapter uses upstream processing, bindings, surface and Lit keyed
rendering. Existing Python security validation, durable receipts, session checks
and action transport remain authoritative. The panel still polls authoritative
snapshots; its adapter passes canonical envelopes to the official processor.
This is not a claim of new SSE streaming support.

| Concern | Implementation and observed gate |
| --- | --- |
| Catalog compatibility | Same six components, Oak IDs/limits and exact property schemas; upstream Card/Column/TextField, three narrow visual compatibility entries. No full Basic Catalog or extra functions. |
| Bindings/actions | Actual TextField and native single-select update upstream data model; emitted context matches changed values and source button. Required fields block submit; cancel works with empty required input. |
| Reconciliation | Same-revision polling preserves draft and focus; an incremental result replaces the reachable form, partial child arrival renders later, deletion/reset disposes surfaces. |
| Security | Unknown component, function call, style, metadata/accessibility extension, cycle, excess depth and excess component count fail closed in the browser. HTML/script/image strings remain literal text. Existing 16 Python A2UI tests cover server cycle/shared-child/binding/revision/receipt guards. |
| CSP/assets | Actual reviewed bundle loads from the fixed local route under unchanged `script-src 'self'` and `style-src 'self'`, with zero observed CSP violations and no CDN/provider requests. Real WebGateway test verifies exact bytes, content types, CSP and no arbitrary vendor route. |
| Existing design | Oak green/dark tokens, 24px card padding, 16px gaps, 50px fields, native labels/focus and button variants. A 390px touch/mobile Chromium viewport has no horizontal overflow; [isolated prototype screenshot](isolated-mobile-prototype.png) was visually inspected. This is not a pixel-parity or physical WebView claim. |
| Lifetime | Consumed action cannot send twice; busy and expired controls stay disabled; reset/delete clears nodes and observer/processor subscriptions. Server action de-duplication remains independent. |
| Private values | Private iframe imports no ordinary runtime. Independent private-widget tests cover the distinct ordinary/private channels; this gate used only non-secret synthetic planning data. |

Validation commands used the installed runtime, with no live accounts or models:

```sh
OAK_PROVIDER_BROWSER_TESTS=1 PLAYWRIGHT_BROWSERS_PATH=/opt/oak-runtime/browsers \
  /opt/oak-runtime/venv/bin/python -m unittest discover -s tests -p test_a2ui_renderer.py -v
/opt/oak-runtime/venv/bin/python -m unittest discover -s tests -p test_a2ui.py -v
OAK_TEST_NODE=/opt/node-v24.20.0-linux-arm64/bin/node \
  /opt/oak-runtime/venv/bin/python -m unittest discover -s tests -p test_login_ui.py -v
npm --prefix frontend run check
```

The final five-case renderer run passed: four actual-browser cases and the
WebGateway asset/manifest case. Existing A2UI tests passed 16/16; the private Node
suite passed 26/26 during adapter integration, before independent private-widget
changes. [Renderer output](renderer-browser-tests.txt) preserves the final
successful integrated run. A clean `npm --prefix frontend ci --ignore-scripts`
and `npm --prefix frontend run check` reproduced the checked-in bundle, package
provenance and licences. Final runtime SHA-256:
`52adb3896b77af169610cf5db3c485a88b9a8f7694557e7aa44d6ad434b8453b`.
Later deployment evidence must record its own source and runtime hashes.

Retained failures: [first prototype attempt](first-attempt.json) found that
loading a narrow custom catalog alone did not supply upstream binder metadata:
input setters/actions were missing. The narrow metadata adapter fixed this and
was tested again. The first integrated harness also used Playwright
`wait_for_function` string evaluation, which strict CSP correctly rejected.
Harness readiness now uses a DOM marker/locator, without weakening CSP. Neither
failure is treated as a provider or authentication result.

The [expanded browser gate](renderer-cycle-gate-failure.txt) initially found
that canonical child schemas also needed upstream component-reference metadata:
a cyclic card reached recursive rendering despite the independent server
guard. Annotating the unchanged `Card.child` and `Column.children` schemas lets
the official topology validator reject cycles and depth overflow. Both cases
are included in the final successful browser run. No catalog property or
accepted input shape was widened.

Primary implementation references:
[A2UI Lit README](https://github.com/a2ui-project/a2ui/tree/main/renderers/lit),
[theming](https://a2ui.org/guides/theming/),
[actions and full-model forwarding](https://a2ui.org/concepts/actions/).
