"""Independent semantic and operation-growth checks for review improvements."""
import unittest
from dataclasses import replace
from itertools import product
from unittest.mock import patch

from bidirectional_modeling import (ConstraintQuery, Context, DescriptionLength, ExperimentHypothesisSearch, MacroAlternativeQuery, QueryStatus, ScenarioKey, SearchExperiment, SearchHypothesis, SearchObservation, SearchProtocol, SearchWorkBudget)
from bidirectional_modeling.search_lazy import (LazyExecutableSearch)
from bidirectional_modeling.search_adapter import (ModelSearchCandidate, ModelSearchCase)
from bidirectional_modeling.search_queries import (verify_query_result)
from bidirectional_modeling.context_network import (
    ContextChange, ContextTransition, ModelingContext, _prepare_context_transition,
)
from bidirectional_modeling.extensions.boolean import (
    BooleanExpression, BooleanLanguage, enumerate_boolean_language,
    find_boolean_substitute, verify_boolean_substitute,
)
from bidirectional_modeling.structural import fingerprint_value


def protocol(size):
    return SearchProtocol('scope', 'code', (SearchExperiment('a', 'read'),),
                          tuple((str(i),) for i in range(size)))


def lazy_problem(size):
    p = protocol(2)
    candidates = tuple(ModelSearchCandidate(BooleanExpression(('false',)).to_model('c'+str(i)),
                                           DescriptionLength()) for i in range(size))
    cases = (ModelSearchCase('a', Context(), ScenarioKey('s', 'baseline'), 'y'),)
    return LazyExecutableSearch(p, candidates, cases, target='answer', world_answers=('no', 'yes'))


def transition(size):
    p = protocol(size)
    c = ModelingContext('c', p, 'lab', 'exact', ('x',), 'finite', 'answer')
    return ContextTransition(c, c, ContextChange.REFINEMENT, (('a', 'a'),),
                             tuple(('a', str(i), str(i)) for i in range(size)))



def matching_source_worlds(transition, target_world, budget):
    old_names = tuple(e.name for e in transition.source.protocol.experiments)
    new_names = tuple(e.name for e in transition.target.protocol.experiments)
    translated = tuple((old_names.index(a), transition.translate_response(a, target_world[new_names.index(b)]))
                       for a, b in transition.experiments)
    matches = []
    for i, row in enumerate(transition.source.protocol.worlds):
        budget.consume('response_checks')
        if all(row[j] == response for j, response in translated):
            matches.append(i)
    return tuple(matches)

class ReviewImprovementTests(unittest.TestCase):
    def test_all_boolean_coordinates_are_validated_before_search(self):
        for variables in (('x', 'z'), ('z', 'x')):
            language = BooleanLanguage(variables, max_nodes=1)
            for target in (BooleanExpression(('var', 'x')), BooleanExpression(('false',))):
                valid = ({'x': False, 'z': True},)
                receipt = find_boolean_substitute(target, language, valid)
                for invalid in (7, 0, None, 'false'):
                    inputs = ({'x': False, 'z': invalid},)
                    with self.assertRaises(ValueError):
                        find_boolean_substitute(target, language, inputs)
                    forged = replace(receipt, experiment_fingerprint=fingerprint_value(inputs))
                    self.assertEqual(verify_boolean_substitute(target, language, inputs, forged), 'invalid')

    def test_boolean_ast_and_executable_model_agree_over_bounded_language(self):
        language = BooleanLanguage(('x', 'z'), max_nodes=4)
        catalogue = enumerate_boolean_language(language)
        self.assertTrue(catalogue.complete)
        for expression in catalogue.expressions:
            model = expression.to_model('expression')
            for values in product((False, True), repeat=2):
                inputs = dict(zip(language.variables, values))
                expected = str(int(expression.evaluate(inputs)))
                self.assertEqual(model.audited_observe({}, Context(environment=inputs))['y'], expected)

    def test_first_witness_and_direct_verification_have_bounded_work(self):
        for size in (10, 1000):
            p = protocol(size)
            h = SearchHypothesis('first', 0, 'yes', DescriptionLength())
            problem = ExperimentHypothesisSearch(p, (h,), 'answer')
            evidence = (SearchObservation('a', '0', 'lab'),)
            for query in (ConstraintQuery(evidence=evidence), MacroAlternativeQuery('no', evidence)):
                with patch.object(ExperimentHypothesisSearch, '_worlds', side_effect=AssertionError('full filter')):
                    receipt = problem.query(query, budget=SearchWorkBudget(10))
                    self.assertEqual(receipt.status, QueryStatus.FOUND)
                    check = verify_query_result(problem, query, receipt, budget=SearchWorkBudget(3))
                    self.assertEqual(check.status, 'valid')
                    bad = replace(receipt, witness_world=size-1)
                    self.assertEqual(verify_query_result(problem, query, bad).status, 'invalid')
                indexed = ExperimentHypothesisSearch(p, (h,), 'answer', backend='indexed')
                with patch.object(ExperimentHypothesisSearch, '_index', side_effect=AssertionError('index build')):
                    self.assertEqual(verify_query_result(indexed, query, receipt, budget=SearchWorkBudget(3)).status, 'valid')

    def test_absence_remains_exhaustive_and_budgeted(self):
        p = SearchProtocol('scope', 'code', (SearchExperiment('a', 'a'), SearchExperiment('b', 'b')),
                           (('0', '0'), ('1', '1')))
        problem = ExperimentHypothesisSearch(p, (), 'out')
        query = ConstraintQuery(evidence=(SearchObservation('a', '0', 'lab'), SearchObservation('b', '1', 'lab')))
        full = problem.query(query)
        self.assertEqual(full.status, QueryStatus.ABSENT)
        self.assertEqual(verify_query_result(problem, query, full).status, 'valid')
        for cap in range(full.work.total):
            self.assertEqual(problem.query(query, budget=SearchWorkBudget(cap)).status, QueryStatus.UNKNOWN)

    def test_screening_declaration_checks_scale_linearly_cold_and_warm(self):
        import bidirectional_modeling.search_lazy as module
        for size in (4, 8, 16):
            lazy = lazy_problem(size)
            data = (SearchObservation('a', '1', 'lab'),)
            for cold in (True, False):
                with patch.object(module, 'model_declaration_fingerprint', wraps=module.model_declaration_fingerprint) as calls:
                    result = lazy.screen_evidence(data)
                    self.assertLessEqual(calls.call_count, 2*size)
                    self.assertEqual(len(result.excluded), size)
                    self.assertEqual(result.simulations_used, 2*size if cold else 0)

    def test_cross_candidate_drift_invalidates_screen_and_cache(self):
        import bidirectional_modeling.search_lazy as module
        lazy = lazy_problem(2)
        collect = module.collect_partial_prediction
        changed = [False]
        def mutating_collect(*args, **kwargs):
            result = collect(*args, **kwargs)
            if not changed[0]:
                # Model a callback side effect on another candidate declaration.
                lazy._candidates[1].model.states['s']['changed'] = True
                changed[0] = True
            return result
        with patch.object(module, 'collect_partial_prediction', side_effect=mutating_collect):
            with self.assertRaisesRegex(ValueError, 'declaration changed'):
                lazy.screen_evidence((SearchObservation('a', '1', 'lab'),))
        with self.assertRaises(ValueError):
            lazy.snapshot
        with self.assertRaises(ValueError):
            lazy.predict_experiments('c0', ('a',))

    def test_indexed_relations_match_independent_scan_including_dense_projection(self):
        for size in (1, 10, 100):
            t = transition(size)
            budget = SearchWorkBudget()
            report, relation = _prepare_context_transition(t, budget=budget)
            self.assertEqual(report.status, 'valid')
            self.assertEqual(relation, tuple((i,) for i in range(size)))
            self.assertLessEqual(budget.work.total, 4*size)
            stopped, _ = _prepare_context_transition(t, budget=SearchWorkBudget(0))
            self.assertEqual(stopped.status, 'undecided')
        t = transition(5)
        for experiments in (t.experiments, ()):
            t = replace(t, kind=ContextChange.RECONSTRUCTION, experiments=experiments,
                        responses=t.responses if experiments else ())
            report, relation = _prepare_context_transition(t)
            reference = tuple(matching_source_worlds(t, row, SearchWorkBudget())
                              for row in t.target.protocol.worlds)
            self.assertEqual(report.status, 'valid')
            self.assertEqual(relation, reference)
        # Relation output itself is quadratic for an empty projection, and charged.
        report, relation = _prepare_context_transition(t, budget=SearchWorkBudget(20))
        self.assertEqual(report.status, 'undecided')
        self.assertLess(len(relation), 5)
