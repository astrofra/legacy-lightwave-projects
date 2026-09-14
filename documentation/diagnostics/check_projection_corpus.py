"""Reconvert the audited LWO2 planar/spherical corpus and texture controls."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]


def read(path): return json.loads(path.read_text(encoding='utf-8-sig'))


def inventory(batch):
    objects={}
    for entry in read(batch/'batch-report.json')['files']:
        if not entry.get('manifest'): continue
        mp=batch/entry['manifest']; manifest=read(mp)
        for asset in manifest['assets']:
            if asset['id'] not in objects:
                objects[asset['id']]=read(mp.parent/asset['uri'])
    return objects


def run(args):
    baseline=args.baseline.resolve(); old=inventory(baseline)
    content=Path(read(baseline/'batch-report.json')['content'])
    selected={}
    for sha,obj in old.items():
        path=Path(obj['source']['path']); relative=path.relative_to(content).as_posix()
        projected=any(t.get('lwo2_block',{}).get('projection') in (0,2)
                      for m in obj['materials'] for t in m['textures'])
        control=relative.startswith('orange-juice-signage/') or relative=='collosus-concept-design/items/aircon/aircon.lwo'
        if not projected and not control: continue
        if hashlib.sha256(path.read_bytes()).hexdigest()!=sha: raise ValueError('Source changed: '+relative)
        selected[sha]=relative
    output=args.output.resolve()
    command=[sys.executable,'-X','utf8',str(ROOT/'tools/batch_convert.py'),
             '--content',str(content),'--output-root',str(output),'--converter',str(args.converter.resolve())]
    for path in sorted(selected.values()): command.extend(['--file',path])
    if not args.analyze_existing:
        output.mkdir(parents=True,exist_ok=False)
        context={'converter':str(args.converter.resolve()),
                 'converter_sha256':hashlib.sha256(args.converter.read_bytes()).hexdigest(),
                 'command':command}
        (output/'conversion-context.json').write_text(json.dumps(context,indent=2)+'\n',encoding='utf-8',newline='\n')
        with (output/'batch.log').open('wb') as log:
            result=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=600)
        if result.returncode not in (0,2): raise RuntimeError('Batch failed; see '+str(output/'batch.log'))
    batches=list(output.glob('batch-*')); assert len(batches)==1
    batch=batches[0]; new=inventory(batch); gained=[]; remaining=Counter(); atlas=[]
    total=Counter(); changes=[]
    for sha,path in selected.items():
        obj=new[sha]; original=old[sha]
        for mi,m in enumerate(obj['materials']):
            if 'derived_texture_mapping' in m: atlas.append({'source':path,'material':m['name']['text']})
            for ti,t in enumerate(m['textures']):
                previous=original['materials'][mi]['textures'][ti]
                if t.get('lwo2_block',{}).get('projection') in (0,2):
                    total['planar' if t['lwo2_block']['projection']==0 else 'spherical']+=1
                    total[t['export_status']]+=1
                    if t['issue']:remaining[t['issue']]+=1
                if t['export_status']!=previous['export_status']:
                    change=dict(source=path,material=m['name']['text'],texture=ti,
                        before=previous['export_status'],after=t['export_status'],issue=t['issue'])
                    changes.append(change)
                    if t['export_status']=='approximated':gained.append(change)
    context_path=output/'conversion-context.json'
    context=read(context_path) if context_path.is_file() else {}
    report={'baseline':str(baseline),'batch':str(batch),'converter_sha256':context.get('converter_sha256'),
            'batch_counts':read(batch/'batch-report.json')['counts'],'source_objects_verified':len(selected),
            'lwo2_projection_blocks':dict(total),'newly_bound_blocks':len(gained),
            'objects_with_new_bindings':len({c['source'] for c in gained}),
            'lost_bindings':[c for c in changes if c['after']!='approximated'],
            'remaining_lwo2_projection_issues':dict(remaining),'wrap_atlases':atlas,'binding_changes':changes,
            'scope':'Selected native files SHA-verified against archived batch. Standalone conversion; no inferred missing images, no scene-space baking.'}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('binding_changes','wrap_atlases')},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--converter',type=Path,default=ROOT/'bin/win64/lwconvert.exe')
    parser.add_argument('--analyze-existing',action='store_true',help='Only compare the completed batch already in --output')
    run(parser.parse_args())
