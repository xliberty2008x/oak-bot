#!/usr/bin/env python3
"""Offline diagnostic for four already revealed losses; never optimizer input.

Example (Pillow/numpy and ffmpeg required):
  python reproduce-regression-diagnosis.py --root /path/to/preserved-pipeline \
    --output /path/to/new-diagnostic-output

Reads immutable saved inputs, masks, raw frames and delivered bytes. Calls source
constructors only: no model, grader, render-code execution or acceptance rescore.
The source-derived consequence is a construction reference, not a native result.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rgb(path):
    with Image.open(path) as image:
        return image.convert('RGB')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--engine', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--decoded-cache', type=Path,
                        help='Optional existing diagnostic frame12 PNGs; avoids decoding.')
    args = parser.parse_args()
    root = args.root.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        parser.error('--output must be an empty directory')
    here = Path(__file__).resolve().parent
    report = json.loads((here / 'regression-diagnosis.json').read_text())
    export_path = here / report['source_export']['path']
    assert sha(export_path) == report['source_export']['sha256']
    export = json.loads(export_path.read_text())
    sys.path.insert(0, str(args.engine.resolve()))
    from oak import harness2_media as media
    from oak import media_pilot_complex_tasks as source

    descriptors = {item['payload']['descriptor']['id']: item['payload']['descriptor']
                   for item in export['cases'] if 'descriptor' in item['payload']}
    for cycle in export['cycles']:
        for split in ('dev', 'regression', 'holdout'):
            for item in cycle['journal']['manifest'][split]:
                descriptor = item['descriptor']
                descriptors[descriptor['id']] = descriptor
    findings = []
    for case in report['results']:
        task = case['task_id']
        descriptor = descriptors.get(task) or media.exposed_regression(task)
        bindings = case['evidence_bindings']
        frames = {}
        masks = {}
        for arm in ('baseline', 'candidate'):
            binding = bindings[arm]
            workspace = root / binding['workspace']
            for name, expected in binding['source_files'].items():
                assert sha(workspace / 'inputs' / name) == expected
            artifact = root / binding['artifact']
            assert sha(artifact) == case[f'{arm}_artifact_sha256']
            raw_path = workspace / binding['raw_frame']
            assert sha(raw_path) == binding['raw_frame_sha256']
            frames[arm + '_raw'] = read_rgb(raw_path)
            masks[arm] = [Image.open(workspace / name).convert('L')
                          for name in binding['masks']]
            name = f"c{case['cycle']}-{task}-{arm}-f12.png"
            cached = args.decoded_cache / name if args.decoded_cache else None
            if cached is not None and cached.is_file():
                assert sha(cached) == binding['diagnostic_decoded_frame_sha256']
                frames[arm + '_delivered'] = read_rgb(cached)
            else:
                decoded = args.output / name
                subprocess.run([
                    'ffmpeg', '-v', 'error', '-threads', '1', '-i', str(artifact),
                    '-vf', 'select=eq(n\\,12)', '-vsync', '0', '-frames:v', '1',
                    '-threads', '1', str(decoded)], check=True, timeout=15)
                frames[arm + '_delivered'] = read_rgb(decoded)
            assert sha(artifact) == case[f'{arm}_artifact_sha256']
        facts = media.quality_descriptor(descriptor, root / bindings['candidate']['workspace'])
        frames['source_consequence'] = source._authored_frame(facts, 12)
        scene = np.array(read_rgb(root / bindings['candidate']['workspace'] / 'inputs/scene.png'))
        expected = np.array(frames['source_consequence']).astype(int)
        true_masks = [fg['rgba'].getchannel('A') for fg in facts['properties']['foregrounds']]
        outside = np.ones(scene.shape[:2], dtype=bool)
        mask_counts = {}
        for real in true_masks:
            outside &= np.array(real.filter(ImageFilter.MaxFilter(5))) == 0
        for arm in ('baseline', 'candidate'):
            mask_counts[arm] = []
            for actual, real in zip(masks[arm], true_masks):
                observed = np.array(actual) > 0
                mask_counts[arm].append({
                    'false_positive_pixels_beyond2px': int(np.sum(
                        observed & (np.array(real.filter(ImageFilter.MaxFilter(5))) == 0))),
                    'false_negative_pixels_beyond2px': int(np.sum(
                        ~observed & (np.array(real.filter(ImageFilter.MinFilter(5))) > 0)))})
        assert mask_counts == case['mask_metrics']
        actor_findings = {}
        for actor in facts['properties']['actors']:
            alpha = Image.new('L', frames['source_consequence'].size)
            alpha.paste(actor['rgba'].getchannel('A'), tuple(actor['positions'][12]))
            stable = np.array(alpha.filter(ImageFilter.MinFilter(5))) > 0
            selected = (stable & outside &
                        (np.max(np.abs(expected - scene.astype(int)), axis=2) >= 20) &
                        np.all(np.array(frames['candidate_raw']) == scene, axis=2))
            count = int(selected.sum())
            restored = int(np.sum(selected & np.all(
                np.array(frames['baseline_raw']) == expected, axis=2)))
            mae = lambda value: float(np.mean(np.abs(
                np.array(value).astype(int)[selected] - expected[selected])))
            actor_findings[actor['id']] = {
                'lost_original_source_pixels_beyond2px_foreground_and_actor_edges': count,
                'baseline_restored_exact_raw_pixels': restored,
                'baseline_decoded_source_rgb_mae': mae(frames['baseline_delivered']),
                'candidate_decoded_source_rgb_mae': mae(frames['candidate_delivered'])}
            for key, value in actor_findings[actor['id']].items():
                assert abs(value - case['actor_pixels'][actor['id']][key]) < 1e-9
        crop = tuple(case['crop_box'])
        canvas = Image.new('RGB', (1020, 240), 'white')
        draw = ImageDraw.Draw(canvas)
        draw.text((8, 4), f"C{case['cycle']} {task} | frame 12", fill='black')
        columns = [('BASE raw', 'baseline_raw'), ('CAND raw', 'candidate_raw'),
                   ('SOURCE consequence', 'source_consequence'),
                   ('BASE delivered', 'baseline_delivered'),
                   ('CAND delivered', 'candidate_delivered')]
        for index, (title, key) in enumerate(columns):
            draw.text((index * 204 + 6, 24), title, fill='black')
            canvas.paste(frames[key].crop(crop).resize((192, 192), Image.Resampling.NEAREST),
                         (index * 204 + 6, 44))
        proof = args.output / case['proof_file']
        canvas.save(proof)
        findings.append({'cycle': case['cycle'], 'task_id': task,
                         'mask_metrics': mask_counts, 'actor_pixels': actor_findings,
                         'proof_file': proof.name, 'proof_sha256': sha(proof)})
    result = {'scope': report['scope'], 'all_original_diagnostic_counts_reproduced': True,
              'results': findings}
    (args.output / 'reproduced-diagnosis.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'cases': len(findings), 'all_original_diagnostic_counts_reproduced': True}))


if __name__ == '__main__':
    main()
