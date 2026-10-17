"""Read-only conversation status shared with chat presentation."""

from datetime import datetime
import time


def summary(controller, scope):
    c, db = controller, controller.db
    thread = c.threads.get(scope)
    last = db.execute('SELECT status FROM turns WHERE chat_id=? AND thread_id=? ORDER BY rowid DESC LIMIT 1',
                      (scope, thread)).fetchone()
    waiting = db.execute("SELECT count(*) FROM interactions WHERE chat_id=? AND status='pending' AND expires>?",
                         (scope, time.time())).fetchone()[0]
    waiting += db.execute("SELECT count(*) FROM input_requests WHERE chat_id=? AND outcome='pending' AND expires>?",
                          (scope, time.time())).fetchone()[0]
    next_job = db.execute("SELECT due FROM jobs WHERE chat_id=? AND status='pending' ORDER BY due,id LIMIT 1", (scope,)).fetchone()
    return {'active': scope in c.active, 'preparing': scope in c._dispatching,
            'last_run_status': last[0] if last else None,
            'model': c.model_for(scope), 'effort': c.effort_for(scope), 'turbo': c.turbo_for(scope),
            'waiting': waiting, 'next_due': next_job[0] if next_job else None,
            'memory_count': db.execute('SELECT count(*) FROM memory_notes WHERE chat_id=?', (scope,)).fetchone()[0]}


def chat_text(controller, scope):
    state = summary(controller, scope)
    if state['active']:
        activity = 'Є активна задача.'
    elif state['preparing']:
        activity = 'Готую запит до виконання.'
    elif state['last_run_status'] == 'uncertain':
        activity = 'Результат попередньої задачі потребує перевірки.'
    else:
        activity = 'Готовий до нової задачі.'
    next_due = 'Немає'
    if state['next_due'] is not None:
        try:
            next_due = datetime.fromtimestamp(state['next_due'], controller.scheduler.timezone).strftime('%d.%m.%Y %H:%M %z')
        except (OverflowError, OSError, ValueError):
            next_due = 'Час поза підтримуваним діапазоном'
    return '\n'.join(['Oak · ' + activity,
                      'Модель: ' + state['model'], 'Міркування: ' + (state['effort'] or 'Автоматично'),
                      'Турбо: ' + ('Увімкнено' if state['turbo'] else 'Вимкнено'),
                      'Очікують відповіді: ' + str(state['waiting']),
                      'Наступний запуск: ' + next_due + ' · ' + str(controller.scheduler.timezone),
                      'Нотаток у цій розмові: ' + str(state['memory_count'])])
