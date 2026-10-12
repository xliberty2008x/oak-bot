"""Portable messages for the local mocked-agent demonstration and protocol tests."""

from .a2ui import CATALOG, VERSION


def message(kind, body):
    return {'version': VERSION, kind: {'surfaceId': 'oak-plan', **body}}


def form_messages():
    return [
        message('createSurface', {'catalogId': CATALOG, 'sendDataModel': False}),
        message('updateComponents', {'components': [
            {'id': 'root', 'component': 'Card', 'child': 'form'},
            {'id': 'form', 'component': 'Column', 'children': ['title', 'hint', 'topic', 'pace', 'submit', 'cancel']},
            {'id': 'title', 'component': 'Text', 'variant': 'heading', 'text': 'План від Oak'},
            {'id': 'hint', 'component': 'Text', 'variant': 'hint', 'text': 'Обери напрям і темп. Oak оновить цю картку після відповіді.'},
            {'id': 'topic', 'component': 'TextField', 'label': 'Тема плану', 'value': {'path': '/form/topic'}, 'required': True},
            {'id': 'pace', 'component': 'ChoicePicker', 'label': 'Темп', 'value': {'path': '/form/pace'}, 'required': True,
             'options': [{'label': 'Спокійно', 'value': 'gentle'}, {'label': 'Зосереджено', 'value': 'focused'}]},
            {'id': 'submit', 'component': 'Button', 'label': 'Скласти план',
             'action': {'event': {'name': 'submit', 'context': {'topic': {'path': '/form/topic'}, 'pace': {'path': '/form/pace'}}}}},
            {'id': 'cancel', 'component': 'Button', 'label': 'Скасувати', 'variant': 'quiet',
             'action': {'event': {'name': 'cancel'}}},
        ]}),
        message('updateDataModel', {'value': {'form': {'topic': 'Мій тиждень', 'pace': ['gentle']}}}),
    ]


def card_messages(surface='oak-day'):
    """A display-only card: metrics, a macro split, a table and a chart."""
    def card(kind, body):
        return {'version': VERSION, kind: {'surfaceId': surface, **body}}
    return [
        card('createSurface', {'catalogId': CATALOG}),
        card('updateComponents', {'components': [
            {'id': 'root', 'component': 'Card', 'child': 'body'},
            {'id': 'body', 'component': 'Column', 'children': ['title', 'targets', 'split', 'meals', 'trend', 'note']},
            {'id': 'title', 'component': 'Text', 'variant': 'heading', 'text': 'Твоя денна ціль'},
            {'id': 'targets', 'component': 'Row', 'children': ['kcal', 'protein', 'fat', 'carbs']},
            {'id': 'kcal', 'component': 'Metric', 'label': 'Калорії', 'value': '1600'},
            {'id': 'protein', 'component': 'Metric', 'label': 'Білки', 'value': '80 г'},
            {'id': 'fat', 'component': 'Metric', 'label': 'Жири', 'value': '60 г'},
            {'id': 'carbs', 'component': 'Metric', 'label': 'Вуглеводи', 'value': '180 г'},
            {'id': 'split', 'component': 'Progress', 'label': 'Розподіл енергії',
             'segments': [{'label': 'Білки', 'value': 20}, {'label': 'Жири', 'value': 34}, {'label': 'Вуглеводи', 'value': 46}]},
            {'id': 'meals', 'component': 'Table', 'caption': 'Меню на один день', 'columns': ['Прийом', 'Страва', 'ккал'],
             'rows': [['Сніданок', 'Вівсянка з бананом', '400'], ['Обід', 'Рис із куркою та овочами', '513'],
                      ['Перекус', 'Йогурт, яблуко та горіхи', '290'], ['Вечеря', 'Риба з овочами', '377']]},
            {'id': 'trend', 'component': 'Chart', 'kind': 'bar', 'title': 'Калорії за прийомами', 'unit': 'ккал',
             'labels': ['Сніданок', 'Обід', 'Перекус', 'Вечеря'], 'series': [{'label': 'План', 'values': [400, 513, 290, 377]}]},
            {'id': 'note', 'component': 'Text', 'variant': 'hint', 'text': {'path': '/note'}},
        ]}),
        card('updateDataModel', {'value': {'note': 'Сума БЖВ дає 1580 ккал; різниця з ціллю несуттєва.'}}),
    ]


def result_messages(context):
    pace = 'спокійний' if context['pace'] == ['gentle'] else 'зосереджений'
    return [
        message('updateComponents', {'components': [
            {'id': 'form', 'component': 'Column', 'children': ['title', 'result', 'edit', 'cancel']},
            {'id': 'title', 'component': 'Text', 'variant': 'heading', 'text': 'План готовий'},
            {'id': 'result', 'component': 'Text', 'text': {'path': '/result'}},
            {'id': 'edit', 'component': 'Button', 'label': 'Змінити вибір', 'variant': 'quiet',
             'action': {'event': {'name': 'edit'}}},
        ]}),
        message('updateDataModel', {'path': '/result', 'value': f"{context['topic']} · {pace} темп. Почни з однієї важливої справи, потім заплануй час на відпочинок."}),
    ]
