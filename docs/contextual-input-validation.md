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

## Bootstrap і відтворення до інтеграції, 2026-10-08

Наступне доповнення підготовлено поверх локального `fef84c49964b7827fe1731f3ed463c6097cd148b`, у тому самому окремому feature worktree. Upstream PR19 не інтегрувався й не змінювався; його Bot API build/cache/migration hooks мають зберегтися під час інтеграції.

- Повна звичайна suite: **143 tests, OK, 1 skip** (optional Pillow), 9.886 s. Після останнього уточнення grace для залишкових descendants: **9 focused supervisor/bootstrap tests, OK**, 0.047 s.
- Окремі bootstrap/lifecycle/CLI guards: **15 tests, OK**, 3.993 s. Реальний SIGTERM під час startup Playwright: **1 test, OK**, 3.215 s; три fixture ports повторно доступні для listener.
- `scripts/verify-bootstrap-features.py --broker-browser`, з установленим тестовим Chromium: **57 mandatory protocol/bootstrap tests, OK**, ordinary submit/cancel, synthetic login/OTP/cancel, **4 native responses, 0 new turns**, cleanup confirmed. Перевірено також запуск через реальний `managed_check` VM supervisor, без запуску VM installer.
- `bash -n`, Python compile та `git diff --check` пройшли. Після незалежного read-only review worker exit/counter guards, source digest coverage, SIGTERM та group cleanup виправлено; нового material fail-open або secret issue не знайдено.

Chromium запуск і завершення використовують [Playwright start/stop](https://playwright.dev/python/docs/api/class-playwright#playwright-stop). Acquisition і cleanup захищені від cancellation; невизначене завершення блокує успіх. Verifier відкидає внутрішні diagnostics і повертає лише bounded counters/manifest; credential values, screenshots і request IDs до bootstrap-звіту не потрапляють.

Перші перевірки в sandbox не могли bind loopback; їх повторено з дозволеним локальним socket access. Dataless залежності в Documents замінено для тестування вже підготовленим тимчасовим runtime, без зміни production state. Нові cancellation та listener/TIME_WAIT failures виправлено перед успішними результатами вище.

Model calls, live Telegram/client, VM install/reboot і broker OS isolation не перевірені цими тестами. Instagram лишається disabled. Oracle read-only inspection показала Ubuntu 22.04.5 ARM64, поза поточною supported matrix; пакети, credentials і сервіси на ньому не змінювалися.

## Інтеграція з актуальним main, 2026-10-08

Окрема локальна гілка `feature/contextual-upstream`, база
`56642238838c5ab13d00063c117e291ee9b01850`. Public `ls-remote` після тестів
підтвердив ту саму main-ревізію. Три початкові коміти перенесено через Git;
збережено PR19 Bot API build/private cache/migration hooks та PR20 endpoint-bound
cursor. Вихідний checkout і remote refs не змінювалися.

- Чиста upstream-база: **101 tests, OK, 1 optional Pillow skip**, 6.075 s.
- Фінальна повна suite з установленим тестовим Chromium: **156 tests, OK,
  1 optional Pillow skip**, 11.041 s. Включає SIGTERM/cleanup, socket alias
  exclusion і CLI diagnostic без provisioning.
- Фінальний verifier через реальний `managed_check`: **65 mandatory tests,
  0 failures/errors/skips**, Chromium ordinary submit/cancel із stale mixed
  launch, synthetic login/OTP/cancel; **4 original native responses, 0 new
  turns**, cleanup confirmed. Feature digest:
  `6e95470491bb3b675c8b811b998c72a2e5e19ad8feb4ba8602577faad7f609c7`.
- Bash/JavaScript syntax та whitespace checks пройшли. Незалежний read-only
  review підтвердив URL/dialog/native routing, PR19/20 hooks та останні
  wrapper/inbox-symlink guards без нових material blockers.

Під час фіналізації local exec transport тимчасово відключився. Попередній
verifier уже завершився exit 0; до повторного запуску залишкових verifier/fixture
процесів не було. Останні source правки перевірено після відновлення. Фінальний
verifier не записував screenshot, frame, request values або account artifacts;
наявні ignored browser artifacts належать попереднім синтетичним перевіркам.

`--check-platform` читає лише installer eligibility й повертає окремі OS/arch/
Python причини до config, apt, Docker або account actions. Unit matrix для
Ubuntu 22.04/aarch64 не є фактичним запуском на ORACLE-VM. Supported matrix
не розширено; реальний Instagram та credential isolation залишаються false.

Не виконувались push, PR mutation, merge до main, інсталяція/deployment,
SSH/access/security зміни чи provider account actions. Для production
залишаються actual ARM toolchain/desktop acceptance, immutable isolated
launcher і denial probes проти runtime та host adapters, reachable HTTPS
human origin, перевірений provider adapter, дозволений ephemeral session scope
та live model/Telegram/client/reboot checks — [детальні gates](credential-broker.md).
