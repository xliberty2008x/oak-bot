"""One inventory for supported chat commands, help and owner-scoped menus."""

# command, argument hint, description, group
COMMANDS = (
    ('start', '', 'Почати роботу з Oak', 'Розмова'),
    ('help', '', 'Показати команди', 'Розмова'),
    ('stop', '', 'Зупинити поточну задачу', 'Розмова'),
    ('new', '', 'Почати нову розмову', 'Розмова'),
    ('status', '', 'Стан вибраної розмови', 'Розмова'),
    ('model', '', 'Обрати модель для розмови', 'Розмова'),
    ('web', '', 'Відкрити панель Oak', 'Розмова'),
    ('remember', 'текст', 'Зберегти факт', 'Пам’ять'),
    ('memory', 'запит', 'Знайти збережені нотатки', 'Пам’ять'),
    ('forget', 'ID', 'Видалити збережену нотатку', 'Пам’ять'),
    ('remind', '10m текст', 'Створити нагадування', 'Розклад'),
    ('tasks', '', 'Переглянути розклад', 'Розклад'),
    ('cancel', 'ID', 'Скасувати задачу', 'Розклад'),
    ('image', 'назва', 'Створити обкладинку', 'Медіа'),
    ('preview', 'назва', 'Створити відеопрев’ю', 'Медіа'),
    ('speak', 'текст', 'Озвучити текст', 'Медіа'),
    ('research', 'запит', 'Дослідити питання', 'Інструменти'),
    ('browse', 'URL', 'Переглянути вебсторінку', 'Інструменти'),
    ('approve', 'ID', 'Дозволити очікувану дію', 'Підтвердження'),
    ('deny', 'ID', 'Відхилити очікувану дію', 'Підтвердження'),
    ('answer', 'ID текст', 'Відповісти на запит', 'Підтвердження'),
)


def command_names():
    return {'/' + row[0] for row in COMMANDS}


def menu_commands(web_available=True):
    return [{'command': name, 'description': description}
            for name, _, description, _ in COMMANDS if web_available or name != 'web']


def help_text(web_available=True):
    groups = {}
    for name, arguments, description, group in COMMANDS:
        if name == 'web' and not web_available:
            continue
        groups.setdefault(group, []).append('/' + name + (' ' + arguments if arguments else '') + ' — ' + description)
    return '\n\n'.join(group + '\n' + '\n'.join(rows) for group, rows in groups.items())


async def register_commands(gateway, owners, *, web_available=False):
    """Cosmetic registration never prevents an authorized owner's intake.

    Bot API setMyCommands accepts at most 100 commands and BotCommandScopeChat:
    https://core.telegram.org/bots/api#setmycommands
    """
    from .telegram import TelegramError
    failed = 0
    for owner in sorted(owners):
        try:
            await gateway._api('setMyCommands', {
                'scope': {'type': 'chat', 'chat_id': owner},
                'commands': menu_commands(web_available)}, timeout=6, rate_limit_attempts=1)
        except TelegramError:
            failed += 1
    if failed:
        print('Telegram command menu registration is temporarily unavailable for some owners.', flush=True)
    return failed
