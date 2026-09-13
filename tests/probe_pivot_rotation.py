"""Optional native FK measurements for pivot composition; never used at runtime."""
import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from probe_skinning_oracle import ROOT, mesh_bytes, motion
from export_lightwave_animation import prepare_scene
from lightwave_animation import digest, read_capture


def cases():
    base = dict(pivot_rotation=[37, -28, 19], pivot=[.2, -.3, .4],
                values=[1, 2, -3, .31, -.47, .23, 1, 1, 1])
    result = []
    for name, overrides in (
        ('neutral-pivot', dict(pivot_rotation=[0, 0, 0])),
        ('rotation-only', dict(pivot=[0, 0, 0])),
        ('translated-pivot', {}),
        ('nonuniform-scale', dict(values=[1, 2, -3, .31, -.47, .23, .7, 1.3, 2])),
        ('negative-scale', dict(values=[1, 2, -3, .31, -.47, .23, -1, 1, 1])),
        ('half-turn', dict(pivot_rotation=[180, -90, 270])),
        ('parented', dict(parent=2)),
        ('bone-zero-rest', dict(bone=True, rest=[0, 0, 0], pivot=[0, 0, 0])),
        ('bone-combined-rest', dict(bone=True, rest=[17, -24, 31], pivot=[0, 0, 0])),
    ):
        case = {**base, **overrides, 'name': name}
        if case.get('bone'):
            case['values'] = [0, 0, 0, *map(math.radians, case['rest']), 1, 1, 1]
        case['moved'] = list(case['values'])
        for i, delta in enumerate((.17, -.29, .38), 3): case['moved'][i] += delta
        result.append(case)
    return result


def scene_bytes(config):
    text = 'LWSC\n3\nFirstFrame 0\nLastFrame 1\nFramesPerSecond 25\n'
    for i, case in enumerate(config):
        text += 'LoadObject probe.lwo\n'
        if case.get('bone'):
            text += motion('Object', [0, 0, 0, 0, 0, 0, 1, 1, 1])
            text += ('AddBone\nBoneName probe\nBoneRestPosition 0 0 0\nBoneRestDirection '
                     + ' '.join(map(str, case['rest'])) + '\nBoneRestLength 1\nBoneActive 1\n'
                     'BoneWeightMapName weight\nBoneWeightMapOnly 1\nBoneNormalization 1\n'
                     'BoneStrength 1\nScaleBoneStrength 0\n' + f'ParentItem {0x10000000+i:08x}\n')
        text += motion('Bone' if case.get('bone') else 'Object', case['values'], case['moved'])
        text += 'PivotRotation ' + ' '.join(map(str, case['pivot_rotation'])) + '\n'
        text += 'PivotPosition ' + ' '.join(map(str, case['pivot'])) + '\n'
        if 'parent' in case: text += f"ParentItem {0x10000000+case['parent']:08x}\n"
    return text.encode('ascii')


def run(output, runtime, lightwave, helper, converter):
    output.mkdir(parents=True, exist_ok=False)
    source = output/'source'; source.mkdir()
    config = cases()
    points = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]
    (source/'probe.lwo').write_bytes(mesh_bytes(points, [('weight', [(i, 1) for i in range(4)])]))
    path = source/'probe.lws'; path.write_bytes(scene_bytes(config))
    package = output/'package'
    result = subprocess.run([str(converter), 'convert', str(path), '--content-root', str(source),
                             '--output', str(package), '--bake-ik', 'off'], capture_output=True, timeout=60)
    if result.returncode not in (0, 2): raise RuntimeError(result.stderr.decode('utf-8'))
    manifest = json.loads((package/'manifest.json').read_text('utf-8'))
    scene = json.loads((package/manifest['scene']).read_text('utf-8'))
    assets = [{**a, 'source_copy': (package/a['uri']).with_name('source.bin')} for a in manifest['assets']]
    evaluation = output/'evaluation'; evaluation.mkdir()
    working, cfg, audit = prepare_scene(path.read_bytes(), scene, assets, evaluation, lightwave, helper, runtime)
    frames = evaluation/'frames'; frames.mkdir()
    host = lightwave/('LWSN.exe' if runtime == 'lightwave6' else 'Programs/lwsn.exe')
    env = os.environ.copy()

    def invoke(directory, command):
        env['LWCONVERT_CAPTURE_DIR'] = str(directory/'frames')
        with (directory/'screamernet.log').open('wb') as log:
            result = subprocess.run(command, cwd=directory, env=env, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=120, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode: raise RuntimeError(f'Native host failed: {result.returncode}')

    if runtime == 'lightwave6':
        with tempfile.TemporaryDirectory(prefix='lw6-') as tmp:
            short = Path(tmp).resolve()
            if len(str(short)) > 70: raise ValueError('LW6 requires a shorter TEMP path')
            shutil.copytree(evaluation, short, dirs_exist_ok=True)
            try: invoke(short, [str(host), '-3', '-cconfig/LW3.CFG', '-d'+str(short), 'evaluation.lws', '0', '1', '1'])
            finally:
                shutil.copytree(short/'frames', frames, dirs_exist_ok=True)
                if (short/'screamernet.log').exists(): shutil.copyfile(short/'screamernet.log', evaluation/'screamernet.log')
    else:
        invoke(evaluation, [str(host), '-3', '-c'+str(cfg), '-d'+str(evaluation), str(working), '0', '1', '1'])
    log = (evaluation/'screamernet.log').read_text('latin1')
    if 'Instance creation failed' in log or "Can't load plug-in" in log: raise RuntimeError('Capture plugin did not load')
    captures = [read_capture(frames/f'frame-{i:06d}.txt') for i in (0, 1)]
    observations = []
    for i, case in enumerate(config):
        owner = 0x10000000+i
        item = 0x40000000+i if case.get('bone') else owner
        # FK only: use native SDK basis vectors, never the Python reconstruction
        # of LW6 post-IK channels (protocol 3 does not record pivot rotation).
        matrices = [c['items'][item].get('sdk_matrix', c['items'][item]['matrix']) for c in captures]
        observations.append({'case': case, 'world_matrices': matrices,
                             'points': [c['meshes'][owner]['points'] for c in captures]})
    report = {'runtime': runtime, 'host_sha256': digest(host), 'helper_sha256': digest(helper),
              'source_sha256': digest(path), 'capture_sha256': [digest(frames/f'frame-{i:06d}.txt') for i in (0, 1)],
              'audit': audit, 'observations': observations,
              'scope': 'Native FK SDK matrices and deformed points; no IK and no reconstructed pivot matrix used as reference.'}
    (output/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime', choices=('lightwave6', 'lightwave96'), required=True)
    parser.add_argument('--lightwave-root', type=Path, required=True)
    parser.add_argument('--capture-plugin', type=Path, required=True)
    parser.add_argument('--converter', type=Path, default=ROOT/'bin/win64/lwconvert.exe')
    args = parser.parse_args()
    report = run(args.output.resolve(), args.runtime, args.lightwave_root.resolve(), args.capture_plugin.resolve(), args.converter.resolve())
    print(json.dumps({'runtime': args.runtime, 'cases': len(report['observations']), 'report': str(args.output/'report.json')}))


if __name__ == '__main__': main()
