"""Incremental product text catalogue; unsupported locales fall back to Ukrainian."""

import re


DEFAULT_LOCALE = 'uk'
_PLACEHOLDER = re.compile(r'\{([a-z][a-z0-9_]*)\}')
_UK = {
    'common.unavailable': 'Текст недоступний',
    'common.file': 'Файл',
    'common.cancel': 'Скасувати',
    'common.confirm': 'Підтвердити',
    'common.more': 'Показати ще',
    'common.loading': 'Завантажуємо…',
    'common.refresh': 'Оновити',
    'tasks.empty': 'Розклад порожній.',
    'tasks.mode.remind': 'Нагадування',
    'tasks.mode.run': 'Задача',
    'tasks.status.pending': 'Очікує',
    'tasks.status.running': 'Виконується',
    'tasks.status.paused': 'Призупинено',
    'tasks.status.uncertain': 'Потребує перевірки',
    'tasks.status.done': 'Завершено',
    'tasks.status.cancelled': 'Скасовано',
    'tasks.status.unknown': 'Невідомий стан',
    'tasks.date_unavailable': 'Час поза підтримуваним діапазоном',
    'tasks.count': 'Задач у розкладі: {count}',
    'recovery.title': 'Потрібна перевірка',
    'recovery.input': 'Запит до Oak',
    'recovery.job': 'Запланована задача',
    'recovery.turn': 'Виконання задачі',
    'recovery.native_request': 'Передавання відповіді на запит',
    'recovery.telegram_intake': 'Отримання повідомлення Telegram',
    'recovery.telegram_delivery': 'Доставлення повідомлення або файлу',
    'recovery.telegram_reply': 'Доставлення відповіді Oak',
    'recovery.review': 'Позначити перевіреним',
    'recovery.reviewed': 'Перевірено власником; результат залишається невідомим.',
    'recovery.retry': 'Повторити з підтвердженням',
    'recovery.empty': 'Немає записів, які потребують перевірки.',
    'artifacts.title': 'Файли',
    'artifacts.empty': 'Файлів у цій розмові ще немає.',
    'artifacts.unavailable': 'Файл більше недоступний.',
    'artifacts.download': 'Завантажити',
    'activity.title': 'Активність',
    'activity.empty': 'Виконань у цій розмові ще немає.',
    'activity.status.inProgress': 'Виконується',
    'activity.status.completed': 'Виконання завершено',
    'activity.status.failed': 'Помилка виконання',
    'activity.status.interrupted': 'Перервано',
    'activity.status.uncertain': 'Результат невідомий',
    'activity.status.unknown': 'Стан невідомий',
    'activity.tools': 'Викликів інструментів: {count}',
    'activity.generic': 'Виконую дію',
    'activity.search': 'Шукаю в інтернеті',
    'activity.browser': 'Працюю з браузером',
    'activity.computer': 'Керую комп’ютером',
    'activity.files': 'Працюю з файлами',
    'activity.command': 'Виконую команду',
    'activity.image': 'Створюю зображення',
    'activity.video': 'Створюю відео',
    'activity.audio': 'Створюю аудіо',
    'activity.transcribe': 'Розпізнаю аудіо',
    'memory.title': 'Пам’ять',
    'memory.empty': 'Нотаток ще немає.',
    'memory.saved': 'Збережено: {id}',
    'memory.deleted': 'Нотатку видалено.',
    'memory.not_found': 'Нотатку в цій розмові не знайдено.',
}
_CATALOGUES = {'uk': _UK}


def normalize_locale(locale=None):
    language = locale.replace('_', '-').split('-', 1)[0].lower() if isinstance(locale, str) else DEFAULT_LOCALE
    return language if language in _CATALOGUES else DEFAULT_LOCALE


def catalogue(locale=None):
    selected = normalize_locale(locale)
    return {'locale': selected, 'default_locale': DEFAULT_LOCALE, 'fallback_locale': DEFAULT_LOCALE,
            'strings': {**_UK, **_CATALOGUES[selected]}}


def _scalar(value):
    if value is None:
        return ''
    if type(value) is bool:
        return 'true' if value else 'false'
    if isinstance(value, str):
        return value
    if type(value) is int and abs(value) <= 9007199254740991:
        return str(value)
    raise ValueError('Text substitutions must be strings, safe integers, booleans or null.')


def format_text(template, values):
    return _PLACEHOLDER.sub(lambda match: _scalar(values[match[1]]) if match[1] in values else match[0], template)


def text(key, locale=None, **values):
    selected = _CATALOGUES[normalize_locale(locale)]
    template = selected.get(key, _UK.get(key, _UK['common.unavailable']))
    return format_text(template, values)
