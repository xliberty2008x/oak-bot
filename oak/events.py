"""Bounded AG-UI events; native reasoning and binary tool data stay private.

Wire fields follow https://docs.ag-ui.com/concepts/events. Oak includes threadId
and runId on every event, including custom events, for independent consumers.
"""

import json

MAX_EVENT_BYTES = 1024 * 1024
MAX_TEXT = 512 * 1024
TOOL_PREVIEW = 32000
TOOL_TYPES = {
    'commandExecution', 'fileChange', 'mcpToolCall', 'dynamicToolCall',
    'collabAgentToolCall', 'webSearch', 'imageView', 'imageGeneration', 'sleep',
}


def _string(value, field, limit=512, empty=False):
    if not isinstance(value, str) or (not empty and not value) or len(value) > limit:
        raise ValueError(f'Invalid event field: {field}')


def _patch(value):
    if not isinstance(value, list) or len(value) > 1000:
        raise ValueError('Invalid JSON patch')
    for operation in value:
        if not isinstance(operation, dict) or operation.get('op') not in {'add', 'remove', 'replace', 'move', 'copy', 'test'}:
            raise ValueError('Invalid JSON patch operation')
        _string(operation.get('path'), 'patch.path', 4096, empty=True)
        if operation['op'] in {'add', 'replace', 'test'} and 'value' not in operation:
            raise ValueError('Missing JSON patch value')
        if operation['op'] in {'move', 'copy'}:
            _string(operation.get('from'), 'patch.from', 4096, empty=True)


def validate_event(event):
    """Return a validated Oak event, or raise ValueError; never silently drop it.

    Validates Oak's emitted AG-UI subset and registered custom payloads, not every
    optional protocol extension or cross-event lifecycle ordering.
    """
    if not isinstance(event, dict):
        raise ValueError('Event must be an object')
    try:
        size = len(json.dumps(event, ensure_ascii=False, allow_nan=False).encode('utf-8'))
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise ValueError('Event must contain JSON values') from exc
    if size > MAX_EVENT_BYTES:
        raise ValueError('Event exceeds 1 MiB')
    for field in ('threadId', 'runId'):
        _string(event.get(field), field)
    for field in ('messageId', 'toolCallId', 'parentMessageId', 'parentRunId', 'subagentRunId'):
        if field in event:
            _string(event[field], field)
    if 'metadata' in event and not isinstance(event['metadata'], dict):
        raise ValueError('Event metadata must be an object')
    if 'timestamp' in event and (type(event['timestamp']) is not int or abs(event['timestamp']) > 9007199254740991):
        raise ValueError('Event timestamp must be a safe JSON integer')
    kind = event.get('type')
    _string(kind, 'type')
    fields = {
        'RUN_STARTED': (), 'RUN_FINISHED': (), 'RUN_ERROR': ('message',),
        'TEXT_MESSAGE_START': ('messageId', 'role'),
        'TEXT_MESSAGE_CONTENT': ('messageId', 'delta'), 'TEXT_MESSAGE_END': ('messageId',),
        'TOOL_CALL_START': ('toolCallId', 'toolCallName'),
        'TOOL_CALL_ARGS': ('toolCallId', 'delta'), 'TOOL_CALL_END': ('toolCallId',),
        'TOOL_CALL_RESULT': ('messageId', 'toolCallId', 'content'),
        'STEP_STARTED': ('stepName',), 'STEP_FINISHED': ('stepName',),
        'STATE_SNAPSHOT': (), 'STATE_DELTA': (),
        'ACTIVITY_SNAPSHOT': ('messageId', 'activityType'),
        'ACTIVITY_DELTA': ('messageId', 'activityType'), 'CUSTOM': ('name',),
    }
    if kind not in fields:
        raise ValueError(f'Unsupported event type: {kind}')
    for field in fields[kind]:
        _string(event.get(field), field, MAX_TEXT if field in {'message', 'delta', 'content'} else 512,
                empty=field == 'content')
    if 'role' in event and event['role'] not in {'developer', 'system', 'assistant', 'user', 'tool'}:
        raise ValueError('Invalid message role')
    if kind == 'RUN_FINISHED' and 'outcome' in event:
        # Cancellation metadata is not a human-input interrupt: approvals in
        # app-server resume the same active turn without ending the AG-UI run.
        if event['outcome'] != {'type': 'success'}:
            raise ValueError('Unsupported run outcome')
    if kind == 'STATE_SNAPSHOT' and 'snapshot' not in event:
        raise ValueError('Missing state snapshot')
    if kind in {'STATE_DELTA', 'ACTIVITY_DELTA'}:
        _patch(event.get('delta' if kind == 'STATE_DELTA' else 'patch'))
    if kind == 'ACTIVITY_SNAPSHOT':
        if not isinstance(event.get('content'), dict) or ('replace' in event and type(event['replace']) is not bool):
            raise ValueError('Invalid activity snapshot')
    if kind == 'CUSTOM':
        name, value = event['name'], event.get('value')
        if not isinstance(value, dict):
            raise ValueError('Custom event value must be an object')
        custom = {
            'artifact': {'id': 512, 'name': 512, 'mime': 128, 'url': 8192},
            'approval_request': {'id': 128, 'summary': 16000},
            'user_input_request': {'id': 128, 'question': 16000},
            'input_request': {'id': 64, 'kind': 16, 'summary': 512},
            'progress': {'message': 16000}, 'web_app_link': {'url': 8192, 'label': 2000},
            'activity': {'activityId': 512},
            'a2ui': {},
        }
        if name not in custom:
            raise ValueError(f'Unsupported custom event: {name}')
        if name == 'input_request' and value.get('kind') not in {'ordinary', 'sign_in'}:
            raise ValueError('Invalid input request kind.')
        if name == 'a2ui':
            from .a2ui import validate_messages
            validate_messages(value.get('messages'))
            if not isinstance(value.get('revisions'), dict) or any(type(v) is not int or v < 1 for v in value['revisions'].values()):
                raise ValueError('Invalid A2UI revisions.')
        for field, limit in custom[name].items():
            _string(value.get(field), f'value.{field}', limit)
        if name == 'web_app_link' and 'button_label' in value:
            _string(value['button_label'], 'value.button_label', 128)
        for field, limit in {'caption': 16000, 'toolCallId': 512, 'status': 128}.items():
            if field in value:
                _string(value[field], f'value.{field}', limit, empty=field == 'caption')
        if name == 'artifact' and 'path' in value:
            raise ValueError('Artifact events require registered URLs, not private paths')
        if name == 'activity' and type(value.get('active')) is not bool:
            raise ValueError('Activity active must be boolean')
    return event


def to_agui_event(event):
    """Move Oak routing/rendering extensions into metadata for strict AG-UI SSE."""
    validate_event(event)
    fields = {
        'RUN_STARTED': {'threadId', 'runId', 'parentRunId', 'input', 'protocolVersion'},
        'RUN_FINISHED': {'threadId', 'runId', 'outcome', 'result', 'usage'},
        'RUN_ERROR': {'message', 'code', 'usage'},
        'TEXT_MESSAGE_START': {'messageId', 'role', 'name', 'subagentRunId'},
        'TEXT_MESSAGE_CONTENT': {'messageId', 'delta', 'subagentRunId'},
        'TEXT_MESSAGE_END': {'messageId', 'subagentRunId'},
        'TOOL_CALL_START': {'toolCallId', 'toolCallName', 'parentMessageId', 'subagentRunId'},
        'TOOL_CALL_ARGS': {'toolCallId', 'delta', 'subagentRunId'},
        'TOOL_CALL_END': {'toolCallId', 'subagentRunId'},
        'TOOL_CALL_RESULT': {'messageId', 'toolCallId', 'content', 'role', 'subagentRunId'},
        'STEP_STARTED': {'stepName', 'subagentRunId'},
        'STEP_FINISHED': {'stepName', 'subagentRunId'},
        'STATE_SNAPSHOT': {'snapshot', 'subagentRunId'},
        'STATE_DELTA': {'delta', 'subagentRunId'},
        'ACTIVITY_SNAPSHOT': {'messageId', 'activityType', 'content', 'replace', 'subagentRunId'},
        'ACTIVITY_DELTA': {'messageId', 'activityType', 'patch', 'subagentRunId'},
        'CUSTOM': {'name', 'value', 'subagentRunId'},
    }[event['type']] | {'type', 'timestamp', 'metadata', 'rawEvent'}
    result = {key: value for key, value in event.items() if key in fields}
    metadata = dict(event.get('metadata', {}))
    metadata.update({key: value for key, value in event.items() if key not in fields})
    if metadata:
        result['metadata'] = metadata
    return result


def _preview(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    truncated = len(text) > TOOL_PREVIEW
    return text[:TOOL_PREVIEW] + ('\n[truncated]' if truncated else ''), truncated


def _tool_arguments(item):
    kind = item['type']
    if kind in {'mcpToolCall', 'dynamicToolCall'}:
        return item.get('arguments', {})
    fields = {
        'commandExecution': ('command', 'cwd'), 'fileChange': ('changes',),
        'webSearch': ('query', 'action'), 'imageView': ('path',),
        'imageGeneration': ('revisedPrompt',), 'sleep': ('durationMs',),
        'collabAgentToolCall': ('prompt', 'receiverThreadIds', 'model'),
    }
    return {key: item[key] for key in fields[kind] if key in item and item[key] is not None}


def _tool_result(item):
    """Expose text/status results; never forward image/audio base64 or raw items."""
    result = {key: item[key] for key in ('status', 'success', 'exitCode', 'durationMs', 'error', 'failure')
              if key in item and item[key] is not None}
    kind = item['type']
    if kind == 'commandExecution':
        result['output'] = item.get('aggregatedOutput') or ''
    elif kind == 'mcpToolCall':
        native = item.get('result') or {}
        result['text'] = [part['text'] for part in native.get('content', [])
                          if part.get('type') == 'text' and isinstance(part.get('text'), str)]
        result['contentTypes'] = [part.get('type') for part in native.get('content', [])]
    elif kind == 'dynamicToolCall':
        parts = item.get('contentItems') or []
        result['text'] = [part['text'] for part in parts if part.get('type') == 'inputText']
        result['contentTypes'] = [part.get('type') for part in parts]
    elif kind == 'webSearch':
        result.update(query=item.get('query'), results=item.get('results'))
    elif kind == 'imageGeneration':
        result['imageAvailable'] = bool(item.get('savedPath') or item.get('result'))
    elif kind == 'fileChange':
        result['paths'] = [change.get('path') for change in item.get('changes', [])]
    elif kind == 'collabAgentToolCall':
        result['agentsStates'] = item.get('agentsStates')
    return result


class EventMapper:
    def __init__(self):
        self._runs = {}

    def feed(self, notification):
        method = notification.get('method')
        params = notification.get('params', {})
        thread_id = params.get('threadId')
        turn = params.get('turn', {})
        run_id = params.get('turnId') or turn.get('id')
        allowed = {'turn/started', 'turn/completed', 'item/started', 'item/completed',
                   'item/agentMessage/delta', 'item/mcpToolCall/progress'}
        if not thread_id or not run_id or method not in allowed:
            # Native errors may retry; only terminal turn status ends a run.
            return []
        item = params.get('item', {})
        if method in {'item/started', 'item/completed'} and item.get('type') not in TOOL_TYPES | {'agentMessage'}:
            return []
        key, output = (thread_id, run_id), []

        def emit(event_type, **fields):
            output.append(validate_event({'type': event_type, 'threadId': thread_id, 'runId': run_id, **fields}))

        if key not in self._runs:
            self._runs[key] = {'messages': {}, 'tools': {}}
            emit('RUN_STARTED')
        messages, tools = self._runs[key]['messages'], self._runs[key]['tools']

        def message_start(item_id, phase=None):
            if item_id not in messages:
                messages[item_id] = {'text': '', 'ended': False, 'phase': phase}
                emit('TEXT_MESSAGE_START', messageId=item_id, role='assistant')
            return messages[item_id]

        def content(item_id, delta):
            for offset in range(0, len(delta), 64000):
                emit('TEXT_MESSAGE_CONTENT', messageId=item_id, delta=delta[offset:offset + 64000])

        def message_complete(item):
            item_id = item['id']
            state = message_start(item_id, item.get('phase'))
            if state['ended']:
                return
            text = item.get('text', '')
            if text.startswith(state['text']) and text != state['text']:
                content(item_id, text[len(state['text']):])
            state.update(text=text, ended=True, phase=item.get('phase'))
            emit('TEXT_MESSAGE_END', messageId=item_id, text=text, phase=state['phase'])

        def tool_start(item):
            item_id = item['id']
            if item_id not in tools:
                name = '.'.join(str(x) for x in (item.get('server') or item.get('namespace'), item.get('tool') or item['type']) if x)
                step = name + ':' + item_id
                tools[item_id] = {'ended': False, 'step': step, 'type': item['type']}
                emit('TOOL_CALL_START', toolCallId=item_id, toolCallName=name)
                serialized, truncated = _preview(_tool_arguments(item))
                if truncated:
                    # Preserve valid JSON when a command or patch needs a preview.
                    serialized = json.dumps({'preview': serialized, 'truncated': True}, ensure_ascii=False)
                emit('TOOL_CALL_ARGS', toolCallId=item_id, delta=serialized, metadata={'truncated': truncated})
                emit('TOOL_CALL_END', toolCallId=item_id)
                emit('STEP_STARTED', stepName=step, toolCallId=item_id)
            return tools[item_id]

        def tool_complete(item):
            state = tool_start(item)
            if state['ended']:
                return
            result, truncated = _preview(_tool_result(item))
            emit('TOOL_CALL_RESULT', toolCallId=item['id'], messageId=item['id'] + ':result',
                 role='tool', content=result, metadata={'truncated': truncated})
            emit('STEP_FINISHED', stepName=state['step'], toolCallId=item['id'])
            state['ended'] = True

        if method == 'item/started':
            if item['type'] == 'agentMessage':
                message_start(item['id'], item.get('phase'))
            else:
                tool_start(item)
        elif method == 'item/agentMessage/delta':
            item_id = params['itemId']
            state = message_start(item_id)
            delta = params.get('delta', '')
            if delta and not state['ended']:
                state['text'] += delta
                content(item_id, delta)
        elif method == 'item/completed':
            (message_complete if item['type'] == 'agentMessage' else tool_complete)(item)
        elif method == 'item/mcpToolCall/progress':
            message, truncated = _preview(params.get('message') or 'Tool running')
            emit('CUSTOM', name='progress', value={'message': message[:16000], 'toolCallId': params['itemId']},
                 toolCallId=params['itemId'], metadata={'truncated': truncated or len(message) > 16000})
        elif method == 'turn/completed':
            for item in turn.get('items', []):
                if item.get('type') == 'agentMessage':
                    message_complete(item)
                elif item.get('type') in TOOL_TYPES:
                    tool_complete(item)
            for item_id, state in messages.items():
                if not state['ended']:
                    emit('TEXT_MESSAGE_END', messageId=item_id, text=state['text'], phase=state['phase'])
            for item_id, state in tools.items():
                if not state['ended']:
                    emit('STEP_FINISHED', stepName=state['step'], toolCallId=item_id,
                         metadata={'status': turn.get('status', 'completed'), 'resultUnavailable': True})
            status = turn.get('status', 'completed')
            if status == 'failed':
                error = turn.get('error') or {}
                emit('RUN_ERROR', message=error.get('message', 'Agent turn failed.'), code='TURN_FAILED')
            else:
                fields = {'outcome': {'type': 'success'}} if status == 'completed' else {}
                emit('RUN_FINISHED', status=status, metadata={'status': status}, **fields)
            del self._runs[key]
        return output
