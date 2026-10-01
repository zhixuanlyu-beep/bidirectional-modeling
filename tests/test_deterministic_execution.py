"""Determinism across process hash seeds, including bounded execution."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from dataclasses import replace
import bidirectional_modeling

from bidirectional_modeling import (
    Context, ConstraintQuery, EquivalenceSpec, Experiment, Intervention,
    LowerSubstituteQuery, MacroAlternativeQuery, SearchHypothesis,
    SearchObservation, ResourceBudget,
)
from bidirectional_modeling._generation import CandidateStream
from bidirectional_modeling.core import requirement_semantic_signature
from bidirectional_modeling.evaluation import SatisfactionEvaluator
from bidirectional_modeling.interpretation import CatalogHypothesisGenerator
from bidirectional_modeling.realization import ParametricCandidateGenerator, RegistryGenerator
from bidirectional_modeling.search_adapter import ExecutableSearchAdapter
from bidirectional_modeling.structural import fingerprint_value, ordered_tuple, deterministic_repr
from test_search_integration import adapter_args
from test_interpretation_status import model, spec


class OrderedInputContracts(unittest.TestCase):
    def test_unordered_inputs_are_rejected_before_any_execution(self):
        p, candidates, cases = adapter_args()
        observation = SearchObservation('read', '0', 'lab')
        factories = (
            lambda: Experiment('e', 'ask', {'red', 'blue'}),
            lambda: Intervention('i', frozenset(('a', 'b'))),
            lambda: Context(scenario_manifest=frozenset()),
            lambda: EquivalenceSpec(frozenset(('x', 'y'))),
            lambda: replace(spec(), observables={'x', 'y'}),
            lambda: replace(model(), actions={'noop', 'other'}),
            lambda: replace(p, experiments=set(p.experiments)),
            lambda: replace(p, worlds=set(p.worlds)),
            lambda: replace(p, worlds=(frozenset(('0',)),)),
            lambda: SearchHypothesis('h', None, 'out', candidates[0].description, {'c'}),
            lambda: replace(candidates[0], commitments={'c'}),
            lambda: ConstraintQuery({'c'}),
            lambda: ConstraintQuery(evidence={observation}),
            lambda: MacroAlternativeQuery('out', {observation}),
            lambda: LowerSubstituteQuery('h', {'lower'}),
            lambda: RegistryGenerator({1, 2}),
            lambda: CatalogHypothesisGenerator({1, 2}),
            lambda: ExecutableSearchAdapter().prepare(p, {1, 2}, cases,
                        target='out', world_answers=('yes', 'no')),
        )
        for factory in factories:
            with self.subTest(factory=factories.index(factory)):
                with self.assertRaisesRegex(TypeError, 'unordered set'): factory()

    def test_parameter_values_are_snapshotted_without_changing_declared_order(self):
        values = ['blue', 'red']
        calls = []
        def factory(parameters, _spec, _context):
            calls.append(parameters)
            return parameters['x']
        generator = ParametricCandidateGenerator({'x': values}, factory)
        values.reverse()
        self.assertEqual(tuple(generator.generate(None, None, None)), ('blue', 'red'))
        with self.assertRaisesRegex(TypeError, 'unordered set'):
            ParametricCandidateGenerator({'x': {'blue', 'red'}}, factory)
        self.assertEqual(len(calls), 2)

    def test_set_generator_is_undecided_without_selecting_a_candidate(self):
        for items in ({1, 2}, {'a': 1, 'b': 2}.keys()):
            stream = CandidateStream(lambda: items, 1)
            self.assertEqual(tuple(stream), ())
            self.assertEqual(stream.inspected, 0)
            self.assertFalse(stream.complete)
            self.assertIn('unordered set', stream.diagnostics[0].reason)

    def test_unordered_simulation_cannot_certify_a_batch(self):
        class UnorderedModel:
            name = 'unordered'
            def simulate(self, context, horizon): return {0, 1}
        batch = SatisfactionEvaluator().collect(UnorderedModel(), Context(), 1,
                                                 ResourceBudget(max_simulations=7))
        self.assertFalse(batch.complete)
        self.assertEqual(batch.traces, ())
        self.assertEqual(batch.simulations_used, 7)  # callback's consumption is unknown
        self.assertTrue(any('unordered set' in d.detail for d in batch.diagnostics))

    def test_opaque_identity_never_enters_a_semantic_signature(self):
        with self.assertRaisesRegex(TypeError, 'deterministic semantic_signature'):
            requirement_semantic_signature(object())
        class UnorderedSignature:
            def semantic_signature(self): return {'a', 'b'}
        with self.assertRaisesRegex(TypeError, 'unordered set'):
            requirement_semantic_signature(UnorderedSignature())

    def test_semantic_sets_remain_canonical_and_ordered_iterators_remain_lazy(self):
        for values in ({'a', 'b'}, frozenset(('a', 'b')), {'a': 1}.keys()):
            with self.assertRaisesRegex(TypeError, 'unordered set'): ordered_tuple(values)
        self.assertEqual(fingerprint_value({'v': {'a', 'b'}}),
                         fingerprint_value({'v': set(('b', 'a'))}))
        self.assertEqual(ordered_tuple(iter(('b', 'a'))), ('b', 'a'))
        reads = []
        def source():
            for name in ('b', 'a'):
                reads.append(name)
                yield name
        stream = CandidateStream(source, 1)
        self.assertEqual(tuple(stream), ('b',))
        self.assertEqual(reads, ['b'])

    def test_mutated_model_order_is_rejected_at_execution_boundary(self):
        candidate = model()
        candidate.initial_states = set(candidate.initial_states)
        with self.assertRaisesRegex(TypeError, 'unordered set'):
            candidate.scenario_manifest(Context())
        candidate = model()
        candidate.actions = {'noop', 'other'}
        from bidirectional_modeling._exploration import explore_reachable
        with self.assertRaisesRegex(TypeError, 'unordered set'):
            explore_reachable(candidate, Context(), max_depth=1, max_states=2)

    def test_nested_semantic_values_have_readable_canonical_text(self):
        from enum import Enum
        class Color(Enum): RED = 'red'
        self.assertEqual(deterministic_repr({'z': frozenset(('b', 'a')), 'a': [{'d', 'c'}]}),
                         "{'a': [{'c', 'd'}], 'z': frozenset({'a', 'b'})}")
        self.assertEqual(deterministic_repr((Color.RED, set(), frozenset(), (1,))),
                         "(<Color.RED: 'red'>, set(), frozenset(), (1,))")
        with self.assertRaises(TypeError): deterministic_repr(object())


PAYLOAD = r'''
import json
from dataclasses import asdict
from bidirectional_modeling import ConstraintQuery, ExperimentHypothesisSearch, SearchWorkBudget
from bidirectional_modeling.search_examples import conflict_search_scenario
from bidirectional_modeling.search_queries import verify_query_result
from bidirectional_modeling.structural import fingerprint_value
from bidirectional_modeling.realization import ParametricCandidateGenerator
from bidirectional_modeling import Context, FieldRequirement, FiniteStateModel, ModelMetrics
from bidirectional_modeling.interpretation import ObservedEffectGenerator
from bidirectional_modeling.provenance import macro_spec_fingerprint
base, evidence = conflict_search_scenario()
rows = []
for backend in ('scan', 'indexed'):
    for limit in (0, 1, 3, 8, 20, 100, 1000):
        p = ExperimentHypothesisSearch(base.protocol, base.hypotheses, base.target, backend=backend)
        query = ConstraintQuery(evidence=evidence)
        receipt = p.query(query, budget=SearchWorkBudget(limit))
        rows.append((asdict(receipt), asdict(verify_query_result(p, query, receipt)),
                     asdict(p.search(evidence, budget=SearchWorkBudget(limit)))))
    p = ExperimentHypothesisSearch(base.protocol, base.hypotheses, base.target, backend=backend)
    rows.append(asdict(p.learn_conflict(('additive',), evidence)))
generator = ParametricCandidateGenerator({'x': ('blue', 'red')}, lambda p,s,c:p['x'])
model = FiniteStateModel('sets', {'s': {'color': {'red', 'blue'}}}, ('s',), ('noop',),
    lambda s,a,c:s, lambda s,c:dict(s), ModelMetrics(1,1,0))
traces = tuple(model.simulate(Context(), 1))
effects = tuple(ObservedEffectGenerator().generate_from_traces(traces))
labels = [(h.name, h.spec.name, macro_spec_fingerprint(h.spec)) for h in effects]
requirement = FieldRequirement('color', 'color', 'eq', {'red', 'blue'})
expected_text = requirement.evaluate(model, traces, Context()).expected
print(json.dumps(dict(rows=rows, candidates=list(generator.generate(None,None,None)),
    labels=labels, expected_text=expected_text,
    semantic_set=fingerprint_value({'v': {'red', 'blue'}})), sort_keys=True))
'''


class HashSeedContracts(unittest.TestCase):
    def test_bounded_reports_witnesses_and_certificates_match_across_processes(self):
        root = Path(__file__).resolve().parents[1]
        source = Path(bidirectional_modeling.__file__).resolve().parents[1]
        baseline = None
        for seed in ('1', '2', '7', '42', '99'):
            env = dict(os.environ, PYTHONPATH=str(source), PYTHONHASHSEED=seed,
                       PYTHONDONTWRITEBYTECODE='1')
            result = subprocess.run([sys.executable, '-c', PAYLOAD], env=env, cwd=root,
                                    text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            if baseline is None: baseline = payload
            else: self.assertEqual(payload, baseline, 'hash seed ' + seed)
