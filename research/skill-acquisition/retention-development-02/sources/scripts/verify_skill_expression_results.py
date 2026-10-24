"""Independently rescore retained executable expression outcomes and protocol gates.

This verifier imports neither the learner nor the evaluator. It reconstructs
source targets, generated corruption inputs, actual typed-program outputs,
selected receipt checks, all resource arithmetic, coverage and paired gates.
Search enumeration and CPU measurements are recorded process evidence; this is
not independent witnessing or a cryptographic proof of physical execution.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from decimal import Decimal, InvalidOperation, localcontext
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
from statistics import mean, stdev
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def serial(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def commit(value):
    return hashlib.sha256(serial(value).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number_text(value):
    number = Decimal(str(value))
    require(number.is_finite(), 'Nonfinite source value')
    if number == 0:
        return '0'
    rendered = format(number, 'f')
    return rendered.rstrip('0').rstrip('.') if '.' in rendered else rendered


def encoded(text, kind):
    if kind == 'raw': return text
    if kind == 'prefix100': return 'value=' + number_text(Decimal(text) * 100)
    if kind == 'suffix1000': return number_text(Decimal(text) * 1000) + ' units'
    if kind == 'percent100': return number_text(Decimal(text) * 100) + '%'
    if kind == 'n1000': return 'n=' + number_text(Decimal(text) * 1000)
    raise ValueError('Unknown generated field encoding')


def source_target(row, recipe):
    values = [Decimal(row[field]) for field in ('casual', 'registered', 'temp', 'hum')]
    indices = recipe['indices']
    if recipe['operation'] == 'field':
        value = values[indices[0]]
    else:
        a, b = (values[index] for index in indices)
        name = recipe['operation']
        require(name in ('add', 'subtract', 'multiply'), 'Unknown target operation')
        value = a+b if name == 'add' else a-b if name == 'subtract' else a*b
    if recipe['claim'] == 'observed-cnt identity':
        require(value == Decimal(row['cnt']), 'Real count identity failed')
    return number_text(value)


def generated_input(row, recipe):
    return json.dumps([encoded(number_text(row[field]), encoding)
                       for field, encoding in zip(('casual','registered','temp','hum'),recipe['encodings'],strict=True)],separators=(',',':'))


def run(program, value, config):
    require(type(program) is dict and set(program) == {'version', 'expr'}, 'Malformed expression envelope')
    require(program['version'] == config['version'], 'Expression version mismatch')
    whitelist = {serial(operation) for operation in config['operations']}
    work = 0
    count = 0
    def compute(tree, depth):
        nonlocal work, count
        count += 1
        require(type(tree) is dict and count <= config['max_ops'] and depth <= 8, 'Expression AST bounds exceeded')
        op = tree.get('op')
        if op == 'field':
            require(serial(tree) in whitelist, 'Unregistered field leaf')
            operands = [value]
        elif op in ('add','subtract','multiply'):
            require(set(tree) == {'op','left','right'} and serial({'op':op}) in whitelist, 'Malformed binary expression')
            operands = [compute(tree['left'],depth+1),compute(tree['right'],depth+1)]
        else:
            require('arg' in tree and serial({k:v for k,v in tree.items() if k!='arg'}) in whitelist, 'Unregistered unary expression')
            operands = [compute(tree['arg'],depth+1)]
        work += 1 + sum(max(1,(len(str(operand).encode())+31)//32) for operand in operands)
        try:
            if op=='field':
                fields=json.loads(value)
                require(type(fields) is list and len(fields)==4 and all(type(field) is str for field in fields),'Input must contain four string fields')
                result=fields[tree['index']]
            elif op in ('add','subtract','multiply'):
                require(all(type(x) is Decimal for x in operands),'Binary operands must be numeric')
                a,b=operands
                with localcontext() as arithmetic:
                    arithmetic.prec=64
                    result=a+b if op=='add' else a-b if op=='subtract' else a*b
            else:
                x=operands[0]
                require(type(x) is (Decimal if op in ('scale','format') else str),'Expression input type mismatch')
                if op=='strip': result=x.strip()
                elif op=='prefix':
                    require(x.startswith(tree['value']),'Prefix absent')
                    result=x[len(tree['value']):]
                elif op=='suffix':
                    require(x.endswith(tree['value']),'Suffix absent')
                    result=x[:-len(tree['value'])]
                elif op=='decimal': result=Decimal(x)
                elif op=='scale':
                    with localcontext() as arithmetic:
                        arithmetic.prec=64
                        result=x*tree['numerator']/tree['denominator']
                elif op=='format': result=number_text(x)
                else: raise ValueError('Unknown expression operation')
            if type(result) is Decimal:
                require(result.is_finite() and abs(result)<=Decimal('1e30') and abs(result.as_tuple().exponent)<=64,'Numeric bounds exceeded')
            else:
                require(type(result) is str and len(result.encode())<=config['max_input_bytes'],'Text bounds exceeded')
            return result
        except (ValueError,TypeError,IndexError,KeyError,InvalidOperation):
            raise ArithmeticError('Expression operation failed') from None
    try:
        output=compute(program['expr'],1)
        require(type(output) is str,'Expression final type is not text')
        return output,work
    except ArithmeticError:
        return None,work


def outcome(program, inputs, expected, config):
    if program is None:
        return {'passed': False, 'correct': 0, 'rows': len(expected), 'outputs': [None] * len(expected),
                'tool_units': 0, 'first_counterexample_index': 0, 'actual_sha256': commit([None] * len(expected))}
    values, work = [], 0
    for source in inputs:
        output, charged = run(program, source, config)
        values.append(output)
        work += charged
    bad = [index for index, (a, b) in enumerate(zip(values, expected, strict=True)) if a != b]
    return {'passed': not bad, 'correct': len(expected) - len(bad), 'rows': len(expected), 'outputs': values,
            'tool_units': work, 'first_counterexample_index': bad[0] if bad else None, 'actual_sha256': commit(values)}


def paired(values):
    n = len(values)
    average = mean(values)
    if n < 2:
        return {'mean': average, 'lower95': None, 'upper95': None, 'independent_streams': n}
    critical = {2: 12.706204736432095, 3: 4.302652729911275, 12: 2.200985160091639}[n]
    radius = critical * stdev(values) / math.sqrt(n)
    return {'mean': average, 'lower95': average - radius, 'upper95': average + radius, 'independent_streams': n,
            'method': 'paired independent-stream Student t interval'}


def summaries(trials, branches, selected_method, summary_version=1):
    aggregates = {}
    for branch in branches:
        rows = [trial for trial in trials if trial['branch'] == branch]
        aggregates[branch] = {'transfer_success': mean(t['transfer_success'] for t in rows),
                              'acquisition_success': mean(t['acquisition_success'] for t in rows),
                              'early_acquisition_success': mean(t['early_acquisition_success'] for t in rows),
                              'late_acquisition_success': mean(t['late_acquisition_success'] for t in rows),
                              'mean_total_work': mean(t['total_work'] for t in rows),
                              'mean_cpu_seconds': mean(t['cpu_seconds'] for t in rows)}
    if summary_version >= 2:
        for branch in branches:
            correct = aggregates[branch]['transfer_success'] * 16
            aggregates[branch]['total_cpu_per_correct_transfer_task'] = aggregates[branch]['mean_cpu_seconds'] / correct if correct else None
            aggregates[branch]['total_work_per_correct_transfer_task'] = aggregates[branch]['mean_total_work'] / correct if correct else None
    def compare(left_branch, right_branch):
        left = {trial['seed']: trial for trial in trials if trial['branch'] == left_branch}
        right = {trial['seed']: trial for trial in trials if trial['branch'] == right_branch}
        return paired([left[seed]['transfer_success'] - right[seed]['transfer_success'] for seed in sorted(left)])
    simple=max(('fresh','factored_fresh') if 'factored_fresh' in branches else ('fresh',),key=lambda method:(aggregates[method]['transfer_success'],-aggregates[method]['mean_total_work']))
    competitive, learning = compare(selected_method,simple), compare(selected_method,'no_acquisition')
    specificity = compare('contextual','frequency')
    ratio = aggregates[selected_method]['mean_total_work'] / max(1,aggregates[simple]['mean_total_work'])
    cpu_ratio = aggregates[selected_method]['mean_cpu_seconds'] / max(1e-12,aggregates[simple]['mean_cpu_seconds'])
    output={'branches': aggregates, 'selected_retention_method':selected_method,
            'selected_minus_fresh':competitive,'selected_minus_no_acquisition':learning,
            'contextual_minus_frequency':specificity,
            'selected_total_work_ratio_vs_fresh':ratio,'selected_cpu_ratio_vs_fresh':cpu_ratio,
            'gates':{'practical_benefit':competitive['mean']>=0.05 and competitive['lower95'] is not None and competitive['lower95']>0 and ratio<=1.1,
                     'acquisition_transfer':learning['mean']>=0.05 and learning['lower95'] is not None and learning['lower95']>0,
                     'cpu_nonregression':cpu_ratio<=1.1 and competitive['mean']>=0,
                     'contextual_specificity':specificity['mean']>=0.05 and specificity['lower95'] is not None and specificity['lower95']>0,
                     'neural_specificity':'not_tested'}}
    if 'factored_fresh' in branches:
        output['strongest_stateless_method']=simple
        output['selected_minus_strongest_stateless']=output.pop('selected_minus_fresh')
        output['selected_total_work_ratio_vs_stateless']=output.pop('selected_total_work_ratio_vs_fresh')
        output['selected_cpu_ratio_vs_stateless']=output.pop('selected_cpu_ratio_vs_fresh')
    return output


def verify(directory, *, check_sources=True):
    protocol = json.loads((directory / 'protocol.json').read_text())
    result = json.loads((directory / 'result.json').read_text())
    require(commit(protocol) == result['protocol_sha256'], 'Protocol commitment changed')
    if check_sources:
        for filename, expected in protocol['source_hashes'].items():
            snapshot = directory / 'sources' / filename if isinstance(directory,Path) else None
            source = snapshot if snapshot is not None and snapshot.exists() else ROOT / filename
            require(hashlib.sha256(source.read_bytes()).hexdigest() == expected, 'Evaluated source changed: ' + filename)
    require(result['source_hashes'] == protocol['source_hashes'], 'Result source identities differ')
    raw = (ROOT / 'research/intervention-memory/data/bike-sharing-original.zip').read_bytes()
    require(hashlib.sha256(raw).hexdigest() == protocol['dataset']['archive_sha256'], 'Source archive changed')
    csv_raw = zipfile.ZipFile(io.BytesIO(raw)).read('hour.csv')
    require(hashlib.sha256(csv_raw).hexdigest() == protocol['dataset']['csv_sha256'], 'Source CSV changed')
    data = {int(row['instant']): row for row in csv.DictReader(io.StringIO(csv_raw.decode()))}
    require(len(data) == protocol['dataset']['rows'], 'Source row count differs')
    trace_raw = (directory / 'traces.jsonl.gz').read_bytes()
    require(hashlib.sha256(trace_raw).hexdigest() == result['trace_sha256'], 'Trace bytes changed')
    records = [json.loads(line) for line in gzip.decompress(trace_raw).decode().splitlines()]
    branches, seeds = protocol['branches'], result['seeds']
    require(seeds in (protocol['development_seeds'], protocol['reserved_seeds']), 'Unregistered seed set')
    expected_count = len(seeds) * len(branches) * (protocol['acquisition_tasks'] + protocol['transfer_tasks'])
    require(len(records) == expected_count, 'Missing/extra task records')
    seen, task_by_key, streams = set(), {}, defaultdict(list)
    for record in records:
        task, branch, seed = record['task'], record['branch'], record['seed']
        require(seed in seeds and branch in branches, 'Unregistered trial')
        phase, step = task['phase'], task['step']
        require(phase in ('acquisition', 'transfer'), 'Unknown phase')
        structures = protocol['acquisition_recipes'] if phase == 'acquisition' else protocol['unseen_transfer_recipes']
        require(task['recipe'] == structures[step % len(structures)], 'Task expression recipe schedule changed')
        task_terms = {key: value for key, value in task.items() if key != 'id'}
        require(commit(task_terms) == task['id'], 'Task identity changed')
        identity = seed, branch, phase, step
        require(identity not in seen, 'Repeated task')
        seen.add(identity)
        common = seed, phase, step
        require(common not in task_by_key or task_by_key[common] == task, 'Branches received different tasks')
        task_by_key[common] = task
        require(len(task['inputs']) == len(task['outputs']) == len(task['row_ids']) == 24, 'Task row count changed')
        for row_id, value, expected in zip(task['row_ids'], task['inputs'], task['outputs'], strict=True):
            source = data[row_id]
            require(source['yr'] == ('0' if phase == 'acquisition' else '1'), 'Chronological split leaked')
            target = source_target(source, task['recipe'])
            require(target == expected and generated_input(source, task['recipe']) == value, 'Input/target differs from source observation')
        require(len(set(task['outputs'][:4])) == 4, 'Visible examples are not identifiable')
        visible_in, visible_out = task['inputs'][:4], task['outputs'][:4]
        solver_work = buyer_work = 0
        last_program = None
        for index, attempt in enumerate(record['attempts']):
            require(index < protocol['max_cegis_rounds'] and attempt['round'] == index, 'Too many or reordered CEGIS rounds')
            require(attempt['visible_inputs'] == visible_in and attempt['visible_outputs'] == visible_out, 'Solver saw undeclared examples')
            report = attempt['result']
            meter = report['work']
            require(all(type(meter[key]) is int and meter[key] >= 0 for key in ('total', 'candidates', 'primitive_attempts', 'byte_blocks', 'errors', 'semantic_duplicates')), 'Invalid search meter')
            require(meter['total'] == meter['candidates'] + meter['primitive_attempts'] + meter['byte_blocks'], 'Search meter does not sum')
            solver_work += meter['total']
            require(solver_work <= protocol['solve_budget'], 'Shared task search budget exceeded')
            require(report['mode'] == protocol['modes'][branch], 'Branch search mode changed')
            last_program = report['program']
            require(report['solved_training'] == (last_program is not None), 'Solved flag does not match program')
            learn = phase == 'acquisition' and branch not in ('fresh', 'factored_fresh', 'no_acquisition') and not (branch == 'frozen' and step >= protocol['acquisition_tasks']//2)
            require(attempt['admission_allowed'] == learn, 'Frozen/feedback-control learning leaked')
            if last_program is None:
                require(attempt['validation'] is None and attempt['receipt'] is None, 'Unsolved proposal received result')
                continue
            require(commit(last_program) == report['program_sha256'], 'Program commitment changed')
            require(outcome(last_program, visible_in, visible_out, protocol['config'])['passed'], 'Claimed program does not fit visible examples')
            checked = outcome(last_program, task['inputs'][4:12], task['outputs'][4:12], protocol['config'])
            require(checked == attempt['validation'], 'Buyer execution result changed')
            buyer_work += checked['tool_units']
            receipt = attempt['receipt']
            require(receipt['accepted'] == checked['passed'] and receipt['correct_rows'] == checked['correct'] and receipt['total_rows'] == 8 and receipt['work'] == checked['tool_units'], 'Receipt utility/work mismatch')
            require(receipt['program_sha256'] == report['program_sha256'] and receipt['proposal_id'] == report['proposal_id'], 'Receipt origin mismatch')
            require(receipt['evaluation_id'] == commit({'task': task['id'], 'round': index, 'phase': phase}), 'Receipt evaluation changed')
            require(receipt['evidence_sha256'] == commit({'inputs': task['inputs'][4:12], 'outputs': task['outputs'][4:12], 'observed': checked['outputs']}), 'Receipt output evidence changed')
            if not checked['passed'] and phase == 'acquisition':
                offset = checked['first_counterexample_index']
                visible_in = visible_in + [task['inputs'][4 + offset]]
                visible_out = visible_out + [task['outputs'][4 + offset]]
            elif index + 1 < len(record['attempts']):
                raise ValueError('Unexpected CEGIS after success or heldout reveal')
        require(last_program == record['final_program'], 'Final program differs from selected execution')
        score = outcome(last_program, task['inputs'][12:], task['outputs'][12:], protocol['config'])
        require(score == record['score'], 'Scored executable outputs changed')
        require(record['solver_work'] == solver_work and record['buyer_work'] == buyer_work and record['scoring_work'] == score['tool_units'], 'Task accounting changed')
        require(record['total_work'] == solver_work + buyer_work + score['tool_units'], 'Total work omits execution')
        require(all(type(record[key]) in (int, float) and math.isfinite(record[key]) and record[key] >= 0 for key in ('cpu_seconds', 'wall_seconds')), 'Invalid measured duration')
        streams[seed, branch].append(record)
    # Prove no source row repeats within or between registered seed buckets.
    owner = {}
    for key, task in task_by_key.items():
        for row in task['row_ids']:
            require(row not in owner, 'Source row reused between tasks/seeds')
            owner[row] = key
    reconstructed = []
    for seed in seeds:
        for branch in branches:
            records = streams[seed, branch]
            acq = sorted((r for r in records if r['task']['phase'] == 'acquisition'), key=lambda r:r['task']['step'])
            transfer = sorted((r for r in records if r['task']['phase'] == 'transfer'), key=lambda r:r['task']['step'])
            require([r['task']['step'] for r in acq] == list(range(protocol['acquisition_tasks'])) and [r['task']['step'] for r in transfer] == list(range(protocol['transfer_tasks'])), 'Task coverage incomplete')
            reconstructed.append({'seed': seed, 'branch': branch, 'acquisition_tasks': len(acq), 'transfer_tasks': len(transfer),
                                  'acquisition_success': mean(r['score']['passed'] for r in acq),
                                  'early_acquisition_success': mean(r['score']['passed'] for r in acq[:len(acq)//2]),
                                  'late_acquisition_success': mean(r['score']['passed'] for r in acq[len(acq)//2:]),
                                  'transfer_success': mean(r['score']['passed'] for r in transfer),
                                  'total_work': sum(r['total_work'] for r in records),
                                  'cpu_seconds': sum(r['cpu_seconds'] for r in records)})
    require(len(result['trials']) == len(reconstructed), 'Trial count changed')
    for retained, calculated in zip(result['trials'], reconstructed, strict=True):
        require({key: retained[key] for key in calculated} == calculated, 'Retained trial arithmetic changed')
    summary = summaries(reconstructed, branches, protocol['selected_method'], protocol.get('summary_version',1))
    require({key:result['summary'][key] for key in summary} == summary, 'Summary or registered gate changed')
    require(result['trial_count'] == len(reconstructed), 'Result trial count differs')
    return {'schema':'nuria.skill-expression-acquisition.verification.v1', 'passed':True,
            'trace_records':expected_count, 'trials':len(reconstructed),
            'unique_tasks':len(task_by_key), 'unique_source_rows':len(owner), 'source_hashes_checked':check_sources,
            'gates':summary['gates'], 'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'scope':'Independent retained-outcome/coverage/accounting rescore; not independent physical execution witnessing, full search replay, or measured CPU attestation.'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('directory',type=Path)
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    result=verify(args.directory)
    if args.out:
        if args.out.exists():
            raise ValueError('Preserve earlier verification output')
        args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
