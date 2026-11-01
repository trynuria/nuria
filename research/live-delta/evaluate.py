"""Run a frozen, complete-data comparison in Nuria's existing OS sandbox."""

import argparse
import ast
import hashlib
import json
from pathlib import Path

from runtime import ComparisonPython

FORBIDDEN = {
    "open",
    "eval",
    "exec",
    "compile",
    "__import__",
    "globals",
    "locals",
    "vars",
    "getattr",
    "setattr",
    "delattr",
    "dir",
    "input",
    "breakpoint",
    "exit",
    "quit",
}


def audit(code):
    tree = ast.parse(code)
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            mods = (
                [a.name for a in n.names]
                if isinstance(n, ast.Import)
                else [n.module or ""]
            )
            if any(m not in {"json", "math", "collections", "functools"} for m in mods):
                raise ValueError("Unenrolled import")
        if isinstance(n, ast.Name) and (n.id in FORBIDDEN or n.id.startswith("__")):
            raise ValueError("Reflection or I/O is outside the candidate contract")
        if isinstance(n, ast.Attribute) and n.attr.startswith("__"):
            raise ValueError("Reflection is outside the candidate contract")
    if not any(
        isinstance(n, ast.FunctionDef) and n.name == "make_patch" for n in tree.body
    ):
        raise ValueError("Missing make_patch")


CHECKS = r"""
import copy,gzip,hashlib,json,random,statistics,time
from pathlib import Path
import jsonpatch,jsondiff
cases=json.loads(Path(CASE_PATH).read_text())
def dump(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def pack(x):return json.dumps(x,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def compact(patch):return [[{'add':0,'remove':1,'replace':2}[o['op']],o['path']]+([o['value']] if o['op']!='remove' else []) for o in patch]
def expand(patch):return [dict(op=('add','remove','replace')[o[0]],path=o[1],**({'value':o[2]} if o[0]!=1 else {})) for o in patch]
def same(a,b):return dump(a)==dump(b)
def validate(a,b):
 before_a=dump(a);before_b=dump(b)
 patch=make_patch(a,b)
 assert isinstance(patch,list) and all(isinstance(o,dict) and o.get('op') in ('add','remove','replace') for o in patch),'Invalid operation'
 result=jsonpatch.apply_patch(a,patch,in_place=False)
 assert same(result,b),'Reconstruction mismatch: old='+repr(a)[:300]+' new='+repr(b)[:300]+' result='+repr(result)[:300]
 assert dump(a)==before_a and dump(b)==before_b,'Inputs were mutated'
 return patch
rng=random.Random(SEED)
def value(depth=0):
 if depth>=3 or rng.random()<.45:return rng.choice([None,False,True,0,1,1.0,-2,3.125,'','é/雪~','a/b','~'])
 if rng.random()<.5:return [value(depth+1) for _ in range(rng.randrange(5))]
 return {rng.choice(['a','b','x/y','~','',str(i)]):value(depth+1) for i in range(rng.randrange(5))}
adversarial=[(1,True),(False,0),({'a':1},{'a':True}),([0],[False]),({'a/b':{'~':1}},{'a/b':{'~':2}}),({'':0},{}),([],{}),({},[]),([1,2,3],[0,1,3,4]),({'__proto__':1},{'__proto__':2})]
for _ in range(256):adversarial.append((value(),value()))
for a,b in adversarial:validate(a,b)
for case in cases:validate(case['old'],case['new'])
methods=('candidate','jsonpatch','jsonpatch_compact','jsondiff','full_gzip')
rows=[]
for case in cases:
 a,b=case['old'],case['new'];unchanged=same(a,b);samples={k:[] for k in methods};decodes={k:[] for k in methods};sizes={};correct={}
 for round_number in range(7):
  order=list(methods);random.Random(round_number+91).shuffle(order)
  for name in order:
   start=time.perf_counter_ns()
   if unchanged:wire=b''
   else:
    if name=='candidate':delta=compact(make_patch(a,b))
    elif name=='jsonpatch':delta=jsonpatch.make_patch(a,b).patch
    elif name=='jsonpatch_compact':
     native=jsonpatch.make_patch(a,b).patch
     # Move/copy operations are legitimate in the baseline, so retain native
     # format when needed; a tagged envelope distinguishes it at reconstruction.
     delta={'native':native} if any(o['op'] not in ('add','remove','replace') for o in native) else compact(native)
    elif name=='jsondiff':delta=jsondiff.diff(a,b,marshal=True)
    else:delta=b
    wire=gzip.compress(pack(delta),compresslevel=3,mtime=0)
   encoded=time.perf_counter_ns()
   if unchanged:result=a
   else:
    data=json.loads(gzip.decompress(wire))
    if name in ('candidate','jsonpatch_compact'):result=jsonpatch.apply_patch(a,data['native'] if isinstance(data,dict) else expand(data),in_place=False)
    elif name=='jsonpatch':result=jsonpatch.apply_patch(a,data,in_place=False)
    elif name=='jsondiff':result=jsondiff.patch(a,data,marshal=True)
    else:result=data
   decoded=time.perf_counter_ns()
   assert same(result,b),name+' failed exact reconstruction on '+case['id']
   if round_number:
    samples[name].append((encoded-start)/1e6);decodes[name].append((decoded-encoded)/1e6)
   sizes[name]=len(wire)
 rows.append({'id':case['id'],'unchanged':unchanged,'old_bytes':len(pack(a)),'new_bytes':len(pack(b)),'methods':{k:{'encode_ms':statistics.median(samples[k]),'decode_ms':statistics.median(decodes[k]),'end_to_end_cpu_ms':statistics.median([x+y for x,y in zip(samples[k],decodes[k])]),'wire_bytes':sizes[k]} for k in methods}})
totals={k:{metric:sum(r['methods'][k][metric] for r in rows) for metric in ('encode_ms','decode_ms','end_to_end_cpu_ms','wire_bytes')} for k in methods}
best_time=min(totals[k]['encode_ms'] for k in ('jsonpatch','jsonpatch_compact','jsondiff'));best_bytes=min(totals[k]['wire_bytes'] for k in ('jsonpatch','jsonpatch_compact','jsondiff'))
ratio_time=totals['candidate']['encode_ms']/best_time if best_time else 1
ratio_bytes=totals['candidate']['wire_bytes']/best_bytes if best_bytes else 1
report={'correct':True,'real_cases':len(cases),'adversarial_cases':len(adversarial),'rows':rows,'totals':totals,'encode_ratio_to_fastest_delta':ratio_time,'wire_ratio_to_smallest_delta':ratio_bytes,'gate_passed':ratio_time<=.70 and ratio_bytes<=1.10,'scope':'Real archived reports and observed snapshots; CPU is measured in one process with alternating order and includes encoding, gzip, decoding and patch application. Common input parsing and HTTP/network transport are excluded from this algorithm comparison and must be measured in deployment.','versions':{'jsonpatch':jsonpatch.__version__,'jsondiff':jsondiff.__version__}}
Path('report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='rows'}))
"""


def evaluate(root, code_path, group, label):
    code = Path(code_path).read_text()
    audit(code)
    corpus = Path(root) / group
    checks = CHECKS.replace("CASE_PATH", repr(str(corpus / "cases.json"))).replace(
        "SEED", "7021" if group == "development" else "919843"
    )
    executor = ComparisonPython(
        Path(root) / "executions",
        dependency_roots=("/opt/nuria/research-deps", str(corpus)),
        wall_seconds=60,
    )
    result = executor.run(code, checks, label=label)
    report_path = Path(root) / "executions" / label / "report.json"
    report = (
        json.loads(report_path.read_text())
        if report_path.exists()
        else {"correct": False, "execution": result}
    )
    report["code_sha256"] = hashlib.sha256(code.encode()).hexdigest()
    report["checks_sha256"] = result["checks_sha256"]
    report["group"] = group
    Path(root, f"{label}.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--group", choices=("development", "holdout"), default="development")
    p.add_argument("--label", required=True)
    a = p.parse_args()
    r = evaluate(a.root, a.code, a.group, a.label)
    print(json.dumps({k: v for k, v in r.items() if k != "rows"}))
