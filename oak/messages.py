"""Bounded assistant-only read projection of the existing durable event log."""

import json

MAX_EVENTS = 80
MAX_MESSAGE_CHARS = 200_000
MAX_RESPONSE_BYTES = 1024 * 1024
KINDS = ('TEXT_MESSAGE_START', 'TEXT_MESSAGE_CONTENT', 'TEXT_MESSAGE_END',
         'RUN_FINISHED', 'RUN_ERROR')


def message_events(db, chat_id, *, after=None, before=None):
    """Project source events without copying tools, errors or custom/form values.

    Cursors are exclusive durable sequence numbers. Initial/older reads walk
    backwards; live reads walk forwards. All windows have explicit boundaries.
    A missing START is never interpreted as an assistant role.
    """
    for cursor in (after, before):
        if cursor is not None and (type(cursor) is not int or not 0 <= cursor <= 9007199254740991):
            raise ValueError('Invalid message cursor.')
    if after is not None and before is not None:
        raise ValueError('Choose one message cursor.')
    high = db.execute('SELECT COALESCE(MAX(sequence),0) FROM ui_events WHERE chat_id=?', (chat_id,)).fetchone()[0]
    query = ('SELECT sequence,event FROM ui_events WHERE chat_id=? AND sequence<=? '
             "AND json_extract(event,'$.type') IN (?,?,?,?,?)")
    args = [chat_id, high, *KINDS]
    if after is not None:
        query += ' AND sequence>?'
        args.append(after)
    if before is not None:
        query += ' AND sequence<?'
        args.append(before)
    query += ' ORDER BY sequence ' + ('ASC' if after is not None else 'DESC') + ' LIMIT ?'
    args.append(MAX_EVENTS + 1)
    rows = db.execute(query, args).fetchall()
    events, scanned, size, limited = [], [], 1024, False
    for row in rows[:MAX_EVENTS]:
        sequence, source = row[0], json.loads(row[1])
        kind = source['type']
        thread, run = source.get('threadId'), source.get('runId')
        if not all(isinstance(value, str) and 0 < len(value) <= 512 for value in (thread, run)):
            scanned.append(sequence)
            limited = True
            continue
        event = {'sequence': sequence, 'threadId': thread, 'runId': run}
        if kind.startswith('TEXT_MESSAGE_'):
            message = source.get('messageId')
            if not isinstance(message, str) or not 0 < len(message) <= 512:
                scanned.append(sequence)
                limited = True
                continue
            if kind == 'TEXT_MESSAGE_START':
                role = source.get('role')
            else:
                start = db.execute("""SELECT json_extract(event,'$.role') FROM ui_events
                    WHERE chat_id=? AND run_id=? AND sequence<=?
                    AND json_extract(event,'$.threadId')=? AND json_extract(event,'$.messageId')=?
                    AND json_extract(event,'$.type')='TEXT_MESSAGE_START'
                    ORDER BY sequence DESC LIMIT 1""", (chat_id, run, sequence, thread, message)).fetchone()
                role = start[0] if start else None
            if role != 'assistant':
                scanned.append(sequence)
                limited |= role is None
                continue
            event.update(messageId=message, type={'TEXT_MESSAGE_START': 'start',
                'TEXT_MESSAGE_CONTENT': 'delta', 'TEXT_MESSAGE_END': 'end'}[kind])
            field = 'delta' if kind == 'TEXT_MESSAGE_CONTENT' else 'text' if kind == 'TEXT_MESSAGE_END' else None
            if field and isinstance(source.get(field), str):
                event[field] = source[field][:MAX_MESSAGE_CHARS]
                event['truncated'] = len(source[field]) > MAX_MESSAGE_CHARS
                limited |= event['truncated']
        else:
            event.update(type='run_end', status='failed' if kind == 'RUN_ERROR' else
                         'interrupted' if source.get('status') == 'interrupted' else 'completed')
        cost = len(json.dumps(event, ensure_ascii=False).encode('utf-8'))
        if size + cost > MAX_RESPONSE_BYTES:
            break
        events.append(event)
        scanned.append(sequence)
        size += cost
    more = len(scanned) < len(rows)
    if after is None:
        events.reverse()
    cursor = (max(scanned) if more and scanned else after or 0) if after is not None else high
    if after is not None and not more:
        cursor = max(after, high)
    return {'events': events, 'cursor': cursor,
            'before': min(scanned) if more and scanned and after is None else None,
            'has_more': more if after is not None else False,
            'partial': limited or (more and after is None)}
