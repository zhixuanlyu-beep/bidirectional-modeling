"""Acceptance gates for reduced work without weakened proof semantics."""
import json
import subprocess
import sys
import unittest
from dataclasses import replace
from itertools import product
from unittest.mock import patch

from bidirectional_modeling import (
    Context, EquivalenceSpec, FiniteStateModel, MacroSpec, ModelMetrics, ScenarioKey,
    SearchProtocol, SearchExperiment, SearchObservation, SearchWorkBudget, SearchSession,
    ModelSearchCase, ModelSearchCandidate, LazyExecutableSearch, ExperimentHypothesisSearch,
    LowerSubstituteQuery, QueryStatus, UndefinedTransition,
)
from bidirectional_modeling.extensions.boolean import (
    BooleanExpression, BooleanLanguage, find_boolean_substitute, verify_boolean_substitute,
    enumerate_boolean_language,
)
from bidirectional_modeling.extensions.gluing import (
    GluingProblem, GluingReport, LocalDescription, verify_gluing_report, solve_gluing,
)
from bidirectional_modeling._exploration import explore_reachable, Edge
from bidirectional_modeling.refinement import ClosureAnalyzer
from bidirectional_modeling.residual import ResidualQuotientAnalyzer
from bidirectional_modeling.search_examples import conflict_search_scenario
from test_context_network import additive_protocol, identity_transition


class SimplificationTests(unittest.TestCase):
    def test_streaming_finds_first_candidate_under_small_budget(self):
        x = BooleanExpression(('var', 'x'))
        language = BooleanLanguage(('x', 'z'), max_nodes=8)
        inputs = ({'x': False, 'z': False}, {'x': True, 'z': False})
        budget = SearchWorkBudget(20)
        result = find_boolean_substitute(x, language, inputs, budget=budget)
        self.assertEqual(result.status, 'found')
        self.assertLess(budget.work.total, 20)
        self.assertFalse(enumerate_boolean_language(language, budget=SearchWorkBudget(20)).complete)
        # A different valid witness is a proof too; no canonical search order needed.
        alternate = replace(result, witness=BooleanExpression(('not', ('not', x.tree))))
        with patch('bidirectional_modeling.extensions.boolean.find_boolean_substitute', side_effect=AssertionError('must not search')):
            self.assertEqual(verify_boolean_substitute(x, language, inputs, alternate, budget=SearchWorkBudget(5)), 'valid')
            self.assertEqual(verify_boolean_substitute(x, language, inputs,
                replace(result, witness=BooleanExpression(('false',)))), 'invalid')

    def test_screening_growth_is_linear_and_early_failure_stays_cheap(self):
        for count in (1, 2, 4, 5, 8):
            names = tuple(str(i) for i in range(count))
            protocol = SearchProtocol('binary', 'code', tuple(SearchExperiment(n, n) for n in names),
                                      (('0',)*count, ('1',)*count))
            expression = BooleanExpression(('var', 'x'))
            c = ModelSearchCandidate(expression.to_model('zero'), BooleanLanguage(('x',)).description(expression))
            cases = tuple(ModelSearchCase(n, Context(environment={'x': False}), ScenarioKey('s', 'baseline'), 'y') for n in names)
            def lazy():
                return LazyExecutableSearch(protocol, (c,), cases, target='out', world_answers=('zero', 'one'))
            evidence = tuple(SearchObservation(n, '0', 'lab') for n in names)
            good = lazy().screen_evidence(evidence)
            self.assertEqual(good.matching_evidence, ('zero',))
            self.assertLessEqual(good.simulations_used, 6*count)
            if count == 4:
                self.assertEqual(good.simulations_used, 14)
            bad = lazy().screen_evidence(tuple(replace(o, response='1') for o in evidence))
            self.assertEqual(bad.excluded, ('zero',))
            self.assertEqual(bad.simulations_used, 2)

    def test_one_relation_preparation_for_multiple_transported_certificates(self):
        p, q = additive_protocol(), additive_protocol(scope='new')
        a, b = ExperimentHypothesisSearch(p, (), 'i'), ExperimentHypothesisSearch(q, (), 'i')
        data = tuple(SearchObservation(n, r, 'lab') for n, r in (('10', '0'), ('01', '0'), ('11', '1')))
        c = a.learn_conflict(('additive',), data)
        import bidirectional_modeling.context_network as module
        with patch.object(module, '_prepare_context_transition', wraps=module._prepare_context_transition) as check:
            result = SearchSession(a, data, (c, c)).migrate_context(b, identity_transition(p, q), data, tuple(zip(data, data)))
            self.assertEqual(result.status, 'completed')
            self.assertEqual(check.call_count, 1)
        self.assertTrue(all(b.validates_conflict(proof, data) for proof in result.session.certificates))

    def test_gluing_found_witness_is_verified_without_search(self):
        domains = tuple(('x%d' % i, ('0', '1')) for i in range(20))
        problem = GluingProblem(domains, (LocalDescription('first', ('x0',), (('0',), ('1',))),))
        receipt = GluingReport(problem.fingerprint, True, 'found', 'global_assignment_found', ('1',)*20)
        with patch('bidirectional_modeling.extensions.gluing.solve_gluing', side_effect=AssertionError('must not search')):
            self.assertEqual(verify_gluing_report(problem, receipt, budget=SearchWorkBudget(30)), 'valid')
        constrained = replace(problem, global_assignments=(('0',)*20,))
        self.assertEqual(verify_gluing_report(constrained, replace(receipt, problem_fingerprint=constrained.fingerprint)), 'invalid')

    def test_irreducibility_legacy_api_delegates_and_preserves_optional_results(self):
        search, _ = conflict_search_scenario()
        with patch.object(ExperimentHypothesisSearch, 'query', wraps=None) as query:
            query.return_value = type('Result', (), dict(status=QueryStatus.ABSENT))()
            self.assertTrue(search.irreducible_against('xz', ('x', 'z')))
            self.assertIsInstance(query.call_args.args[0], LowerSubstituteQuery)
        self.assertFalse(search.irreducible_against('x', ('x', 'x')))
        self.assertIsNone(search.irreducible_against('x', ()))

    def test_core_import_does_not_load_optional_domains_or_benchmarks(self):
        code = """import sys, json
import bidirectional_modeling as m
print(json.dumps({'optional': [k for k in sys.modules if k.startswith('bidirectional_modeling.extensions') or k.endswith('.search_benchmark')], 'exports': m.__all__}))
"""
        result = json.loads(subprocess.check_output([sys.executable, '-c', code], text=True))
        self.assertEqual(result['optional'], [])
        self.assertNotIn('BooleanExpression', result['exports'])
        self.assertNotIn('ContextNetwork', result['exports'])
        import bidirectional_modeling as package
        with self.assertWarns(DeprecationWarning):
            # Other test imports may have already resolved this compatibility name.
            package.__dict__.pop('BooleanExpression', None)
            self.assertIs(package.BooleanExpression, BooleanExpression)
        from bidirectional_modeling.boolean_reconstruction import BooleanExpression as old
        self.assertIs(old, BooleanExpression)
        with self.assertRaises(AttributeError):
            package.not_a_public_api

    def test_shared_explorer_preserves_limits_partiality_and_callback_errors(self):
        def transition(state, action, context):
            if action == 'disabled':
                raise UndefinedTransition('disabled')
            if action == 'crash':
                raise RuntimeError('not partial')
            return {'n': 1-state['n']} if action == 'flip' else state
        model = FiniteStateModel('finite', {'a': {'n': 0}, 'copy': {'n': 0}}, ('a', 'copy'),
            ('flip', 'disabled'), transition, lambda s, c: {'n': s['n']}, ModelMetrics(0, 0, 0))
        graph = explore_reachable(model, Context(), max_depth=None, max_states=5)
        self.assertTrue(graph.complete)
        self.assertEqual(len(graph.states), 2)
        self.assertEqual(graph.initial_indices, (('a', 0), ('copy', 0)))
        self.assertIs(graph.transitions[0, 'disabled'], Edge.UNDEFINED)
        for depth, limit in ((0, 5), (None, 1)):
            limited = explore_reachable(model, Context(), max_depth=depth, max_states=limit)
            self.assertFalse(limited.complete)
            self.assertTrue(any(edge is Edge.UNKNOWN for edge in limited.transitions.values()))
        broken = replace(model, actions=('crash',))
        graph = explore_reachable(broken, Context(), max_depth=None, max_states=5)
        self.assertFalse(graph.complete)
        self.assertTrue(graph.errors)
        spec = MacroSpec('n', ('n',), (), EquivalenceSpec(('n',)), horizon=3)
        self.assertTrue(ClosureAnalyzer().analyze(model, spec, Context()).closed)
        self.assertTrue(ResidualQuotientAnalyzer().analyze(model, spec.equivalence, Context()).minimal)
        for kwargs in ({'max_states': 0}, {'max_depth': -1}):
            with self.assertRaises(ValueError):
                explore_reachable(model, Context(), **dict({'max_states': 5, 'max_depth': None}, **kwargs))
