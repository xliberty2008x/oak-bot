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
