"""Fixed activity labels shared by Telegram and owner-only history."""

UNSUPPORTED_INPUT_MESSAGE = ('Oak не підтримує цей формат запиту MCP. Дію не виконано; '
                             'це не відмова користувача.')


def tool_label(name):
    if not isinstance(name, str):
        return 'Виконую дію'
    # Only known identifiers are interpreted; arbitrary names are never displayed.
    tail = name.rsplit('.', 1)[-1]
    labels = {
        'webSearch': 'Шукаю в інтернеті', 'web_search': 'Шукаю в інтернеті',
        'oak_research': 'Шукаю в інтернеті', 'search_query': 'Шукаю в інтернеті',
        'oak_browser': 'Працюю з браузером', 'browser': 'Працюю з браузером',
        'oak_computer': 'Керую комп’ютером', 'computer': 'Керую комп’ютером',
        'fileChange': 'Працюю з файлами', 'apply_patch': 'Працюю з файлами',
        'commandExecution': 'Виконую команду', 'exec_command': 'Виконую команду',
        'imageGeneration': 'Створюю зображення', 'oak_create_image': 'Створюю зображення',
        'oak_create_video': 'Створюю відео', 'oak_speak': 'Створюю аудіо',
        'oak_transcribe': 'Розпізнаю аудіо',
    }
    return labels.get(tail, 'Виконую дію')
