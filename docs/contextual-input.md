# Контекстні запити в Oak

Поточна локальна гілка `feature/contextual-upstream` інтегрована з main `56642238` (PR19/20). Початкова реалізація була поверх `feature/a2ui-miniapp` (`1dce3748`); вихідний checkout, PR #15 та живі сервіси не змінювалися. Розмова залишається в Telegram. Та сама Mini App, авторизація власника, екран стану, діалоги та canonical каталог A2UI v0.9.1 показують форму за кнопкою з розмови.

## Реалізований перший крок

`oak_request_input` приймає лише серверний `template`: `task_details`, `plan_details` або `instagram_sign_in`. Перші два мають фіксовані поля для звичайних даних. Native `requestUserInput` і MCP form elicitation підтримують до трьох обов'язкових полів із переліками варіантів; довільні текстові native/MCP схеми та URL elicitation відхиляються до запису metadata. Новий шлях не приймає модельний HTML, URL чи компонент поля пароля. Додаткова перевірка чутливих назв не замінює ці обмеження.

Оригінальний native RPC чекає відповідь; задача зберігає той самий thread/turn. Окреме глобальне `pause_for_manual` не викликається. Подія AG-UI містить тільки непрозорий request ID, вид запиту й фіксоване резюме. Telegram отримує `web_app` кнопку з серверної HTTPS адреси Mini App та локатором `?request=<id>&conversation=<scope>`. Це локатор, а не дозвіл: GET/POST перевіряють власника та scope через існуючу авторизацію. Пряме посилання відкриває діалог після перевірки власника.

SQLite записує owner, scope, thread, turn, runtime epoch, оригінальний RPC ID, схему, абсолютний строк 300 секунд і стан. Submit/cancel атомарно споживають pending-запит та записують receipt. Однакова дія повертає receipt; змінений payload із тим самим action ID або друга дія відхиляються. Future передає відповідь початковому RPC, без нового `turn/start`. Routing lock захищає також ранній запит під час реєстрації turn. Зупинка чи завершення turn скасовує ще pending-запити без нового продовження.

Стани outcome: `pending`, `submitted`, `cancelled`, `expired`. Delivery: `waiting`, `ready`, `sent`, `uncertain`, `abandoned`. `sent` означає тільки успішне відправлення у stdio, а не підтвердження використання відповіді. Повтор native ID у тій самій epoch не обробляється вдруге, навіть після помилки send. На restart pending-запити стають expired; ready і sent без зафіксованого завершення turn стають uncertain. Автоматичного replay немає. Панель показує ready/uncertain окремо від запитів, які ще чекають відповіді. Строге exactly-once після аварії потребує durable acknowledgement/reconciliation з боку runtime; поточний transport такого контракту не має.

Форма призначена для звичайних даних: вони повертаються моделі та зберігаються як native response. Паролі, коди, токени й cookies для неї не підтримуються. Вільний текст не є надійним детектором секретів: користувач не повинен вставляти їх у звичайне поле. Ця зміна не оголошує старий довільний A2UI шлях каналом для секретів. Чернетка очищається після рішення, expiry, закриття, зміни сесії та невідомої відповіді; terminal діалог має кнопку закриття.

## Instagram: контракт, без входу

`instagram_sign_in` показує тільки серверний provider `instagram`, destination `https://www.instagram.com`, `capability=false`, `trusted_channel_unavailable`, `trusted_user_required`. Немає identifier/password/OTP полів, save checkbox, довільного URL, ручного «успішно увійшов» чи submit. Доступний cancel; native результат завжди містить `authenticated=false`. Напис `instagram.com` пояснює ціль, а не перевірений поточний origin. Замок або обіцянка захищеного з'єднання не показуються.

Наявний remote desktop має прямий human input, але спільний профіль браузера й той самий OS principal, доступний runtime. Це **не готовий ізольований канал облікових даних**. Автоматично відкривати його для входу небезпечно: cookies/profile можуть бути доступні через shell, а поточна глобальна пауза не має native task resume контракту. Збереження доступу не реалізовано й не схвалено.

Наступний browser broker має працювати під окремим OS principal/ізольованим контейнером, із тимчасовим owner-bound профілем, одним scope/turn, коротким lease й перевіркою фактичного origin браузера. Людський ввід йде прямо в broker, без LLM/chat/A2UI/AG-UI/analytics/logs. Під час lease runtime не отримує screenshot, DOM, CDP, shell/profile/cookie доступ. Broker очищає профіль на cancel, expiry та завершення задачі. Тільки підтверджений broker результат може розблокувати автентифіковане продовження. Телефонний браузер користувача сам по собі не створює сесію браузера Oak.

Для провайдерів, які підтримують потрібний сценарій, слід використовувати provider-hosted authorization code + PKCE, state/issuer binding і точні redirect URI: [RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html). Для native OAuth перевага external user-agent та заборона embedded user-agent описані в [RFC 8252](https://www.rfc-editor.org/rfc/rfc8252.html#section-8.12); це не доказ загальної підтримки чи заборони Instagram website login у Telegram WebView.

[Instagram API with Instagram Login](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login) та [Business Login](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/business-login) описують API-доступ professional accounts, а не вхід на сайт і не cookie bridge. Instagram тут обрано саме як website login. [Офіційна довідка про вхід](https://help.instagram.com/553970941289985/) і [умови Instagram](https://help.instagram.com/581066165581870/) потребують окремої перевірки застосовного автоматизованого сценарію. Частина повних сторінок Meta була недоступна (429); доступні офіційні індексовані фрагменти не підтверджують підтримку такого broker. UX-reference Buddy не встановлює його внутрішньої реалізації чи гарантій.

[Telegram Mini Apps](https://core.telegram.org/bots/webapps) описують HTTPS launch, перевірку `initData`, `startapp` і `openLink`; launch не переносить provider cookies у Oak. Поточна кнопка використовує існуючу configured public URL, без нової BotFather конфігурації. `start_param=request_<id>` підтримується лише як локатор; створення named Main Mini App не виконувалось.

## Межі та наступні рішення

Вхід на реальний сайт, реєстрації, OAuth grants, persistent access, provider app, push, PR mutation та deployment не виконуються цією зміною. Перед наступним етапом потрібні конкретні дозволи: створити ізольований broker на узгодженому сервері; провести контрольований website login до явно обраного тестового акаунта (секрети вводить тільки користувач); погодити мету й строк сесії та спосіб знищення профілю. Збереження cookies/refresh tokens або ongoing access потребує окремого явного дозволу. OAuth app registration має сенс лише після вибору придатного API-сценарію й також потребує дозволу.

До активації слід визначити: чи Instagram дозволяє саме потрібну дію, чи доступні підтримувані provider механізми, чи достатня одноразова тимчасова browser-сесія, які серверні звичайні шаблони додати та політику видалення звичайних responses/receipts. Транспорт після аварії має залишатися fail closed до підтвердженого reconciliation.

## Перевірка

Відтворення з перевіреної ревізії репозиторію: `./scripts/bootstrap.sh --features-only --verify-broker`.
Режим завершується до private config, account checks і запуску бота. VM bootstrap
завжди перевіряє протокол перед доступом до акаунта; `--verify-broker` додатково
перевіряє ordinary submit/cancel і synthetic login/OTP/cancel у Chromium. Звіт
розділяє локальні тести, модель, live Telegram, клієнт і credential isolation.
Protected `/api/bootstrap` містить версії, digest лише вихідних feature-файлів і
runtime epoch без bearer token; старий gateway не проходить readiness. Digest
не засвідчує ізоляцію. Повтор на running service лишається check-only.
Докладні команди й обмеження — у [bootstrap guide](bootstrap.md#reproduce-the-feature-checks-without-an-account).

`tests/test_requests.py` перевіряє native reply без нового turn, schema/choice validation, submit/cancel race, абсолютний expiry, owner/scope/turn/epoch binding, receipt rollback, повтор HTTP/native, uncertain send/restart, ранню реєстрацію turn, origin rejection, direct Telegram launch і виключення synthetic credential canaries із auth-контракту, SQLite та AG-UI. Це не доказ, що довільний секрет можна розпізнати в звичайному тексті.

`scripts/input-request-demo.py --port 18769` запускає тільки loopback fixture з тимчасовою SQLite, синтетичним runtime і синтетично підписаним Telegram launch. `POST /demo/request/{template}` створює сценарій; `?expired=1` та `?uncertain=1` — виключно fixture-перевірки. `GET /demo/result` повертає лічильники native attempts/new turns та стани без значень форми. Ніякі `/demo` маршрути не додаються до production gateway.
