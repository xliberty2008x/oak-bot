# Динамічні картки Oak у Mini App

Oak може складати декларативні форми й картки в наявному розділі **Стан**.
Розмова, звичайні відповіді й запити дозволів залишаються в Telegram. Навігація,
Living Control Center, скляні поверхні, поля та кнопки Mini App збережені.
Ця функція не надає нових дозволів на інструменти чи зовнішні дії.

## Зафіксований протокол і підтримуваний каталог

- A2UI **v0.9.1**, upstream commit
  `db4306536438df46e4f0443b9c4ec0d5f1a42dc4`.
- Власний renderer **oak-a2ui-dom@1.0.0**, без зовнішньої renderer dependency.
- Каталог **urn:oak:a2ui:canonical:v1**:
  [JSON schema](../oak/web/a2ui-catalog.json).
- Незмінні офіційні server/client/common schemas та Apache 2.0 license:
  [protocol fixtures](../tests/fixtures/a2ui/upstream/OAK-PROTOCOL.md).

Це обмежений A2UI-каталог Oak, а не реалізація всього Basic Catalogue.
Офіційний envelope та client action перевіряються проти upstream schemas;
`catalog.json` у server schema розв'язується до власного каталогу Oak.
Upstream schema IDs містять `v0_9`, але дозволяють version `v0.9.1`.
Oak приймає лише `v0.9.1`.

Підтримуються `createSurface`, `updateComponents`, `updateDataModel` та
`deleteSurface`; кожне повідомлення має `version` і рівно одну операцію.
`updateComponents` додає або оновлює визначення за ID. Кореневий ID — `root`.
`updateDataModel` замінює значення за JSON Pointer; відсутній path або `/`
означає весь object model, а відсутній value видаляє ключ. Шлях підтримує
escaping `~0` та `~1`. Повторне створення поверхні потребує `deleteSurface`.

| Компонент Oak | Підтримувані властивості |
| --- | --- |
| `Card` | Один `child`, наявна скляна картка |
| `Column` | Статичний масив `children` |
| `Text` | Літеральний текст або `{path}`, body/heading/hint |
| `TextField` | Літеральний label, bound string value, required |
| `ChoicePicker` | Літеральні options, bound `string[]`, один вибір, required |
| `Button` | Літеральний label, primary/quiet/danger, `action.event` |

Невідомі child refs можуть тимчасово показувати placeholder під час складання.
Каталог обмежений деревом: цикли, спільні children та root як child відхиляються.
Template children, arrays у data paths, функції, theme overrides, HTML, URL,
довільні CSS/JS та компоненти інших каталогів не підтримуються. Текст
відображається через `textContent`. Existing bound values перевіряються за
типом native widget до збереження batch.

Ліміти Oak: 64 KiB на batch/action/state, 32 messages у batch, 4 поверхні на
сесію, 64 компоненти на поверхню, глибина 16, 32 варіанти вибору, 2000 символів
у текстовому полі. Поверхня діє одну годину після останнього оновлення.
Це ліміти реалізації Oak, не обмеження офіційної специфікації.

## Потік агента й дій

Native tool `oak_a2ui` з `action: "catalog"` повертає каталог та приклад.
`action: "publish"` приймає `messages_json` — JSON array офіційних A2UI messages —
і обов'язковий зрозумілий Ukrainian `fallback` для Telegram. Помилковий batch
не змінює поточний стан. Дані зберігаються в owner/session-scoped SQLite tables,
а messages публікуються через наявний durable AG-UI `CUSTOM` event `a2ui`.
Модель, provider, account і permission flow залишаються наявними.

Mini App отримує authoritative snapshot через `GET /api/a2ui?conversation=…`.
Поки розділ Стан видимий, він перевіряє оновлення кожні три секунди та змінює
тільки відповідну поверхню. Це transport snapshot Oak, не окремий A2UI envelope.
Без зміни revision локальна чернетка поля зберігається. Hidden page/view зупиняє
оновлення; зміна сесії та завершення входу очищають картки й чернетки.

`POST /api/a2ui/action` використовує наявний Telegram-validated session cookie
або memory-only Bearer token, origin guard і перевірку ownership conversation.
Transport wrapper:

```json
{
  "requestId": "stable-request-id",
  "revision": 1,
  "inputs": {"/form/topic": "Мій тиждень", "/form/pace": ["gentle"]},
  "message": {
    "version": "v0.9.1",
    "action": {
      "name": "submit",
      "surfaceId": "oak-plan",
      "sourceComponentId": "submit",
      "timestamp": "2026-10-08T05:00:00Z",
      "context": {"topic": "Мій тиждень", "pace": ["gentle"]}
    }
  }
}
```

`requestId`, revision та inputs належать transport, а не A2UI message.
Сервер приймає лише видиму запропоновану Button action й bound editable paths,
перевіряє choices та заново обчислює context. Використану revision не можна
відправити вдруге. Receipt містить fingerprint: той самий requestId/payload
повертає попередній статус, змінений payload відхиляється. Невизначені запити
не повторюються автоматично, включно з crash/recovery до model dispatch.

Дія надходить у ту саму conversation через існуючий Controller, із повторною
перевіркою thread/surface під routing lock. Агент отримує значення як untrusted
user data й може надіслати incremental updates або deleteSurface. Це не обхід
наявного підтвердження publishing, purchases чи destructive actions.
`/new`, видалення теми, interrupted/failed/uncertain run, закінчення терміну
поверхні та зміна revision роблять старі дії недійсними. Cancel — звичайна
запропонована дія агента; вона може бути відправлена з порожніми required fields.

## Локальна демонстрація та перевірки

```bash
python scripts/a2ui-demo.py --port 18767
python -m unittest discover -s tests -v
python scripts/validate-a2ui-schema.py
node --check oak/web/app.js
node --check oak/web/a2ui.js
git diff --check
```

Demo слухає тільки `127.0.0.1`, створює тимчасовий стан, використовує справжні
Oak gateway/tools/controller та синтетичні agent/Telegram launch data.
Форма «План від Oak» дозволяє вибрати тему/темп, відправити відповідь, побачити
оновлену картку, змінити вибір або скасувати форму. Demo не запускає model
process, Telegram poller, delivery sink чи production service. Завершити Ctrl+C.

Offline schema validator потребує optional `jsonschema==4.25.1` у validation
environment; production requirements його не потребують. Він перевіряє
зафіксовані server/client fixtures з власним каталогом, без network fetching.
Основні regression checks використовують наявний стандартний unittest.

Реальна модель, Telegram-клієнт, актуальний VM revision та deployed Mini App
перевіряються окремо. До авторизованого rollout потрібно звірити VM source і
локальні зміни, зберегти існуючі config/account/state та зробити backup SQLite.
Нова schema additive й створюється Oak при старті; production migration чи
restart цією feature task не виконується. Tools catalogue version змінено:
наявний Controller переносить попередній контекст при native-tool rollover.
Після окремо дозволеного rollout перевірити реальний `oak_a2ui` tool call,
підписаний Telegram launch, submit/update/cancel у власній сесії та text fallback.
Меню бота й public URL для цієї функції змінювати не потрібно.
