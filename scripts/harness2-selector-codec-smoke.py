#!/usr/bin/env python3
"""Post-cycle codec/protocol smoke on an isolated copy of one real DEV artifact.

Never launches a model, calls a grader, opens holdout files, applies a patch, or
writes original native evidence. Run only after the coordinator confirms that
all selected-cycle model work has finished and installs the prospective asset hook.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from unittest.mock import patch


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    check(path.is_file() and not path.is_symlink() and path.stat().st_size <= 16*1024*1024,
          'Use a bounded ordinary evidence file.')
    return json.loads(path.read_text())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--pipeline-root', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--chat', type=int, required=True)
    parser.add_argument('--domain', required=True)
    parser.add_argument('--cycle-id', required=True)
    parser.add_argument('--artifact-sha256', required=True)
    parser.add_argument('--patch', required=True)
    parser.add_argument('--label', default='c2', help='Composed DEV final to inspect, for example c2.')
    parser.add_argument('--output-root')
    parser.add_argument('--models-complete', action='store_true')
    args = parser.parse_args(argv)
    check(args.models_complete, 'Coordinator confirmation --models-complete is required.')
    root, repo = Path(args.pipeline_root).resolve(strict=True), Path(args.repo).resolve(strict=True)
    scope = (args.project, args.chat, args.domain)
    scope_key = canonical(scope)
    scope_root = root / 'scopes' / hashlib.sha256(scope_key.encode()).hexdigest()

    # Existing global worker lock is read-only here. A running optimizer or
    # learned follow-up prevents the smoke from competing with native jobs.
    descriptor = os.open(root/'worker.lock', os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        db = sqlite3.connect((root/'pipeline.sqlite3').as_uri()+'?mode=ro', uri=True)
        try:
            row = db.execute('SELECT phase FROM cycles WHERE scope=? AND id=?',
                             (scope_key, args.cycle_id)).fetchone()
        finally:
            db.close()
        check(row is not None and row[0] == 'completed', 'The selected cycle is still active.')

        sys.path.insert(0, str(repo))
        from oak import harness2_media as media, quality_media
        from oak.harness2_upstream import upstream, _ContextTools, _snapshot
        core = upstream()
        check(hasattr(media.MediaBenchmarkAdapter, 'deliverable_assets'),
              'The prospective selector-assets hook has not been installed; no patch is applied by this script.')

        development = scope_root/'cycles'/args.cycle_id/'development'
        matches = []
        for receipt_path in (development/'executions').glob('*/receipt.json'):
            record = load(receipt_path)
            if record.get('run_id', '').endswith('/'+args.label):
                matches.append((receipt_path, record))
        check(len(matches) == 1, 'Expected exactly one frozen DEV final terminal receipt.')
        original_receipt, original = matches[0]
        check(original.get('state') == 'finished' and original.get('terminal_integrity') == 'verified',
              'DEV final lacks a verified terminal receipt.')
        native = original['receipt']
        check(native.get('status') == 'completed' and native.get('delivered') is True
              and native.get('artifact_sha256') == native.get('candidate_sha256') == args.artifact_sha256,
              'DEV final differs from the declared immutable delivered artifact.')

        def confined(path):
            path = Path(path).absolute()
            check('..' not in path.parts and path.is_relative_to(development), 'Only selected DEV evidence may be read.')
            check(not any(parent.is_symlink() for parent in (path, *path.parents)), 'Evidence links are forbidden.')
            check(path.is_file() and path.stat().st_size <= 64*1024*1024, 'Smoke source exceeds its fixed copy bound.')
            return path

        source_paths = {confined(path): expected for path, expected in original['task']['source_hashes'].items()}
        artifact = confined(native['artifact_path'])
        candidate = confined(native['candidate_path'])
        check(artifact.suffix.lower() == '.mp4', 'This smoke specifically requires actual MP4 bytes.')
        trace_path = confined(original_receipt.parent/'trace.json')
        originals = {original_receipt: sha(original_receipt), trace_path: sha(trace_path),
                     artifact: args.artifact_sha256, candidate: args.artifact_sha256, **source_paths}
        check(all(sha(path) == expected for path, expected in originals.items()), 'Original DEV bytes changed.')

        output = Path(args.output_root) if args.output_root else Path(tempfile.mkdtemp(prefix='oak-h2-selector-codec-smoke-'))
        output = output.absolute()
        check(not output.is_relative_to(root) and not output.is_relative_to(repo), 'Keep smoke copies outside native state and source checkout.')
        if output.exists():
            check(output.is_dir() and not any(output.iterdir()) and not output.is_symlink(), 'Use a fresh empty smoke root.')
        else:
            output.mkdir(parents=True, mode=0o700)
        output.chmod(0o700)
        run_dir = output/'isolated-dev-run'
        run_dir.mkdir(mode=0o700)
        (run_dir/'sources').mkdir(mode=0o700)
        copied = json.loads(canonical(original))
        replacements = {}
        copied_sources = {}
        for index, (path, expected) in enumerate(sorted(source_paths.items())):
            destination = run_dir/'sources'/(str(index)+'-'+path.name)
            shutil.copyfile(path, destination)
            check(sha(destination) == expected, 'Copied DEV source bytes changed.')
            replacements[str(path)] = str(destination)
            copied_sources[str(destination)] = expected
        copied['task']['source_hashes'] = copied_sources

        def replace_paths(value):
            if isinstance(value, dict):
                return {key: replace_paths(child) for key, child in value.items()}
            if isinstance(value, list):
                return [replace_paths(child) for child in value]
            return replacements.get(value, value) if isinstance(value, str) else value

        copied['task'] = replace_paths(copied['task'])
        copied['run_dir'] = str(run_dir)
        copied['workspace'] = str(run_dir/'workspace')
        (run_dir/'workspace').mkdir(mode=0o700)
        frozen = run_dir/'frozen.mp4'
        shutil.copyfile(artifact, frozen)
        frozen.chmod(0o400)
        copied['receipt']['artifact_path'] = copied['receipt']['candidate_path'] = str(frozen)
        (run_dir/'receipt.json').write_text(canonical(copied)+'\n')
        shutil.copyfile(trace_path, run_dir/'trace.json')

        # No executor/config constructor runs: only the real completed-receipt
        # hooks required by the pinned selector are exercised.
        adapter = object.__new__(media.MediaBenchmarkAdapter)
        adapter.root, adapter.runtime_home = output, None
        check(adapter.deliverable_images(run_dir) == [],
              'DEV final already has artifact-bound trace images; this is not the missing-image case.')

        def context(folder):
            directory = output/folder
            candidates = directory/'candidates'
            candidates.mkdir(parents=True)
            selected = core.selection.Selection('dev-final-codec-smoke', 'seq', 2, id_map={'A': args.label})
            rollout = core.spec.RunView(args.label, original['run_id'], run_dir, True, True)
            names = core.selection._write_candidates(selected, {args.label: adapter.deliverable_text(run_dir)},
                        {args.label: rollout}, candidates, adapter)
            check(bool(names), 'Pinned selector received no rendered evidence.')
            core.context.assert_no_grader_verdicts(directory)
            core.context.assert_no_leak_paths(directory, ('receipt.json','evaluation.json','*grader*','*holdout*','*mask*'))
            check(all(path.suffix.lower() in {'.md','.png','.jpg','.jpeg'} for path in directory.rglob('*') if path.is_file()),
                  'Unexpected metadata or raw video entered selector context.')
            return directory, names

        started = time.monotonic()
        # These functions must remain uncalled. Real decoding is exclusively
        # quality_media.inspect_media and its existing fixed-codec paths.
        with (patch.object(media, 'evaluate_artifact', side_effect=AssertionError('Grader called.')),
              patch.object(media, 'evaluate_delivery', side_effect=AssertionError('Grader called.'))):
            with patch.object(quality_media, 'inspect_media', wraps=quality_media.inspect_media) as inspect:
                first_context, first_names = context('first-context')
                check(inspect.call_count == 1, 'Expected exactly one real inspector/codec call.')
            with patch.object(quality_media, 'inspect_media', side_effect=AssertionError('Cache decoded video again.')):
                second_context, second_names = context('cached-context')
                check(first_names == second_names, 'Cache changed the selector asset names.')
                for name in first_names:
                    check((first_context/'candidates/A'/name).read_bytes() == (second_context/'candidates/A'/name).read_bytes(),
                          'Cached selector assets changed.')

        files = _snapshot(first_context, 64*1024*1024)
        tools = _ContextTools(files, first_context, ('AGENTS.md',), 12000, 64, None, {})
        tools.thread_id = 'codec-smoke-no-model'
        viewed = []
        for name in sorted(first_names):
            relative = 'candidates/A/'+name
            result = asyncio.run(tools.handle({'tool':'oak_h2_view','threadId':tools.thread_id,
                                               'arguments':{'path':relative}}))
            check(result['success'], 'Real role view tool rejected the copied selector image.')
            images = [item for item in result['contentItems'] if item.get('type') == 'inputImage']
            check(len(images) == 1 and base64.b64decode(images[0]['imageUrl'].split(',',1)[1], validate=True) == files[relative],
                  'Role tool did not display the exact copied image bytes.')
            viewed.append({'name':name, 'sha256':hashlib.sha256(files[relative]).hexdigest()})

        manifests = list((run_dir/'selector-assets').glob('*/evidence.json'))
        check(len(manifests) == 1, 'Unexpected selector evidence cache count.')
        manifest = load(manifests[0])
        check(manifest.get('identity', {}).get('artifact_sha256') == args.artifact_sha256,
              'Selector evidence manifest is not bound to delivered bytes.')
        image = manifests[0].parent/manifest['images'][0]['name']
        original_image = image.read_bytes()
        image.write_bytes(b'changed copied selector image')
        with patch.object(quality_media, 'inspect_media', side_effect=AssertionError('Tamper triggered a new decode.')):
            try:
                adapter.deliverable_assets(run_dir)
            except ValueError:
                pass
            else:
                raise AssertionError('Changed cached selector image was accepted.')
        image.write_bytes(original_image)
        frozen_bytes = frozen.read_bytes()
        frozen.chmod(0o600)
        frozen.write_bytes(frozen_bytes+b'changed copied delivery')
        try:
            with patch.object(quality_media, 'inspect_media', side_effect=AssertionError('Changed delivery was decoded.')):
                try:
                    adapter.deliverable_assets(run_dir)
                except ValueError:
                    pass
                else:
                    raise AssertionError('Changed delivered artifact was accepted.')
        finally:
            frozen.write_bytes(frozen_bytes)
            frozen.chmod(0o400)
        check(all(sha(path) == expected for path, expected in originals.items()), 'Original native evidence was mutated.')
        report = {'schema':'oak-selector-assets-codec-smoke-v1', 'cycle_id':args.cycle_id,
            'recorded_at':datetime.now(timezone.utc).isoformat(),
            'upstream_commit':core.commit, 'original_run_id':original['run_id'],
            'original_receipt_sha256':originals[original_receipt], 'artifact_sha256':args.artifact_sha256,
            'media_adapter_sha256':sha(Path(media.__file__)), 'inspector_sha256':sha(Path(quality_media.__file__)),
            'patch_sha256':sha(Path(args.patch)), 'real_inspector_calls':1, 'model_calls':0, 'grader_calls':0,
            'image_count':len(viewed), 'images':viewed, 'cache_reused_without_codec':True,
            'cache_tamper_rejected_without_codec':True, 'role_view_exact_bytes':True,
            'delivered_artifact_tamper_rejected_without_codec':True,
            'original_native_evidence_unchanged':True, 'only_isolated_copy_mutated':True,
            'no_independent_grader_files_in_context':True, 'elapsed_seconds':round(time.monotonic()-started,3),
            'evidence_manifest':manifest, 'quality_gain_claim':False, 'original_cycle_measurement_reinterpreted':False,
            'limitations':['Post-cycle unmeasured interface repair; original cycle source seal is unchanged.',
                'Rendered samples do not establish every video frame or continuous playback.',
                'Inspector labels/crop coordinates/coverage stay in the cache manifest; pinned selector receives image files and a generic sample note.',
                'View-tool protocol exercised directly without a model session; no selector choice or quality gain is measured.']}
        (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)+'\n')
        print(json.dumps({'output':str(output/'report.json'), 'artifact_sha256':args.artifact_sha256,
                          'image_count':len(viewed), 'model_calls':0, 'grader_calls':0}))
    finally:
        os.close(descriptor)


if __name__ == '__main__':
    main()
