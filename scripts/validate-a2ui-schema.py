#!/usr/bin/env python3
"""Offline verification against the unchanged, pinned upstream A2UI schemas.

Optional validation dependency: jsonschema==4.25.1; not a production dependency.
"""
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from oak.a2ui import validate_messages

upstream = root / 'tests' / 'fixtures' / 'a2ui' / 'upstream'
schemas = {name: json.loads((upstream / (name + '.json')).read_text())
           for name in ('server_to_client', 'client_to_server', 'common_types')}
catalog = json.loads((root / 'oak' / 'web' / 'a2ui-catalog.json').read_text())
registry = Registry()
for name, schema in schemas.items():
    uri = schema.get('$id', 'urn:oak:fixture:' + name)
    registry = registry.with_resource(uri, Resource.from_contents(schema, default_specification=DRAFT202012))
raw_common = 'https://raw.githubusercontent.com/a2ui-project/a2ui/db4306536438df46e4f0443b9c4ec0d5f1a42dc4/specification/v0_9_1/json/common_types.json'
registry = registry.with_resource(raw_common, Resource.from_contents(schemas['common_types']))
registry = registry.with_resource('https://a2ui.org/specification/v0_9/catalog.json', Resource.from_contents(catalog))
registry = registry.with_resource(catalog['$id'], Resource.from_contents(catalog))
fixtures = json.loads((root / 'tests' / 'fixtures' / 'a2ui' / 'profile.json').read_text())
for name in ('server_to_client', 'client_to_server'):
    schema = schemas[name]
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
    for value in fixtures['server' if name == 'server_to_client' else 'client']:
        validator.validate(value)
validate_messages(fixtures['server'])
for value in fixtures['invalid']:
    try:
        validate_messages([value])
    except ValueError:
        continue
    raise AssertionError('Invalid Oak profile fixture was accepted.')
print(f"Офіційні A2UI v0.9.1 schemas: {len(fixtures['server'])} server messages і {len(fixtures['client'])} client action пройшли; 3 unsupported fixtures відхилено.")
