"""Bounded owner-facing projections; never expose stored event payloads or paths."""


def require_scope(controller, owner, scope):
    """Validate an already selected conversation, including private web sessions."""
    if type(owner) is not int or type(scope) is not int or controller.sessions.deleted(scope):
        raise LookupError('Розмову не знайдено.')
    if controller.sessions.owner(scope) == owner:
        return
    db = controller.db
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='web_conversations'").fetchone():
        if db.execute('SELECT 1 FROM web_conversations WHERE chat_id=? AND owner=?', (scope, owner)).fetchone():
            return
    raise LookupError('Розмову не знайдено.')


def page_bounds(before, limit):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('Розмір сторінки має бути від 1 до 50.')
    if before is not None and (type(before) is not int or not 0 < before < 2**63):
        raise ValueError('Некоректна сторінка.')
    return before, limit


def preview(text, limit=80):
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + '…'
