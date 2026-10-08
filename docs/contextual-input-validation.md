# Перевірка contextual input, 2026-10-08

База: `1dce3748be615a2fd44e12e0eb4173be41d72281`, окремий worktree та гілка `feature/contextual-input`. Реальний runtime, Telegram API та Instagram account не запускалися.

- `TMPDIR=/private/tmp /tmp/oak-input-test-runtime/bin/python -m unittest discover -s tests -q`: **108 tests, OK, 1 skip** (optional Pillow).
- Після уточнення лічильника uncertain: `TMPDIR=/private/tmp PYTHONPATH=tests /tmp/oak-input-test-runtime/bin/python -m unittest test_requests test_web.WebTests.test_panel_redacts_content_and_scopes_task_controls_to_owner -q`: **15 tests, OK**. Серед них 14 focused request tests.
- `node --check` для `app.js`, `a2ui.js`, `requests.js`: **3 checks passed**.
- `git diff --check`: без помилок whitespace.
- Незалежний read-only review після виправлення replay, ранньої реєстрації turn та delivery-aware UI: нових суттєвих issues не знайдено.

Перший повний запуск мав дві помилки старих тестів через macOS `/tmp` / `/private/tmp` alias і один cold-filesystem timeout. Повтор із канонічним TMPDIR пройшов. Тестові залежності винесено в `/tmp`, бо читання нового venv у Documents затримувалося. Репозиторні auth-файли, cookie stores, секрети середовища та чутливі журнали не перевірялись.

Локальна browser verification використовувала `scripts/input-request-demo.py --port 18771` та actual pixels/AX у Codex in-app browser. Перевірено пряме відкриття ordinary form, submit/очищення полів/закриття, Instagram без credential fields і cancel, явний uncertain та доступ до нього з панелі, expired із вимкненим submit і доступним закриттям. CLI wrapper Playwright був недоступний (`playwright-cli` не знайдено), тому використано CUA browser API.

Результат synthetic fixture: 4 native attempts, 0 нових `turn/start`; ordinary submitted/sent, Instagram cancelled/sent, synthetic send failure submitted/uncertain, expired/sent. Це перевірка маршруту та UI, а не доказ виконання відповіді реальною моделлю чи exactly-once після аварії.

Локальні докази (ігноруються Git): `output/playwright/browser-results.json`, `input-submitted.jpg`, `instagram-blocked.jpg`, `input-uncertain.jpg`, `input-expired.jpg`. Тимчасовий demo server зупиняється після перевірки.

## Bootstrap і відтворення, 2026-10-08

Наступне доповнення підготовлено поверх локального `fef84c49964b7827fe1731f3ed463c6097cd148b`, у тому самому окремому feature worktree. Upstream PR19 не інтегрувався й не змінювався; його Bot API build/cache/migration hooks мають зберегтися під час інтеграції.

- Повна звичайна suite: **143 tests, OK, 1 skip** (optional Pillow), 9.886 s. Після останнього уточнення grace для залишкових descendants: **9 focused supervisor/bootstrap tests, OK**, 0.047 s.
- Окремі bootstrap/lifecycle/CLI guards: **15 tests, OK**, 3.993 s. Реальний SIGTERM під час startup Playwright: **1 test, OK**, 3.215 s; три fixture ports повторно доступні для listener.
- `scripts/verify-bootstrap-features.py --broker-browser`, з установленим тестовим Chromium: **57 mandatory protocol/bootstrap tests, OK**, ordinary submit/cancel, synthetic login/OTP/cancel, **4 native responses, 0 new turns**, cleanup confirmed. Перевірено також запуск через реальний `managed_check` VM supervisor, без запуску VM installer.
- `bash -n`, Python compile та `git diff --check` пройшли. Після незалежного read-only review worker exit/counter guards, source digest coverage, SIGTERM та group cleanup виправлено; нового material fail-open або secret issue не знайдено.

Chromium запуск і завершення використовують [Playwright start/stop](https://playwright.dev/python/docs/api/class-playwright#playwright-stop). Acquisition і cleanup захищені від cancellation; невизначене завершення блокує успіх. Verifier відкидає внутрішні diagnostics і повертає лише bounded counters/manifest; credential values, screenshots і request IDs до bootstrap-звіту не потрапляють.

Перші перевірки в sandbox не могли bind loopback; їх повторено з дозволеним локальним socket access. Dataless залежності в Documents замінено для тестування вже підготовленим тимчасовим runtime, без зміни production state. Нові cancellation та listener/TIME_WAIT failures виправлено перед успішними результатами вище.

Model calls, live Telegram/client, VM install/reboot і broker OS isolation не перевірені цими тестами. Instagram лишається disabled. Oracle read-only inspection показала Ubuntu 22.04.5 ARM64, поза поточною supported matrix; пакети, credentials і сервіси на ньому не змінювалися.
