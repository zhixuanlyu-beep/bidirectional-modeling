import unittest
from dataclasses import replace
from itertools import product

from bidirectional_modeling import (
    Context, LazyExecutableSearch, MacroAlternativeQuery, QueryStatus,
    ResponseConstraint, ScenarioKey, SearchExperiment, SearchProtocol, SearchWorkBudget,
    Trace, collect_partial_prediction, verify_partial_prediction, verify_query_result,
)
from test_search_integration import adapter_args


def partial_args():
    _, candidates, cases = adapter_args()
    protocol = SearchProtocol('scope', 'code',
        (SearchExperiment('a', 'read y'), SearchExperiment('b', 'read y')),
        tuple(product(('0', '1'), repeat=2)))
    return protocol, candidates[0], tuple(replace(cases[0], experiment=n) for n in ('a', 'b'))


class ExternalResponse:
    name = 'external'
    output = '0'

    def search_signature(self):
        return {'version': 'fixed'}

    def simulate(self, context, horizon):
        return (Trace(self.name, 's', 'baseline',
                      tuple({'y': self.output} for _ in range(horizon + 1))),)


class PartialTests(unittest.TestCase):
    def lazy(self, args=None):
        p, c, cases = args or partial_args()
        return LazyExecutableSearch(p, (c,), cases, target='out',
                                    world_answers=('low', 'mixed', 'mixed', 'high'))

    def external_args(self):
        p, c, cases = partial_args()
        cases = tuple(replace(case, context=Context(
            scenario_manifest=(ScenarioKey('s', 'baseline'),))) for case in cases)
        return p, replace(c, model=ExternalResponse()), cases

    def setUp(self):
        ExternalResponse.output = '0'

    def tearDown(self):
        ExternalResponse.output = '0'

    def test_selected_case_saves_simulations_and_replays(self):
        args = partial_args()
        result = collect_partial_prediction(*args, ('b',))
        self.assertEqual(result.simulations_used, 2)
        self.assertEqual(result.prediction.experiments, ('b',))
        self.assertEqual(result.prediction.responses, ('0',))
        self.assertEqual(len(result.prediction.batch_bindings), 1)
        verified = verify_partial_prediction(*args, result.prediction)
        self.assertEqual(verified.status, 'valid')
        self.assertEqual(verified.simulations_used, 2)
        full = collect_partial_prediction(*args, ('b', 'a'))
        self.assertEqual(full.prediction.experiments, ('a', 'b'))
        self.assertEqual(full.simulations_used, 4)
        self.assertNotEqual(full.prediction.fingerprint, result.prediction.fingerprint)

    def test_projection_of_all_subsets_and_worlds(self):
        p, c, cases = partial_args()
        # Contexts provide per-experiment outputs, so all four worlds are executable.
        from bidirectional_modeling import FiniteStateModel, ModelMetrics
        for row in p.worlds:
            model = FiniteStateModel('row', {'s': {}}, ('s',), ('noop',),
                lambda state, action, context: state,
                lambda state, context: {'y': context.environment['y']}, ModelMetrics(1, 1, 0))
            selected_cases = tuple(replace(case, context=Context(environment={'y': y}))
                                   for case, y in zip(cases, row))
            candidate = replace(c, model=model)
            for selected in (('a',), ('b',), ('a', 'b')):
                r = collect_partial_prediction(p, candidate, selected_cases, selected)
                self.assertIsNotNone(r.prediction)
                self.assertEqual(r.prediction.responses, tuple(row[('a', 'b').index(n)] for n in selected))
                self.assertEqual(verify_partial_prediction(p, candidate, selected_cases, r.prediction).status, 'valid')

    def test_partial_does_not_resolve_query_and_full_promotion_does(self):
        lazy = self.lazy()
        q = MacroAlternativeQuery('high')
        first = lazy.predict_experiments('zero', ('a',))
        self.assertEqual(first.simulations_used, 2)
        self.assertIsNone(lazy.snapshot.hypotheses[0].world)
        self.assertIs(lazy.execute(q, max_simulations=0).receipt.status, QueryStatus.UNKNOWN)
        cached = lazy.predict_experiments('zero', ('a',), max_simulations=0)
        self.assertEqual(cached.prediction, first.prediction)
        self.assertEqual(cached.simulations_used, 0)
        extended = lazy.predict_experiments('zero', ('b',))
        self.assertEqual(extended.prediction.experiments, ('a', 'b'))
        self.assertEqual(extended.simulations_used, 4)
        r = lazy.execute(q, max_simulations=0)
        self.assertIs(r.receipt.status, QueryStatus.FOUND)
        self.assertEqual(r.simulations_used, 0)
        self.assertEqual(verify_query_result(r.search, q, r.receipt).status, 'valid')
        self.assertEqual(lazy.predict_experiments('zero', ('b',), max_simulations=0).prediction, extended.prediction)

    def test_failed_extension_keeps_only_completed_matrix(self):
        lazy = self.lazy()
        first = lazy.predict_experiments('zero', ('a',))
        failed = lazy.predict_experiments('zero', ('b',), max_simulations=3)
        self.assertIsNone(failed.prediction)
        self.assertEqual(failed.simulations_used, 3)
        self.assertIsNone(lazy.snapshot.hypotheses[0].world)
        self.assertEqual(lazy.predict_experiments('zero', ('a',), max_simulations=0).prediction, first.prediction)
        self.assertIsNotNone(lazy.predict_experiments('zero', ('b',), max_simulations=4).prediction)

    def test_budget_cutoffs_do_not_publish_incomplete_responses(self):
        args = partial_args()
        for limit in range(5):
            r = collect_partial_prediction(*args, ('a', 'b'), max_simulations=limit)
            self.assertLessEqual(r.simulations_used, limit)
            self.assertEqual(r.prediction is not None, limit == 4)
        for budget in (SearchWorkBudget(0), SearchWorkBudget(cancelled=lambda: True)):
            r = collect_partial_prediction(*args, ('a',), budget=budget)
            self.assertIsNone(r.prediction)
            self.assertEqual(r.simulations_used, 0)
        lazy = self.lazy()
        lazy.predict_experiments('zero', ('a',))
        self.assertIsNone(lazy.predict_experiments('zero', ('a',), budget=SearchWorkBudget(0)).prediction)

    def test_replay_rejects_tampering_and_changed_unexecuted_case(self):
        p, c, cases = partial_args()
        pred = collect_partial_prediction(p, c, cases, ('a',)).prediction
        for forged in (replace(pred, responses=('1',)), replace(pred, responses=()),
                       replace(pred, candidate='other'), replace(pred, experiments=('unknown',)),
                       replace(pred, experiments=('a', 'a')), replace(pred, batch_bindings=())):
            self.assertEqual(verify_partial_prediction(p, c, cases, forged).status, 'invalid')
        changed = (cases[0], replace(cases[1], horizon=2))
        self.assertEqual(verify_partial_prediction(p, c, changed, pred).reason, 'binding_mismatch')
        self.assertEqual(verify_partial_prediction(p, c, cases, pred, max_simulations=1).status, 'undecided')
        self.assertEqual(verify_partial_prediction(p, c, cases, pred, budget=SearchWorkBudget(0)).status, 'undecided')

    def test_commitments_are_checked_at_full_promotion(self):
        p, c, cases = partial_args()
        p = replace(p, constraints=(ResponseConstraint('one', (3,)),))
        c = replace(c, commitments=('one',))
        lazy = self.lazy((p, c, cases))
        first = lazy.predict_experiments('zero', ('a',))
        self.assertIsNotNone(first.prediction)
        with self.assertRaisesRegex(ValueError, 'commitment'):
            lazy.predict_experiments('zero', ('b',))
        self.assertIsNone(lazy.snapshot.hypotheses[0].world)
        self.assertEqual(lazy.predict_experiments('zero', ('a',)).prediction, first.prediction)

    def test_conflicting_extension_quarantines_instance(self):
        lazy = self.lazy(self.external_args())
        lazy.predict_experiments('external', ('a',))
        ExternalResponse.output = '1'
        with self.assertRaisesRegex(ValueError, 'partial response changed'):
            lazy.predict_experiments('external', ('b',))
        with self.assertRaisesRegex(ValueError, 'declaration changed'):
            lazy.execute(MacroAlternativeQuery('high'))
        with self.assertRaises(ValueError): lazy.predict_experiments('external', ('a',))

    def test_full_query_must_agree_with_previous_partial(self):
        lazy = self.lazy(self.external_args())
        lazy.predict_experiments('external', ('a',))
        ExternalResponse.output = '1'
        with self.assertRaisesRegex(ValueError, 'partial response changed'):
            lazy.execute(MacroAlternativeQuery('low'))

    def test_partial_must_agree_with_previous_full_query(self):
        lazy = self.lazy(self.external_args())
        lazy.execute(MacroAlternativeQuery('high'))
        ExternalResponse.output = '1'
        with self.assertRaisesRegex(ValueError, 'partial response changed'):
            lazy.predict_experiments('external', ('a',))

    def test_cross_case_drift_is_not_assembled_into_complete_row(self):
        p, c, cases = self.external_args()
        class Stateful(ExternalResponse):
            def __init__(self): self.calls = 0
            def simulate(self, context, horizon):
                self.output = '0' if self.calls < 2 else '1'
                self.calls += 1
                return super().simulate(context, horizon)
        lazy = self.lazy((p, replace(c, model=Stateful()), cases))
        self.assertIsNotNone(lazy.predict_experiments('external', ('a',)).prediction)
        historical = lazy.snapshot
        with self.assertRaisesRegex(ValueError, 'prediction drift'):
            lazy.predict_experiments('external', ('b',))
        with self.assertRaises(ValueError):
            _ = lazy.snapshot
        self.assertIsNone(historical.hypotheses[0].world)

    def test_input_validation(self):
        args = partial_args()
        for names in ((), ('missing',), ('a', 'a')):
            with self.assertRaises(ValueError): collect_partial_prediction(*args, names)
            with self.assertRaises(ValueError): self.lazy().predict_experiments('zero', names)
        with self.assertRaises(ValueError): self.lazy().predict_experiments('missing', ('a',))
        p, c, cases = args
        with self.assertRaises(ValueError): collect_partial_prediction(p, c, cases[:1], ('a',))
