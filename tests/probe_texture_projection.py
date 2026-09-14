"""Optional SDK UV oracle, isolated from the converter and the installed helper."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from export_lightwave_animation import prepare_scene


def cases():
    points = [(0.2, 0.3, 0.4), (-0.7, 0.2, -0.1), (0, 1, 0), (1, 0, 0), (0, 0, 1)]
    variants = [dict(center=[0, 0, 0], rotation=[0, 0, 0], size=[1, 1, 1], tiles=[1, 1]),
                dict(center=[.1, -.2, .3], rotation=[0, 0, 0], size=[2, 3, 4], tiles=[1, 1]),
                dict(center=[.1, -.2, .3], rotation=[.37, -.28, .19], size=[2, 3, 4], tiles=[1, 1]),
                dict(center=[0, 0, 0], rotation=[0, 0, 0], size=[-2, 3, .7], tiles=[2.5, .75]),
                dict(center=[0, 0, 0], rotation=[0, 0, 0], size=[1, 1, 1], tiles=[-1, 0])]
    return [dict(projection=p, axis=a, **v, point=list(pt))
            for p in (0, 2) for a in range(3) for v in variants for pt in points]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    output=args.output.resolve(); output.mkdir(parents=True, exist_ok=False)
    native=output/'native'; native.mkdir()
    source=(ROOT/'tools/lightwave_capture/capture.c').read_text('utf-8')
    marker='static void process(LWInstance instance,const LWFilterAccess *access) {'
    assert source.count(marker)==1
    source=source.replace(marker, '#include "'+(ROOT/'tests/texture_projection_oracle.h').as_posix()+'"\n'+marker+'\n    texture_projection_probe();')
    (native/'capture.c').write_text(source, encoding='utf-8')
    (native/'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.21)\nproject(texture_oracle C)\n'
        'add_library(probe MODULE capture.c)\ntarget_include_directories(probe PRIVATE "'+args.sdk.resolve().as_posix()+'/include")\n'
        'target_compile_definitions(probe PRIVATE _MSWIN _CRT_SECURE_NO_WARNINGS)\n'
        'set_target_properties(probe PROPERTIES SUFFIX .p PREFIX "" MSVC_RUNTIME_LIBRARY MultiThreaded)\n', encoding='utf-8')
    subprocess.run(['cmake','-S',str(native),'-B',str(native/'build'),'-G','Visual Studio 17 2022','-A',
                    'Win32' if args.runtime=='lightwave6' else 'x64'],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['cmake','--build',str(native/'build'),'--config','Release'],check=True,stdout=subprocess.DEVNULL)
    helper=native/'build/Release/probe.p'
    config=cases()
    inputs=output/'inputs.txt'; observations=output/'observations.txt'
    inputs.write_text(''.join(' '.join(map(str,[c['projection'],c['axis'],*c['center'],*c['rotation'],*c['size'],*c['tiles'],*c['point']]))+'\n' for c in config))
    evaluation=output/'evaluation'; evaluation.mkdir(); (evaluation/'frames').mkdir()
    working,cfg,audit=prepare_scene(b'LWSC\n3\nFirstFrame 0\nLastFrame 0\nFramesPerSecond 25\n',
        {'version':3,'nodes':[]},[],evaluation,args.lightwave_root.resolve(),helper,args.runtime)
    host=args.lightwave_root.resolve()/('LWSN.exe' if args.runtime=='lightwave6' else 'Programs/lwsn.exe')
    env={**os.environ,'LWCONVERT_TEXTURE_INPUT':str(inputs),'LWCONVERT_TEXTURE_OUTPUT':str(observations)}
    def invoke(directory, command):
        env['LWCONVERT_CAPTURE_DIR']=str(directory/'frames')
        with (evaluation/'screamernet.log').open('wb') as log:
            subprocess.run(command,cwd=directory,env=env,stdout=log,stderr=subprocess.STDOUT,
                           timeout=60,creationflags=subprocess.CREATE_NO_WINDOW,check=True)
    if args.runtime=='lightwave6':
        with tempfile.TemporaryDirectory(prefix='lw6-') as temp:
            short=Path(temp); shutil.copytree(evaluation,short,dirs_exist_ok=True)
            invoke(short,[str(host),'-3','-cconfig/LW3.CFG','-d'+str(short),'evaluation.lws','0','0','1'])
    else:
        invoke(evaluation,[str(host),'-3','-c'+str(cfg),'-d'+str(evaluation),str(working),'0','0','1'])
    rows=[line.split() for line in observations.read_text().splitlines()]
    assert len(rows)==len(config), (len(rows),len(config))
    for i,(c,row) in enumerate(zip(config,rows)):
        assert int(row[0])==i, row
        # LW6 returns zero from setters which do change the evaluated mapping.
        # Retain the return value instead of treating it as a portable boolean.
        c['setter_result']=int(row[1])
        c['uv']=list(map(float,row[2:]))
    report=dict(runtime=args.runtime,host_sha256=digest(host),helper_sha256=digest(helper),
                inputs_sha256=digest(inputs),observations_sha256=digest(observations),
                scope='Native SDK Texture Functions evaluateUV; raw UVs before polygon seam/pole reconstruction.',cases=config)
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps({'cases':len(config),'report':str(output/'report.json')}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--sdk',required=True,type=Path)
    parser.add_argument('--lightwave-root',required=True,type=Path)
    parser.add_argument('--runtime',choices=('lightwave6','lightwave96'),default='lightwave96')
    run(parser.parse_args())
