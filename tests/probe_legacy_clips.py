"""Optional native SDK probe of LWSC1 clip parameters; never used by conversion.

Requires a local Windows x64 LightWave host and SDK supporting clipMap (6.5+).
Builds an isolated temporary capture plugin; leaves installed configs untouched.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from export_lightwave_animation import prepare_scene


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lightwave-root',type=Path,required=True)
    parser.add_argument('--sdk',type=Path,required=True,help='SDK include directory')
    parser.add_argument('--output',type=Path,required=True,help='New isolated working directory')
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    out=args.output.resolve(); out.mkdir(parents=True,exist_ok=False)
    native=out/'native'; native.mkdir()
    marker='static void process(LWInstance instance,const LWFilterAccess *access) {'
    capture=(ROOT/'tools/lightwave_capture/capture.c').read_text('utf-8')
    assert capture.count(marker)==1
    (native/'capture.c').write_text(capture.replace(marker,'#include "legacy_clip_probe.h"\n'+marker+'\nlegacy_clip_probe();'),encoding='utf-8')
    shutil.copyfile(Path(__file__).with_name('legacy_clip_probe.h'),native/'legacy_clip_probe.h')
    (native/'CMakeLists.txt').write_text(
        'cmake_minimum_required(VERSION 3.21)\nproject(clip_probe C)\nadd_library(probe MODULE capture.c)\n'
        f'target_include_directories(probe PRIVATE "{args.sdk.resolve().as_posix()}")\n'
        'target_compile_definitions(probe PRIVATE _MSWIN _CRT_SECURE_NO_WARNINGS)\n'
        'set_target_properties(probe PROPERTIES SUFFIX .p PREFIX "" MSVC_RUNTIME_LIBRARY MultiThreaded)\n',encoding='utf-8')
    for command in (['cmake','-S',str(native),'-B',str(native/'build'),'-G','Visual Studio 17 2022','-A','x64'],
                    ['cmake','--build',str(native/'build'),'--config','Release']):
        subprocess.run(command,check=True,stdout=subprocess.DEVNULL)
    # Minimal independent LWOB plane, no external content or image plugins.
    def chunk(tag,payload): return tag+struct.pack('>I',len(payload))+payload+bytes(len(payload)%2)
    body=b'LWOB'+chunk(b'PNTS',struct.pack('>12f',-2,-2,0,2,-2,0,2,2,0,-2,2,0))
    body+=chunk(b'SRFS',b'face\0\0')+chunk(b'POLS',struct.pack('>6H',4,0,1,2,3,1))
    source=out/'plane.lwo'; source.write_bytes(b'FORM'+struct.pack('>I',len(body))+body)
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    raw=b'LWSC\n1\nFirstFrame 0\nLastFrame 0\n'; nodes=[]; assets=[]; cases=[]
    for flags in (0,1,2,4,6,8,15):
        for value in (0,.5,1):
            name=f'f{flags}_v{value}'; i=len(nodes)
            raw+=f'LoadObject {name}.lwo\nObjectMotion\n9\n1\n0 0 10 0 0 0 1 1 1\n0 0 0 0 0\nEndBehavior 1\nClipMap Planar Image Map\nTextureImage (none)\nTextureFlags {flags}\nTextureAxis 2\nTextureWrapModes 2 3\nTextureSize 3.98 3.98 1\nTextureValue {value}\nShadowOptions 7\n'.encode()
            nodes.append({'asset_index':i,'id':0x10000000+i})
            assets.append({'source_path':name+'.lwo','source_copy':source,'id':source_hash})
            cases.append(dict(name=name,flags=flags,value=value))
    evaluation=out/'evaluation'; evaluation.mkdir(); (evaluation/'frames').mkdir()
    working,cfg,_=prepare_scene(raw,{'version':1,'nodes':nodes},assets,evaluation,args.lightwave_root.resolve(),native/'build/Release/probe.p')
    host=args.lightwave_root.resolve()/'Programs/lwsn.exe'; observations=out/'observations.txt'
    with (out/'host.log').open('wb') as log:
        subprocess.run([str(host),'-3','-c'+str(cfg),'-d'+str(evaluation),str(working),'0','0','1'],cwd=evaluation,
                       env={**os.environ,'LWCONVERT_CLIP_PROBE':str(observations),'LWCONVERT_CAPTURE_DIR':str(evaluation/'frames')},
                       stdout=log,stderr=subprocess.STDOUT,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW,check=True)
    lines=observations.read_text('utf-8').splitlines()
    assert len(lines)==len(cases)
    for case,line in zip(cases,lines):
        tokens=line.split(); assert tokens[0]==case['name']
        params=dict(token.split('=',1) for token in tokens[1:]); flags=case['flags']
        expected=dict(axis='2',coord=str(flags&1),invert=str(int(bool(flags&2))),pix=str(int(bool(flags&4))),
                      aa=str(int(bool(flags&8))),wrap='2,3',proj='0',blend='0',opacity='1')
        assert all(params[k]==v for k,v in expected.items()), (case,params)
        case['observed']=params
    report=dict(passed=True,host=str(host),host_sha256=hashlib.sha256(host.read_bytes()).hexdigest(),
                input_lwsc_version=1,source_scene_sha256=hashlib.sha256(raw).hexdigest(),cases=cases,
                scope='LWSC1 image-clip parameter import read via LWTextureFuncs. No image is assigned; this measures flags, mapping and layer opacity, not rendered cutoff or image filtering.')
    args.report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(f'{len(cases)} native parameter cases passed; report: {args.report}')


if __name__=='__main__': main()
