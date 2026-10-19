"""Oak's media benchmark seam for the pinned Harness² implementation.

The upstream method owns proposal, probing, composition and its experience
library.  This module supplies tasks, real isolated Oak executions and a
separate, explicitly unlocked acceptance measurement.  It contains no search
or candidate-selection loop.  All source material is generated synthetic art.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import fcntl
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from typing import Mapping

from PIL import Image, ImageFilter

from . import media_pilot_complex_tasks as source
from .media_pilot_complex_quality import evaluate_artifact
from .media_pilot_runtime import MODEL, PilotRuntime


_HEX = re.compile(r'[a-f0-9]{64}')
_SAFE = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,159}')
_FAMILIES = {'projective', 'choreography'}
_SPLITS = {'dev', 'regression', 'holdout', 'followup'}
_DESCRIPTOR_KEYS = {'version', 'id', 'split', 'kind', 'family', 'size', 'title', 'seed'}
_EXPOSED = {'holdout-create-folio307-perspective', 'holdout-create-reeds309-depth',
            'holdout-repair-reeds310-depth'}
HARNESS_FILE = 'AGENTS.md'


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_sha(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('An ordinary immutable evidence file is required.')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def evaluator_fingerprint():
    """Bind actual independent checks and native isolation/reviewer code."""
    root = Path(__file__).resolve().parent
    names = ('harness2_media.py', 'media_pilot_complex_tasks.py', 'media_pilot_complex_quality.py',
             'media_pilot_hard_quality.py', 'media_pilot_perceptual_quality.py',
             'media_pilot_runtime.py', 'media_pilot_render.py', 'quality.py', 'quality_media.py')
    files = {'oak/' + name: file_sha(root / name) for name in names}
    return {'sha256': canonical_sha(files), 'files': files}


def _write(path, payload):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True,
                                    allow_nan=False) + '\n')
    temporary.replace(path)


def make_descriptor(namespace, index, *, split='dev', family='choreography', kind='creation'):
    """Deterministic new artwork identity; the namespace belongs to one cycle.

    Neither repeated executions nor relabeling a previously exposed descriptor
    establish freshness.  The outer registry must reserve commitments globally
    in its selected user/project scope before optimization starts.
    """
    if (not isinstance(namespace, str) or not _SAFE.fullmatch(namespace)
            or type(index) is not int or not 0 <= index <= 1000000
            or split not in _SPLITS or family not in _FAMILIES
            or kind not in {'creation', 'repair'}):
        raise ValueError('Invalid bounded synthetic media descriptor request.')
    identity = canonical_sha({'namespace': namespace, 'index': index, 'split': split,
                              'family': family, 'kind': kind, 'version': 1})
    seed = 10000 + int(identity[:12], 16) % 1000000000
    if family == 'projective':
        size = [896 + 8 * (int(identity[12:14], 16) % 12),
                704 + 8 * (int(identity[14:16], 16) % 8)]
    else:
        size = [800 + 8 * (int(identity[12:14], 16) % 9),
                512 + 8 * (int(identity[14:16], 16) % 8)]
    descriptor = {'version': 1, 'id': 'media-' + identity[:20], 'split': split,
                  'kind': kind, 'family': family, 'size': size,
                  'title': 'ATELIER ' + identity[16:24].upper(), 'seed': seed}
    validate_descriptor(descriptor)
    return descriptor


def exposed_regression(task_id):
    """Import one revealed historical case explicitly as regression material."""
    if task_id not in _EXPOSED:
        raise ValueError('Only the explicitly revealed historical cases are imported.')
    old = source._spec(task_id)
    return {'version': 1, 'id': old[0], 'split': 'regression', 'kind': old[2],
            'family': old[3], 'size': list(old[4]), 'title': old[5], 'seed': old[6]}


def validate_descriptor(descriptor):
    if not isinstance(descriptor, Mapping) or set(descriptor) != _DESCRIPTOR_KEYS:
        raise ValueError('The descriptor has unsupported or missing fields.')
    if (descriptor['version'] != 1 or not isinstance(descriptor['id'], str)
            or not _SAFE.fullmatch(descriptor['id']) or descriptor['split'] not in _SPLITS
            or descriptor['kind'] not in {'creation', 'repair'}
            or descriptor['family'] not in _FAMILIES
            or type(descriptor['seed']) is not int or not 0 <= descriptor['seed'] <= 2**40
            or not isinstance(descriptor['title'], str) or not 1 <= len(descriptor['title']) <= 48
            or any(ord(c) < 32 for c in descriptor['title'])
            or not isinstance(descriptor['size'], (list, tuple)) or len(descriptor['size']) != 2
            or any(type(n) is not int or n % 2 for n in descriptor['size'])
            or not 800 <= descriptor['size'][0] <= 1024
            or not 512 <= descriptor['size'][1] <= 800):
        raise ValueError('Invalid bounded synthetic media descriptor.')
    # An exposed identity cannot acquire independent status by changing split.
    if descriptor['id'] in _EXPOSED and descriptor != exposed_regression(descriptor['id']):
        raise ValueError('Historical holdouts must retain their regression identity.')
    return descriptor


def _spec(descriptor):
    validate_descriptor(descriptor)
    return (descriptor['id'], descriptor['split'], descriptor['kind'], descriptor['family'],
            tuple(descriptor['size']), descriptor['title'], descriptor['seed'])


def materialize_descriptor(descriptor, workspace):
    """Use existing authored source constructors without changing global catalogs."""
    spec = _spec(descriptor)
    workspace = Path(workspace).resolve()
    inputs = workspace / 'inputs'
    if inputs.is_symlink() or (inputs.exists() and any(inputs.iterdir())):
        raise ValueError('Use a fresh empty inputs directory.')
    inputs.mkdir(parents=True, exist_ok=True)
    contract = source._contract(spec)
    assets, masks, properties = source._build(spec)
    for name, image in assets.items():
        image.save(inputs / contract['assets'][name])
    (inputs / 'scene.json').write_text(json.dumps(contract, ensure_ascii=False, indent=2) + '\n')
    if descriptor['kind'] == 'repair':
        if descriptor['family'] == 'projective':
            broken = assets['scene'].copy()
            broken.paste(properties['clean_original'].filter(ImageFilter.GaussianBlur(3)),
                         (0, 0), masks['guide'])
            broken.save(inputs / 'broken-seed.png')
        else:
            frames = []
            for n in range(36):
                frame = assets['scene'].convert('RGBA')
                for actor in properties['actors']:
                    frame.alpha_composite(actor['rgba'], tuple(actor['positions'][n]))
                frames.append(frame.convert('RGB'))
            source._encode(frames, inputs / 'broken-seed.mp4')
    names = (['scene', 'donor', 'guide'] if descriptor['family'] == 'projective'
             else ['scene', 'atlas', 'depth_guide'])
    roles = {'scene': ('original damaged print' if descriptor['family'] == 'projective'
                       else 'original stationary scene'),
             'donor': 'complete higher-resolution original donor with four labeled landmarks',
             'guide': 'original damaged print with cyan repair authorization boundary',
             'atlas': 'original atlas containing both complete patterned actors',
             'depth_guide': 'public guide identifying both veils and required depth order'}
    references = [inputs / contract['assets'][name] for name in names]
    public = source._public(spec)
    return {'task_id': descriptor['id'],
            **{key: public[key] for key in ('split', 'kind', 'brief', 'output_name')},
            'specification_path': str(inputs / 'scene.json'),
            'reference_paths': list(map(str, references)),
            'reference_roles': {str(path): roles[name] for path, name in zip(references, names)},
            'source_hashes': {str(path): file_sha(path) for path in sorted(inputs.iterdir())}}


def quality_descriptor(descriptor, workspace):
    """Regenerate hidden semantic data in runner memory, never producer inputs."""
    spec = _spec(descriptor)
    workspace = Path(workspace).resolve()
    contract = source._contract(spec)
    if json.loads((workspace / 'inputs' / 'scene.json').read_text()) != contract:
        raise ValueError('The immutable source contract changed.')
    rebuilt, masks, properties = source._build(spec)
    for name, expected in rebuilt.items():
        with Image.open(workspace / 'inputs' / contract['assets'][name]) as actual:
            actual.load()
            if actual.mode != expected.mode or actual.size != expected.size or actual.tobytes() != expected.tobytes():
                raise ValueError('Regenerated semantic references differ from the committed source pixels.')
    return {**{key: contract[key] for key in ('task_id', 'family', 'size', 'duration',
                                            'fps', 'frame_count')},
            'contract': contract,
            'assets': {name: str(workspace / 'inputs' / filename)
                       for name, filename in contract['assets'].items()},
            'masks': masks, 'properties': properties}


def authored_descriptor_frames(descriptor, workspace):
    """Authored construction controls only; never put these in optimizer inputs."""
    facts = quality_descriptor(descriptor, workspace)
    for index in range(facts['frame_count']):
        yield source._authored_frame(facts, index)


def preflight_descriptors(descriptors, root):
    """Persist authored positives and semantic negatives before native search.

    These are construction checks, without model calls or a visual-quality
    claim. Videos are checked as actual encoded bytes with every indexed frame;
    lossless PNG construction frames and their hashes are retained separately.
    No thresholds are changed. Root must remain outside optimizer contexts.
    """
    root = Path(root).resolve()
    if root.is_relative_to(Path(__file__).resolve().parent.parent):
        raise ValueError('Keep authored controls outside the source checkout.')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    descriptors = [dict(validate_descriptor(row)) for row in descriptors]
    if not descriptors or len({row['id'] for row in descriptors}) != len(descriptors):
        raise ValueError('Construction controls require distinct descriptors.')
    binding = {'evaluator': evaluator_fingerprint(),
               'descriptors_sha256': canonical_sha(descriptors)}
    destination = root / 'preflight.json'
    if destination.exists():
        report = json.loads(destination.read_text())
        if any(report.get(key) != value for key, value in binding.items()):
            raise ValueError('Authored control identity changed; use a new preflight directory.')
    else:
        report = {'schema_version': 1, **binding, 'status': 'running', 'cases': [],
                  'scope': 'Local authored construction controls only. No native model or external provider calls; no aesthetic verdict.'}
    for descriptor in descriptors:
        directory = root / descriptor['id']
        directory.mkdir(parents=True, exist_ok=True)
        workspace = directory / 'workspace'
        if (workspace / 'inputs').exists():
            facts = quality_descriptor(descriptor, workspace)
            task = {'task_id': descriptor['id'], 'source_hashes': {
                str(path): file_sha(path) for path in sorted((workspace / 'inputs').iterdir())}}
        else:
            task = materialize_descriptor(descriptor, workspace)
            facts = quality_descriptor(descriptor, workspace)
        commitment = source_commitment(descriptor, task)
        existing = next((row for row in report['cases']
                         if row['descriptor_sha256'] == canonical_sha(descriptor)), None)
        if existing is not None:
            if existing['source_commitment'] != commitment:
                raise ValueError('Authored control sources changed.')
            for control in existing['controls'].values():
                for relative, sha in control['files'].items():
                    if file_sha(root / relative) != sha:
                        raise ValueError('An archived authored control changed.')
            continue
        positive = [source._authored_frame(facts, n) for n in range(facts['frame_count'])]
        if descriptor['family'] == 'projective':
            with Image.open(facts['assets']['scene']) as original:
                negative = original.convert('RGB')
            blurred = facts['properties']['clean_original'].filter(ImageFilter.GaussianBlur(3))
            negative.paste(blurred, (0, 0), facts['masks']['guide'])
            negatives = [negative]
            defect = 'Blur recoverable source lettering and engraved detail inside the authorized repair guide.'
        else:
            negatives = []
            for n in range(facts['frame_count']):
                with Image.open(facts['assets']['scene']) as original:
                    frame = original.convert('RGBA')
                for actor in facts['properties']['actors']:
                    frame.alpha_composite(actor['rgba'], tuple(actor['positions'][n]))
                negatives.append(frame.convert('RGB'))
            defect = 'Both intact moving actors incorrectly cover both stationary foreground veils.'
        controls = {}
        for label, frames in (('positive', positive), ('negative', negatives)):
            files, raw_frames = {}, []
            frame_dir = directory / label / 'frames'
            frame_dir.mkdir(parents=True, exist_ok=True)
            for n, frame in enumerate(frames):
                path = frame_dir / f'{n:03}.png'
                frame.save(path)
                files[path.relative_to(root).as_posix()] = file_sha(path)
                raw_frames.append(hashlib.sha256(frame.tobytes()).hexdigest())
            artifact = directory / (label + ('.mp4' if facts['duration'] else '.png'))
            if facts['duration']:
                source._encode(frames, artifact)
            else:
                frames[0].save(artifact)
            files[artifact.relative_to(root).as_posix()] = file_sha(artifact)
            measured = evaluate_artifact(descriptor['id'], artifact, workspace,
                                         artifact_root=directory, task_spec=facts)
            controls[label] = {'artifact_sha256': file_sha(artifact),
                               'lossless_rgb_frame_sha256': raw_frames,
                               'files': files, 'mechanical': measured}
        passed = (controls['positive']['mechanical']['status'] == 'pass'
                  and controls['negative']['mechanical']['status'] == 'fail'
                  and controls['negative']['mechanical']['structural_status'] == 'pass')
        report['cases'].append({'descriptor_sha256': canonical_sha(descriptor),
                                'source_commitment': commitment, 'controls': controls,
                                'negative_defect': defect, 'status': 'pass' if passed else 'fail'})
        _write(destination, report)
    report['status'] = 'pass' if all(row['status'] == 'pass' for row in report['cases']) else 'fail'
    _write(destination, report)
    return report


def source_commitment(descriptor, task):
    """Portable commitment suitable for sealing a hidden task before proposals."""
    validate_descriptor(descriptor)
    if task.get('task_id') != descriptor['id'] or any(
            file_sha(path) != sha for path, sha in task['source_hashes'].items()):
        raise ValueError('Task sources do not match their declared identity.')
    names = {Path(path).name: sha for path, sha in task['source_hashes'].items()}
    if len(names) != len(task['source_hashes']) or any(not _HEX.fullmatch(x) for x in names.values()):
        raise ValueError('Task source identities are invalid.')
    value = {'descriptor_sha256': canonical_sha(dict(descriptor)), 'inputs': names}
    return {**value, 'sha256': canonical_sha(value)}


def _sanitized_source(descriptor, task, destination):
    """Optimizer evidence contains observations and work, without measurement recipes."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    contract = json.loads(Path(task['specification_path']).read_text())
    for name, sha in task['source_hashes'].items():
        original = Path(name)
        if original.suffix.lower() not in {'.png', '.mp4'}:
            continue
        if file_sha(original) != sha:
            raise ValueError('The source changed before optimizer context construction.')
        target = destination / original.name
        data = original.read_bytes()
        if target.exists() and target.read_bytes() != data:
            raise ValueError('Existing optimizer source differs.')
        target.write_bytes(data)
    allowed = ('family', 'size', 'duration', 'fps', 'frame_count', 'assets',
               'actor_atlas_crops', 'depth_order', 'motion')
    public = {key: contract[key] for key in allowed if key in contract}
    public['intent'] = contract['review_manifest']['visual']
    _write(destination / 'work.json', public)
    return destination


def _receipt(path):
    value = json.loads((Path(path) / 'receipt.json').read_text())
    if value.get('state') != 'finished':
        raise ValueError('This execution has no finished receipt.')
    return value


def _verify_bound_artifact(record, prefix):
    receipt, run_dir = record['receipt'], Path(record['run_dir']).resolve()
    path, sha = receipt.get(prefix + '_path'), receipt.get(prefix + '_sha256')
    if path is None and sha is None:
        return None
    if not isinstance(path, str) or not isinstance(sha, str) or not _HEX.fullmatch(sha):
        raise ValueError('Incomplete immutable artifact binding.')
    path = Path(path)
    if not path.is_absolute() or not path.resolve().is_relative_to(run_dir) or file_sha(path) != sha:
        raise ValueError('The immutable native artifact is missing or changed.')
    return path


def evaluate_delivery(descriptor, run_dir, *, freeze_sha256):
    """Outer acceptance measurement; never called by execute or context methods.

    The coordinator supplies the committed freeze digest.  This does not turn
    numeric properties into a visual verdict: primary success is an exact-SHA
    conjunction of independently reviewed delivery and mechanical compliance.
    """
    if not isinstance(freeze_sha256, str) or not _HEX.fullmatch(freeze_sha256):
        raise ValueError('A committed candidate freeze is required before independent measurement.')
    record = _receipt(run_dir)
    if record['descriptor_sha256'] != canonical_sha(dict(validate_descriptor(descriptor))):
        raise ValueError('Task descriptor differs from its execution.')
    receipt = record['receipt']
    try:
        source_immutable = all(file_sha(path) == sha for path, sha in record['task']['source_hashes'].items())
    except (OSError, ValueError):
        source_immutable = False
    terminal_integrity = record.get('terminal_integrity', 'verified') != 'failed'
    artifact = _verify_bound_artifact(record, 'artifact') if terminal_integrity else None
    candidate = _verify_bound_artifact(record, 'candidate') if terminal_integrity else None
    selected = artifact or candidate
    if selected is None:
        measured = {'status': 'unverified', 'structural_status': 'unverified',
                    'quality_score': 0.0, 'checks': [], 'limitation': 'No immutable native candidate.'}
    else:
        measured = evaluate_artifact(descriptor['id'], selected, record['workspace'],
                                     artifact_root=run_dir,
                                     task_spec=quality_descriptor(descriptor, record['workspace']))
    sha = receipt.get('artifact_sha256')
    reviews = receipt.get('reviews', [])
    visual = bool(artifact and any(row.get('verdict') == 'pass' and row.get('sha256') == sha
                                  for row in reviews))
    material_defect = any(row.get('sha256') == sha
                          and any(issue.get('severity') in {'major', 'blocker'}
                                  for issue in row.get('issues', [])) for row in reviews)
    completed = receipt.get('status') == 'completed'
    delivered = bool(completed and receipt.get('delivered') is True and artifact is not None)
    model_valid = (record.get('model') == MODEL and receipt.get('model') == MODEL
                   and record.get('effort') == 'low' and receipt.get('effort') == 'low')
    calls, elapsed = receipt.get('tool_calls', 0), receipt.get('elapsed_seconds', 0)
    budget_valid = (type(calls) is int and 0 <= calls <= 64 and len(reviews) <= 4
                    and len(receipt.get('render_calls', [])) <= 16
                    and type(elapsed) in (int, float) and elapsed >= 0)
    mechanical = (measured.get('status') == 'pass' and source_immutable
                  and receipt.get('source_immutable') is True and model_valid and budget_valid)
    return {'freeze_sha256': freeze_sha256, 'run_id': record['run_id'],
            'task_id': descriptor['id'], 'split': descriptor['split'],
            'procedure_sha256': record['procedure_sha256'], 'artifact_sha256': sha,
            'candidate_sha256': receipt.get('candidate_sha256'), 'status': receipt.get('status'),
            'terminal_integrity': record.get('terminal_integrity', 'verified'),
            'model': receipt.get('model'), 'effort': receipt.get('effort'),
            'review_calls': len(reviews), 'material_defect': material_defect,
            'model_valid': model_valid, 'budget_valid': budget_valid,
            'configured_timeout_seconds': record['timeout'],
            'completed': completed, 'delivered': delivered, 'visual_passed': visual,
            'mechanical_passed': mechanical,
            'joint_passed': bool(delivered and visual and mechanical),
            'source_immutable': source_immutable and receipt.get('source_immutable') is True,
            'unverified': (not delivered or not visual or not source_immutable
                           or not model_valid or not budget_valid or not reviews
                           or any(row.get('verdict') == 'unverified' for row in reviews)
                           or measured.get('status') == 'unverified'),
            'mechanical': measured, 'elapsed_seconds': elapsed, 'tool_calls': calls,
            'cost_usd': None, 'cost_limitation': 'Subscription execution: tool calls and time are cost proxies.'}


class MediaSubstrate:
    """A real upstream SurfaceSpec; only the supported procedure surface is editable."""
    name = 'oak_native_media'

    def surface(self):
        from .harness2_upstream import upstream
        return upstream().tree.SurfaceSpec(
            name='codex', always_on=(HARNESS_FILE,), always_on_max=12000,
            always_on_cap_reason='Oak bounds injected reusable media procedures.',
            skill_dir='skills', skill_name_rule='must_match', trigger_hint='',
            autotrigger_file=HARNESS_FILE, trigger_line='', subagent_requires_mode=False,
            plugin_dir=None, hooks_file=None)


class MediaBenchmarkAdapter:
    """Actual upstream BenchmarkAdapter with DEV-only, durable native executions."""

    def __init__(self, root, descriptors, *, channels=None, runtime_home=None, effort='low',
                 timeout=600, baseline_procedure='', runtime_factory=None, budget_hook=None,
                 optimizer=True, freeze_sha256=None, native_lock_path=None):
        from .harness2_upstream import NativeRoleSpec, upstream
        api = upstream()
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        descriptors = [json.loads(json.dumps(validate_descriptor(row))) for row in descriptors]
        self.descriptors = {row['id']: dict(validate_descriptor(row)) for row in descriptors}
        if not self.descriptors or (optimizer and any(row['split'] != 'dev' for row in self.descriptors.values())):
            raise ValueError('Only development descriptors may enter the Harness² optimizer adapter.')
        if not optimizer and (not isinstance(freeze_sha256, str) or not _HEX.fullmatch(freeze_sha256)):
            raise ValueError('Independent execution requires a committed outer freeze.')
        if len(self.descriptors) != len(descriptors):
            raise ValueError('Development task identities must be unique.')
        if effort != 'low' or type(timeout) is not int or not 30 <= timeout <= 600:
            raise ValueError('The fixed native demonstration requires low effort and bounded time.')
        self.runtime_home, self.effort, self.timeout = runtime_home, effort, timeout
        self.runtime_factory, self.budget_hook, self.optimizer = runtime_factory, budget_hook, optimizer
        self.native_lock_path = Path(native_lock_path).resolve() if native_lock_path is not None else None
        self.implementation_sha256 = evaluator_fingerprint()['sha256']
        self.substrate = MediaSubstrate()
        role = NativeRoleSpec()
        self.spec = api.spec.BenchSpec(
            name='oak-media', runs_root=self.root / 'runs', install_mode='workspace_agents_md',
            prompts=api.spec.BenchPrompts(
                work='create and repair source-preserving artwork and layered animated scenes',
                domain_word='media practice',
                inv_levers='Improve reusable observation, rendering, inspection and delivery procedures.',
                dom_levers='Use supplied originals and coherent local layer boundaries.',
                yardstick='Faithful source identity, clear occlusion, intact detail and completed delivery.',
                selection_checks='Compare actual actions and visible delivered artwork against the supplied originals.'),
            channels=channels or {'proposer': role, 'selector': role},
            leak_globs=('receipt.json', 'evaluation.json', '*grader*', '*holdout*', '*mask*'),
            obs_trunc=100000)
        self._evaluation_freeze = freeze_sha256
        self._tasks, self._source_lock = {}, threading.RLock()
        self.validate_tree({HARNESS_FILE: baseline_procedure})
        base = self.base_harness()
        base.mkdir(parents=True, exist_ok=True)
        existing = base / HARNESS_FILE
        if existing.exists() and existing.read_text() != baseline_procedure:
            raise ValueError('The persisted base procedure differs from the requested version.')
        existing.write_text(baseline_procedure)
        config = {'descriptors': list(self.descriptors.values()), 'model': MODEL,
                  'effort': effort, 'timeout': timeout, 'optimizer': optimizer,
                  'freeze_sha256': freeze_sha256,
                  'native_lock_path': str(self.native_lock_path) if self.native_lock_path is not None else None,
                  'implementation_sha256': self.implementation_sha256,
                  'baseline_procedure_sha256': hashlib.sha256(baseline_procedure.encode()).hexdigest()}
        config_path = self.root / 'adapter.json'
        if config_path.exists() and json.loads(config_path.read_text()) != config:
            raise ValueError('Adapter identity changed; use a different cycle directory.')
        _write(config_path, config)

    def tasks(self, split='all'):
        return list(self.descriptors) if split in {'all', 'dev'} else []

    def domain_of(self, task_id):
        return self.descriptors[task_id]['family']

    def task_prompt(self, task_id):
        contract = source._contract(_spec(self.descriptors[task_id]))
        return contract['review_manifest']['visual']['brief']

    def _task(self, task_id):
        with self._source_lock:
            descriptor = self.descriptors[task_id]
            if task_id not in self._tasks:
                workspace = self.root / 'sources' / task_id
                manifest = workspace / 'materialized.json'
                if manifest.exists():
                    task = json.loads(manifest.read_text())
                    if any(file_sha(path) != sha for path, sha in task['source_hashes'].items()):
                        raise ValueError('Persisted task sources changed.')
                else:
                    task = materialize_descriptor(descriptor, workspace)
                    _write(manifest, task)
                self._tasks[task_id] = task
            return self._tasks[task_id]

    def task_files(self, task_id):
        with self._source_lock:
            return _sanitized_source(self.descriptors[task_id], self._task(task_id),
                                     self.root / 'optimizer-sources' / task_id)

    def base_harness(self):
        return self.root / 'base'

    def validate_tree(self, tree):
        if (not isinstance(tree, dict) or set(tree) != {HARNESS_FILE}
                or not isinstance(tree[HARNESS_FILE], str) or len(tree[HARNESS_FILE]) > 12000
                or '\x00' in tree[HARNESS_FILE]):
            raise ValueError('Oak supports only AGENTS.md, a reusable instruction text of at most 12000 characters. Other components, scripts, tools and permission changes are unavailable.')

    def _procedure(self, harness_dir):
        directory = Path(harness_dir or self.base_harness()).resolve(strict=True)
        files = [path for path in directory.rglob('*') if path.is_file()]
        if any(path.is_symlink() for path in directory.rglob('*')):
            raise ValueError('Harness files must be regular files.')
        if {path.relative_to(directory).as_posix() for path in files} != {HARNESS_FILE}:
            raise ValueError('The native procedure surface cannot install other files.')
        result = (directory / HARNESS_FILE).read_text()
        self.validate_tree({HARNESS_FILE: result})
        return result

    def execute(self, task_id, harness_dir, run_id, *, timeout=None):
        """Persist intent before native execution; completed run_id never re-executes."""
        if (not isinstance(run_id, str) or len(run_id) > 512 or len(run_id.split('/')) > 8
                or not all(_SAFE.fullmatch(segment) for segment in run_id.split('/'))):
            raise ValueError('Use a bounded non-escaping logical execution identity.')
        descriptor = self.descriptors[task_id]
        if evaluator_fingerprint()['sha256'] != self.implementation_sha256:
            raise ValueError('Native media implementation changed during this cycle.')
        procedure = self._procedure(harness_dir)
        duration = self.timeout if timeout is None else timeout
        if type(duration) is not int or not 30 <= duration <= self.timeout:
            raise ValueError('A probe cannot increase its fixed execution budget.')
        # Upstream logical IDs contain namespace/task/label separators. They
        # are retained in receipts; physical directories disclose no scenario.
        run_dir = self.root / 'executions' / hashlib.sha256(run_id.encode()).hexdigest()
        run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (run_dir / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            identity = {'run_id': run_id, 'descriptor_sha256': canonical_sha(descriptor),
                        'procedure_sha256': hashlib.sha256(procedure.encode()).hexdigest(),
                        'implementation_sha256': self.implementation_sha256,
                        'model': MODEL, 'effort': self.effort, 'timeout': duration}
            marker = run_dir / 'receipt.json'
            if marker.exists():
                prior = json.loads(marker.read_text())
                if any(prior.get(key) != value for key, value in identity.items()):
                    raise ValueError('An execution identity cannot be reused with changed inputs.')
                if prior.get('state') == 'finished':
                    if prior.get('terminal_integrity') == 'pending_validation':
                        # Native execution already returned. Recover only the
                        # unfinished journal validation, never the producer.
                        # Invalid evidence is a conservative completed negative;
                        # the interruption does not establish when it changed.
                        try:
                            self._verify_record(prior)
                            prior['terminal_integrity'] = 'verified'
                        except (OSError, ValueError):
                            prior.update(terminal_integrity='failed',
                                         integrity_error_code='native_terminal_integrity_failure')
                        prior['integrity_recovery'] = 'validation_resumed_from_terminal_receipt'
                        _write(marker, prior)
                    else:
                        # Evidence changed after a verified completion remains
                        # a hard integrity error, not a reclassified retry.
                        self._verify_record(prior)
                    return run_dir
                interrupted = run_dir / ('interrupted-' + str(prior.get('attempt', 1)) + '.json')
                if not interrupted.exists():
                    started = prior.get('state') == 'running'
                    prior.update(state='interrupted',
                                 classification=('interrupted_without_terminal_receipt' if started
                                                 else 'interrupted_before_producer_start'),
                                 producer_started=started, cause='unconfirmed')
                    _write(interrupted, prior)
                attempt = prior.get('attempt', 1) + 1
            else:
                attempt = 1
            intent = {**identity, 'state': 'pending_budget', 'attempt': attempt,
                      'run_dir': str(run_dir), 'started_at': time.time()}
            _write(marker, intent)
            if self.budget_hook is not None:
                self.budget_hook('producer', {**identity, 'attempt': attempt})
            intent['state'] = 'budget_reserved'
            _write(marker, intent)
            workspace = run_dir / ('attempt-' + str(attempt)) / 'workspace'
            task = materialize_descriptor(descriptor, workspace)
            record = {**identity, 'state': 'running', 'attempt': attempt,
                      'workspace': str(workspace), 'run_dir': str(run_dir), 'task': task,
                      'attempt_trace_path': str(workspace.parent / 'trace.json'),
                      'source_commitment': source_commitment(descriptor, task), 'started_at': time.time()}
            _write(marker, record)
            trace = []
            evidence_dir = run_dir / ('attempt-' + str(attempt)) / 'visible-evidence'
            runtime = self._runtime(trace, evidence_dir, duration, workspace, run_dir)
            with contextlib.ExitStack() as locks:
                if self.native_lock_path is not None:
                    self.native_lock_path.parent.mkdir(parents=True, exist_ok=True)
                    native_lock = locks.enter_context(self.native_lock_path.open('a'))
                    fcntl.flock(native_lock, fcntl.LOCK_EX)
                receipt = asyncio.run(runtime.run(task, workspace, procedure))
            # Native state snapshots originally live next to workspace. All are
            # inside this run directory and independently rehashed on resume.
            record.update(state='finished', receipt=receipt, finished_at=time.time(),
                          terminal_integrity='pending_validation')
            _write(run_dir / 'trace.json', trace)
            # A returned terminal result is durable before validation. A source
            # violation is a completed negative, never an interrupted retry.
            _write(marker, record)
            try:
                self._verify_record(record)
                record['terminal_integrity'] = 'verified'
            except (OSError, ValueError):
                record.update(terminal_integrity='failed',
                              integrity_error_code='native_terminal_integrity_failure')
            _write(marker, record)
            return run_dir

    def _runtime(self, trace, evidence_dir, duration, workspace, run_dir):
        if self.runtime_factory is not None:
            return self.runtime_factory(runtime_home=self.runtime_home, model=MODEL,
                                        effort=self.effort, timeout=duration)
        from .runtime import RuntimeClient

        def client_factory(**kwargs):
            original = kwargs.get('tool_handler')
            if original is not None:
                async def capture(metadata):
                    arguments = metadata.get('arguments') or {}
                    if isinstance(arguments, str):
                        arguments = json.loads(arguments)
                    entry = {'tool': metadata.get('tool'),
                             'arguments': self._public(arguments, workspace, run_dir)}
                    trace.append(entry)
                    _write(workspace.parent / 'trace.json', trace)
                    value = await original(metadata)
                    entry['result'] = self._observation(value, evidence_dir)
                    entry['result'] = self._public(entry['result'], workspace, run_dir)
                    _write(workspace.parent / 'trace.json', trace)
                    return value
                kwargs['tool_handler'] = capture
            return RuntimeClient(**kwargs)
        return PilotRuntime(runtime_home=self.runtime_home, model=MODEL, effort=self.effort,
                            timeout=duration, client_factory=client_factory)

    @staticmethod
    def _observation(result, evidence_dir):
        safe, images = [], []
        for item in result.get('contentItems', result.get('content', [])):
            if item.get('type') in {'inputImage', 'image'}:
                uri = item.get('imageUrl', item.get('image_url', ''))
                if isinstance(uri, dict):
                    uri = uri.get('url', '')
                if (isinstance(uri, str) and len(uri) <= 6 * 1024 * 1024
                        and uri.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,'))):
                    try:
                        raw = base64.b64decode(uri.split(';base64,', 1)[1], validate=True)
                    except ValueError:
                        continue
                    if len(raw) <= 4 * 1024 * 1024:
                        evidence_dir.mkdir(parents=True, exist_ok=True)
                        sha = hashlib.sha256(raw).hexdigest()
                        destination = evidence_dir / (sha + '.png')
                        if not destination.exists():
                            destination.write_bytes(raw)
                        images.append(destination.name)
                continue
            if item.get('type') in {'inputText', 'text'}:
                body = item.get('text', '')
                try:
                    body = json.loads(body)
                except (ValueError, TypeError):
                    pass
                safe.append(body)
        return {'observations': safe, 'image_files': images}

    def _public(self, value, workspace, run_dir):
        if isinstance(value, dict):
            return {key: self._public(child, workspace, run_dir) for key, child in value.items()}
        if isinstance(value, list):
            return [self._public(child, workspace, run_dir) for child in value]
        if isinstance(value, str):
            value = value.replace(str(workspace), '/workspace').replace(str(run_dir), '/execution')
            value = value.replace(str(self.root), '/cycle')
            if self.runtime_home:
                value = value.replace(str(self.runtime_home), '/native-runtime')
        return value

    def _verify_record(self, record):
        if record.get('terminal_integrity') == 'failed':
            return False
        if any(file_sha(path) != sha for path, sha in record['task']['source_hashes'].items()):
            raise ValueError('An immutable task source changed.')
        for prefix in ('artifact', 'candidate'):
            _verify_bound_artifact(record, prefix)
        return True

    def delivered(self, run_dir):
        record = _receipt(run_dir)
        valid = self._verify_record(record)
        receipt = record['receipt']
        return bool(valid and receipt.get('status') == 'completed' and receipt.get('delivered') is True
                    and receipt.get('artifact_path'))

    def trajectory(self, run_dir, *, obs_trunc):
        record = _receipt(run_dir)
        self._verify_record(record)
        path = Path(run_dir) / 'trace.json'
        trace = json.loads(path.read_text()) if path.exists() else []
        lines = []
        for event in trace:
            lines.extend(('[ACTION] ' + str(event['tool']) + '\n' + json.dumps(event.get('arguments'), ensure_ascii=False),
                          '[OBSERVATION] ' + json.dumps(event.get('result', {}), ensure_ascii=False)))
        receipt = record['receipt']
        summary = {key: receipt.get(key) for key in ('status', 'error_code', 'delivered',
                   'artifact_sha256', 'candidate_sha256', 'source_immutable', 'tool_calls',
                   'elapsed_seconds', 'render_calls', 'reviews')}
        summary.update(terminal_integrity=record.get('terminal_integrity'),
                       integrity_error_code=record.get('integrity_error_code'))
        lines.append('[OBSERVATION] Native execution receipt: ' + json.dumps(summary, ensure_ascii=False))
        return self._public('\n'.join(lines), Path(record['workspace']), Path(run_dir))

    def deliverable_text(self, run_dir):
        record = _receipt(run_dir)
        self._verify_record(record)
        receipt = record['receipt']
        return json.dumps({'delivered': self.delivered(run_dir),
                           'terminal_integrity': record.get('terminal_integrity'),
                           'artifact_sha256': receipt.get('artifact_sha256'),
                           'candidate_sha256': receipt.get('candidate_sha256'),
                           'visual_observations': receipt.get('reviews', [])}, ensure_ascii=False)

    def deliverable_files(self, run_dir):
        return {'native-artifact': self.deliverable_text(run_dir)}

    def deliverable_images(self, run_dir):
        record = _receipt(run_dir)
        if not self._verify_record(record):
            return []
        receipt = record['receipt']
        wanted = receipt.get('artifact_sha256') or receipt.get('candidate_sha256')
        images = []
        trace_path = Path(run_dir) / 'trace.json'
        trace = json.loads(trace_path.read_text()) if trace_path.exists() else []
        evidence = Path(run_dir) / ('attempt-' + str(record['attempt'])) / 'visible-evidence'
        for event in trace:
            result = event.get('result', {})
            if any(isinstance(row, dict) and row.get('sha256') == wanted
                   for row in result.get('observations', [])):
                images.extend(evidence / name for name in result.get('image_files', []))
        # Still-image artifact itself is exact native bytes and needs no decoding.
        path = _verify_bound_artifact(record, 'artifact') or _verify_bound_artifact(record, 'candidate')
        if not images and path is not None and path.suffix.lower() == '.png':
            images.append(path)
        return list(dict.fromkeys(images))[:8]

    def deliverable_assets(self, run_dir):
        """Pinned selector hook: actual image evidence, never a raw video path."""
        record = _receipt(run_dir)
        if not self._verify_record(record):
            return []
        receipt = record['receipt']
        if receipt.get('status') != 'completed' or receipt.get('delivered') is not True:
            return []
        wanted = receipt.get('artifact_sha256')
        if wanted != receipt.get('candidate_sha256'):
            raise ValueError('Selector evidence requires the exact delivered candidate SHA.')
        images = self.deliverable_images(run_dir)
        if images:
            for image in images:
                if (not image.resolve().is_relative_to(Path(run_dir).resolve())
                        or image.suffix.lower() not in {'.png', '.jpg', '.jpeg'}):
                    raise ValueError('Selector evidence must be a confined rendered image.')
                actual = file_sha(image)
                if _HEX.fullmatch(image.stem) and actual != image.stem:
                    raise ValueError('Archived rendered evidence changed.')
            return images
        artifact = _verify_bound_artifact(record, 'artifact')
        if artifact is None or artifact.suffix.lower() != '.mp4':
            return []
        return self._selector_video_assets(run_dir, artifact, wanted)

    def _selector_video_assets(self, run_dir, artifact, wanted):
        """Trusted decoder fallback when a producer never inspected its video.

        Only immutable delivered bytes enter the existing media inspector. No
        task properties, source masks, quality evaluator or verdict is used.
        """
        import io
        from .executor_worker import FileTransfers
        from . import quality_media

        identity = {'artifact_sha256': wanted, 'version': 1,
                    'inspector_sha256': file_sha(Path(quality_media.__file__))}
        directory = Path(run_dir) / 'selector-assets' / canonical_sha(identity)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        manifest = directory / 'evidence.json'
        with (directory / '.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if file_sha(artifact) != wanted:
                raise ValueError('The delivered artifact changed before image extraction.')
            if manifest.exists():
                saved = json.loads(manifest.read_text())
                if saved.get('identity') != identity or not 1 <= len(saved.get('images', [])) <= 8:
                    raise ValueError('Selector evidence cache identity changed.')
                paths = []
                for item in saved['images']:
                    name = item['name']
                    if (Path(name).name != name or Path(name).suffix not in {'.png', '.jpg'}
                            or file_sha(directory / name) != item['sha256']):
                        raise ValueError('Cached selector image changed.')
                    paths.append(directory / name)
                return paths
            files = FileTransfers({'workspace': str(Path(run_dir).resolve()),
                                   'runtime_home': str(directory)})
            try:
                observed = quality_media.inspect_media(files, str(artifact), wanted)
            finally:
                files.close_all()
            if (observed.get('sha256') != wanted or observed.get('type') != 'video'
                    or observed.get('decode_status') != 'complete' or file_sha(artifact) != wanted):
                raise ValueError('No complete image evidence from the exact delivered video.')
            images, paths, total = [], [], 0
            for item in observed.get('evidence', [])[:8]:
                url = item.get('image_url', '')
                prefix, separator, encoded = url.partition(',')
                suffix = {'data:image/png;base64': '.png',
                          'data:image/jpeg;base64': '.jpg'}.get(prefix)
                if not separator or suffix is None or len(encoded) > quality_media.EVIDENCE_BYTES:
                    raise ValueError('Unsupported selector image evidence.')
                data = base64.b64decode(encoded, validate=True)
                total += len(data)
                if not data or total > quality_media.EVIDENCE_BYTES:
                    raise ValueError('Selector image evidence exceeds its existing bound.')
                with Image.open(io.BytesIO(data)) as picture:
                    if picture.format not in {'PNG', 'JPEG'} or picture.width * picture.height > quality_media.MAX_PIXELS:
                        raise ValueError('Selector image evidence cannot be viewed.')
                    picture.verify()
                sha = hashlib.sha256(data).hexdigest()
                target = directory / (sha + suffix)
                if target.exists() and file_sha(target) != sha:
                    raise ValueError('Existing selector image changed.')
                target.write_bytes(data)
                images.append({'name': target.name, 'sha256': sha,
                               'label': str(item.get('label', 'Rendered video sample'))[:500]})
                paths.append(target)
            if not paths:
                raise ValueError('The delivered video has no inspectable selector evidence.')
            _write(manifest, {'identity': identity, 'images': images,
                              'coverage': observed.get('coverage', '')})
            return paths

    def deliverable_assets_note(self, names):
        return ('Rendered images from the exact delivered artifact are in `{anon}/`: '
                + ', '.join(names) + '. Inspect these images. Samples do not establish '
                'every frame or continuous playback.\n')

    def authorize_evaluation(self, frozen_sha256):
        if not isinstance(frozen_sha256, str) or not _HEX.fullmatch(frozen_sha256):
            raise ValueError('A committed freeze SHA is required.')
        if self._evaluation_freeze not in {None, frozen_sha256}:
            raise ValueError('The independent measurement freeze cannot change.')
        self._evaluation_freeze = frozen_sha256

    def score(self, run_dir, task_id):
        if self._evaluation_freeze is None:
            raise ValueError('Independent measurement is unavailable before the outer candidate freeze.')
        result = evaluate_delivery(self.descriptors[task_id], run_dir,
                                   freeze_sha256=self._evaluation_freeze)
        return {**result, 'n_passed': int(result['joint_passed']), 'n_criteria': 1,
                'all_pass': result['joint_passed']}
