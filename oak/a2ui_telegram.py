"""Telegram rich-message blocks for a stored Oak A2UI surface.

Telegram cannot host the interactive renderer, so cards degrade to native blocks:
metric rows and charts become tables, progress becomes percentages, and form
fields show their current values. Actions stay in the Mini App.
"""

from .a2ui import get_value


def _plain(value, model):
    if isinstance(value, dict):
        value = get_value(model, value['path'])
    return value if isinstance(value, str) else ''


def _number(value):
    value = round(value, 2)
    return str(int(value)) if float(value).is_integer() else str(value)


def _cell(text, header=False, align='left'):
    cell = {'text': text, 'align': align, 'valign': 'middle'}
    if header:
        cell['is_header'] = True
    return cell


def _table(cells, caption=None):
    block = {'type': 'table', 'cells': cells, 'is_bordered': True, 'is_striped': True, 'is_compact': True}
    if caption:
        block['caption'] = caption
    return block


def card_blocks(state, attach):
    """Return InputRichBlock objects; attach(artifact_id) yields an upload name or None."""
    components, model = state['components'], state['dataModel']
    blocks = []

    def visit(key, depth=0):
        item = components.get(key)
        if item is None or depth > 16:
            return
        kind = item['component']
        if kind == 'Card':
            visit(item['child'], depth + 1)
        elif kind in {'Column', 'Row'}:
            children = [components.get(child) for child in item['children']]
            if kind == 'Row' and children and all(child and child['component'] == 'Metric' for child in children):
                blocks.append(_table([[_cell(child['label'], True, 'center') for child in children],
                                      [_cell({'type': 'bold', 'text': _plain(child['value'], model) or '—'}, align='center')
                                       for child in children]]))
            else:
                for child in item['children']:
                    visit(child, depth + 1)
        elif kind == 'Text':
            text = _plain(item['text'], model)
            if not text.strip():
                return
            variant = item.get('variant', 'body')
            if variant == 'heading':
                blocks.append({'type': 'heading', 'text': text, 'size': 3})
            else:
                blocks.append({'type': 'paragraph', 'text': {'type': 'italic', 'text': text} if variant == 'hint' else text})
        elif kind == 'Badge':
            text = _plain(item['text'], model)
            if text.strip():
                blocks.append({'type': 'paragraph', 'text': {'type': 'bold', 'text': text}})
        elif kind == 'Metric':
            text = [{'type': 'bold', 'text': _plain(item['value'], model) or '—'}, ' · ' + item['label']]
            if item.get('hint'):
                text.append({'type': 'italic', 'text': ' (' + item['hint'] + ')'})
            blocks.append({'type': 'paragraph', 'text': text})
        elif kind == 'Progress':
            total = sum(segment['value'] for segment in item['segments'])
            parts = ' · '.join(f"{segment['label']} {round(100 * segment['value'] / total)}%" for segment in item['segments'])
            blocks.append({'type': 'paragraph', 'text': [{'type': 'bold', 'text': item['label'] + ': '}, parts]
                           if item.get('label') else parts})
        elif kind == 'Table':
            blocks.append(_table([[_cell(column, True) for column in item['columns']]]
                                 + [[_cell(value) for value in row] for row in item['rows']], item.get('caption')))
        elif kind == 'Chart':
            unit = (' ' + item['unit']) if item.get('unit') else ''
            cells = [[_cell('', True)] + [_cell(series['label'], True, 'right') for series in item['series']]]
            for index, label in enumerate(item['labels']):
                cells.append([_cell(label)] + [_cell(_number(series['values'][index]) + unit, align='right')
                                               for series in item['series']])
            blocks.append(_table(cells, item.get('title')))
        elif kind == 'Image':
            name = attach(item['artifact'])
            if name:
                blocks.append({'type': 'photo', 'photo': {'type': 'photo', 'media': 'attach://' + name}})
        elif kind == 'Divider':
            blocks.append({'type': 'divider'})
        elif kind == 'TextField':
            blocks.append({'type': 'paragraph', 'text': [{'type': 'bold', 'text': item['label'] + ': '},
                                                         get_value(model, item['value']['path']) or '—']})
        elif kind == 'ChoicePicker':
            chosen = get_value(model, item['value']['path']) or []
            names = {option['value']: option['label'] for option in item['options']}
            blocks.append({'type': 'paragraph', 'text': [{'type': 'bold', 'text': item['label'] + ': '},
                                                         ', '.join(names.get(value, value) for value in chosen) or '—']})

    visit('root')
    return blocks
