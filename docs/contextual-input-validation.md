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
