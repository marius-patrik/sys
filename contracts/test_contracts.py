"""Reference-only validation of selected Living Intelligence 0.27 seed contracts.

This is intentionally *not* a production scheduler, database or graph engine.
"""
import copy
import json
from pathlib import Path

HERE = Path(__file__).parent
policy = json.loads((HERE / 'attention.seed.json').read_text())
registry = json.loads((HERE / 'capabilities.seed.json').read_text())
example = json.loads((HERE / 'diagnose.graph.json').read_text())
capabilities = {c['id']: c for c in registry['capabilities']}


def attend(kind, features=None, *, duplicate=False, classify_worthwhile=False, budget_ok=True):
    if kind in policy['exempt_kinds']:
        return 'activate'
    if duplicate:
        return 'ignore'
    features = features or {}
    if set(features) != set(policy['weights']):
        raise ValueError('All recorded attention features are required for optional events')
    if not all(isinstance(v, (float, int)) and 0 <= v <= 1 for v in features.values()):
        raise ValueError('Feature out of range')
    score = sum(float(features[k]) * v for k, v in policy['weights'].items())
    if score >= policy['thresholds']['activate'] - 1e-10:
        return 'activate'
    if score >= policy['thresholds']['classify_or_defer'] - 1e-10:
        return 'classify' if classify_worthwhile and budget_ok else 'defer'
    return 'ignore'


def validate_graph(graph, *, grants, allowed_effects):
    """Closed, simple seed schema only. Reject all unknown/unsupported contract cases."""
    if graph.get('schemaVersion') != 1:
        raise ValueError('unsupported graph schema')
    nodes = graph['nodes']
    ids = [n['id'] for n in nodes]
    if len(ids) != len(set(ids)) or not ids:
        raise ValueError('duplicate/empty node ids')
    index = {n['id']: n for n in nodes}
    for n in nodes:
        if n['capability'] not in capabilities:
            raise ValueError('unknown capability')
        c = capabilities[n['capability']]
        if not set(c['grants']).issubset(grants):
            raise PermissionError('required grant not present')
        if c['effect'] not in allowed_effects:
            raise PermissionError('effect not permitted')
        if set(n['inputs']) != set(c['inputs']):
            raise ValueError('input ports must match capability exactly')
        for port, arg in n['inputs'].items():
            t = c['inputs'][port]
            if 'input' in arg:
                if graph['inputs'].get(arg['input']) != t:
                    raise TypeError('graph input type mismatch')
            elif 'node' in arg:
                origin = index.get(arg['node'])
                if origin is None:
                    raise ValueError('unknown source node')
                out_t = capabilities[origin['capability']]['outputs'].get(arg.get('port'))
                if out_t != t:
                    raise TypeError('node port type mismatch')
                if not any(e['from'] == arg['node'] and e['to'] == n['id'] and e['port'] == port for e in graph['edges']):
                    raise ValueError('missing typed dependency edge')
            else:
                raise ValueError('unknown binding form')
    indegree = {n: 0 for n in ids}
    adjacency = {n: set() for n in ids}
    for e in graph['edges']:
        if e['from'] not in index or e['to'] not in index:
            raise ValueError('edge references missing node')
        if not any(arg.get('node') == e['from'] and port == e['port'] for port, arg in index[e['to']]['inputs'].items()):
            raise ValueError('unbound/mislabelled dependency edge')
        if e['to'] not in adjacency[e['from']]:
            adjacency[e['from']].add(e['to'])
            indegree[e['to']] += 1
    frontier = [n for n in ids if indegree[n] == 0]
    seen = 0
    while frontier:
        n = frontier.pop()
        seen += 1
        for child in adjacency[n]:
            indegree[child] -= 1
            if indegree[child] == 0:
                frontier.append(child)
    if seen != len(ids):
        raise ValueError('cycles not allowed in one graph revision')
    for output in graph['outputs'].values():
        if output['node'] not in index or output['port'] not in capabilities[index[output['node']]['capability']]['outputs']:
            raise ValueError('invalid output reference')
    return True


def match_score(features):
    """Seed rank for hard-filtered candidates only; not a replacement for filtering."""
    weights = {'intent_similarity': .45, 'output_coverage': .20,
               'context_fit': .15, 'observed_success': .10, 'budget_fit': .10}
    if set(features) != set(weights) or any(not 0 <= v <= 1 for v in features.values()):
        raise ValueError('invalid match features')
    score = sum(weights[k] * v for k, v in features.items())
    return 'reuse' if score >= .82 - 1e-10 else 'adapt' if score >= .65 - 1e-10 else 'compose'


def join_decision(mode, statuses, k=None):
    """Seed join outcomes; branch-ineligible predecessors must be pre-marked skipped."""
    allowed = {'completed', 'failed', 'skipped', 'waiting'}
    if not statuses or not set(statuses).issubset(allowed):
        raise ValueError('bad predecessor states')
    completed = statuses.count('completed')
    waiting = statuses.count('waiting')
    if mode == 'all':
        if 'failed' in statuses:
            return 'failed'
        return 'ready' if not waiting else 'waiting'
    if mode == 'any':
        return 'ready' if completed else 'waiting' if waiting else 'failed'
    if mode == 'quorum':
        if not isinstance(k, int) or not 1 <= k <= len(statuses):
            raise ValueError('bad quorum')
        return 'ready' if completed >= k else 'failed' if completed + waiting < k else 'waiting'
    raise ValueError('unknown join')


def temporal_conflict(existing, candidate, single_valued=True):
    """Minimal seed evidence classification; no destructive claim merging."""
    if existing['subject'] != candidate['subject'] or existing['predicate'] != candidate['predicate']:
        return 'independent'
    a, b = existing['valid'], candidate['valid']  # half-open [start,end)
    overlap = a[0] < b[1] and b[0] < a[1]
    if existing['value'] == candidate['value'] and overlap:
        return 'support'
    if overlap and single_valued:
        return 'contradiction'
    return 'independent'


def expect_fails(fn, error):
    try:
        fn()
    except error:
        return
    raise AssertionError(f'expected {error.__name__}')


def test_all():
    # Exact weighted boundaries: all features equal the desired score.
    for val, result in [(0, 'ignore'), (0.349, 'ignore'), (0.35, 'defer'), (0.699, 'defer'), (0.7, 'activate'), (1, 'activate')]:
        features = {k: val for k in policy['weights']}
        assert attend('tool.observed', features) == result, val
    assert attend('surface.input') == 'activate'
    assert attend('tool.observed', {k: 1 for k in policy['weights']}, duplicate=True) == 'ignore'
    middle = {k: 0.5 for k in policy['weights']}
    assert attend('tool.observed', middle, classify_worthwhile=True) == 'classify'
    assert attend('tool.observed', middle, classify_worthwhile=True, budget_ok=False) == 'defer'
    expect_fails(lambda: attend('tool.observed', {'novelty': 0.5}), ValueError)
    assert validate_graph(example, grants={'repository.read', 'model.use'}, allowed_effects={'read', 'inference'})
    expect_fails(lambda: validate_graph(example, grants={'repository.read'}, allowed_effects={'read', 'inference'}), PermissionError)
    expect_fails(lambda: validate_graph(example, grants={'repository.read', 'model.use'}, allowed_effects={'read'}), PermissionError)
    # A *real* typed cycle, not merely a syntactically invalid extra edge.
    cycle = {
        'schemaVersion': 1, 'inputs': {},
        'nodes': [
            {'id': 'a', 'capability': 'text.echo', 'inputs': {'value': {'node': 'b', 'port': 'value'}}},
            {'id': 'b', 'capability': 'text.echo', 'inputs': {'value': {'node': 'a', 'port': 'value'}}}
        ],
        'edges': [
            {'from': 'a', 'to': 'b', 'port': 'value'},
            {'from': 'b', 'to': 'a', 'port': 'value'}
        ],
        'outputs': {'text': {'node': 'a', 'port': 'value'}}
    }
    expect_fails(lambda: validate_graph(cycle, grants=set(), allowed_effects={'read'}), ValueError)
    bad_port = copy.deepcopy(example)
    bad_port['nodes'][1]['inputs']['observations']['port'] = 'no-such-port'
    expect_fails(lambda: validate_graph(bad_port, grants={'repository.read','model.use'}, allowed_effects={'read','inference'}), TypeError)
    unknown = copy.deepcopy(example)
    unknown['nodes'][1]['capability'] = 'not.registered'
    expect_fails(lambda: validate_graph(unknown, grants={'repository.read','model.use'}, allowed_effects={'read','inference'}), ValueError)
    for score, result in [(0.5, 'compose'), (0.65, 'adapt'), (0.819, 'adapt'), (0.82, 'reuse')]:
        assert match_score({k: score for k in ['intent_similarity','output_coverage','context_fit','observed_success','budget_fit']}) == result
    assert join_decision('all', ['completed', 'skipped']) == 'ready'
    assert join_decision('all', ['completed', 'failed']) == 'failed'
    assert join_decision('any', ['failed', 'completed']) == 'ready'
    assert join_decision('any', ['failed', 'skipped']) == 'failed'
    assert join_decision('quorum', ['completed', 'waiting', 'failed'], 2) == 'waiting'
    assert join_decision('quorum', ['completed', 'failed', 'failed'], 2) == 'failed'
    original = {'subject':'pkg-x', 'predicate':'version', 'value':'1', 'valid':[1,5]}
    changed = {'subject':'pkg-x', 'predicate':'version', 'value':'2', 'valid':[3,8]}
    assert temporal_conflict(original, changed) == 'contradiction'
    assert temporal_conflict(original, changed, single_valued=False) == 'independent'
    assert temporal_conflict(original, dict(changed, value='1')) == 'support'
    print('PASS: seed-contract cases for attention, matching, typed DAGs, joins, and evidence conflicts')

if __name__ == '__main__':
    test_all()
