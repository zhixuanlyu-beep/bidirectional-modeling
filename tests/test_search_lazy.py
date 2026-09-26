import unittest
from dataclasses import replace

from bidirectional_modeling import (
    ConstraintQuery, ExecutableSearchAdapter, LazyExecutableSearch,
    LowerSubstituteQuery, MacroAlternativeQuery, QueryStatus,
    SearchObservation, SearchWorkBudget, verify_query_result,
)
from test_search_integration import adapter_args, model


class LazyTests(unittest.TestCase):
    def lazy(self, *args, **kwargs):
        return LazyExecutableSearch(*(args or adapter_args()), target='out',
                                    world_answers=('low', 'high'), **kwargs)

    def test_first_witness_saves_simulations_and_cache_reuses(self):
        lazy = self.lazy()
        q = MacroAlternativeQuery('high')
        r = lazy.execute(q)
        self.assertEqual(r.simulations_used, 2)
        self.assertEqual(r.resolved_candidates, ('zero',))
        self.assertIs(r.receipt.status, QueryStatus.FOUND)
        self.assertEqual(tuple(h.world for h in r.search.hypotheses), (0, None))
        self.assertEqual(verify_query_result(r.search, q, r.receipt).status, 'valid')
        warm = lazy.execute(q, max_simulations=0)
        self.assertEqual(warm.simulations_used, 0)
        self.assertEqual(warm.batch_bindings, r.batch_bindings)
        # A later query resolves the other candidate, leaving old snapshot intact.
        later = lazy.execute(MacroAlternativeQuery('low'))
        self.assertEqual(later.resolved_candidates, ('one',))
        self.assertEqual(tuple(h.world for h in r.search.hypotheses), (0, None))
        self.assertEqual(verify_query_result(later.search, q, r.receipt).status, 'invalid')

    def test_absence_matches_eager_for_both_backends(self):
        for backend in ('scan', 'indexed'):
            for answer in ('low', 'high'):
                for evidence in ((), (SearchObservation('read', '0', 'lab'),),
                                 (SearchObservation('read', '1', 'lab'),)):
                    q = MacroAlternativeQuery(answer, evidence)
                    r = self.lazy(backend=backend).execute(q)
                    eager = ExecutableSearchAdapter().prepare(*adapter_args(), target='out',
                        world_answers=('low', 'high'), backend=backend)
                    expected = eager.search.query(q)
                    self.assertIs(r.receipt.status, expected.status)
                    self.assertEqual(r.receipt.witness_candidate, expected.witness_candidate)
                    self.assertEqual(r.receipt.compatible_catalogue_nonempty, expected.compatible_catalogue_nonempty)
                    self.assertEqual(verify_query_result(r.search, q, r.receipt).status, 'valid')
                    self.assertLessEqual(r.simulations_used, eager.simulations_used)

    def test_interruption_does_not_cache_partial_predictions(self):
        lazy = self.lazy()
        q = MacroAlternativeQuery('high')
        r = lazy.execute(q, max_simulations=1)
        self.assertIs(r.receipt.status, QueryStatus.UNKNOWN)
        self.assertEqual(r.simulations_used, 1)
        self.assertEqual(r.batch_bindings, ())
        self.assertEqual(r.resolved_candidates, ())
        resumed = lazy.execute(q, max_simulations=2)
        self.assertIs(resumed.receipt.status, QueryStatus.FOUND)
        self.assertEqual(resumed.simulations_used, 2)

    def test_budget_and_cancellation_do_not_simulate(self):
        q = MacroAlternativeQuery('high')
        for budget in (SearchWorkBudget(0), SearchWorkBudget(cancelled=lambda: True)):
            r = self.lazy().execute(q, budget=budget)
            self.assertEqual(r.simulations_used, 0)
            self.assertIs(r.receipt.status, QueryStatus.UNKNOWN)
        r = self.lazy().execute(q, max_simulations=0)
        self.assertIs(r.receipt.status, QueryStatus.UNKNOWN)
        self.assertEqual(r.diagnostics[0][-1], 'simulation_budget_exhausted')

    def test_constraint_query_needs_no_predictions(self):
        r = self.lazy().execute(ConstraintQuery(), max_simulations=0)
        self.assertEqual(r.simulations_used, 0)
        self.assertIs(r.receipt.status, QueryStatus.FOUND)

    def test_lower_query_resolves_only_reference_candidates(self):
        p, candidates, cases = adapter_args()
        alias = replace(candidates[0], model=model('alias', '0'))
        lazy = self.lazy(p, candidates + (alias,), cases)
        q = LowerSubstituteQuery('zero', ('alias',))
        r = lazy.execute(q)
        self.assertIs(r.receipt.status, QueryStatus.FOUND)
        self.assertEqual(r.resolved_candidates, ('zero', 'alias'))
        self.assertEqual(r.simulations_used, 4)
        self.assertIsNone(r.search.hypotheses[1].world)
        self.assertEqual(verify_query_result(r.search, q, r.receipt).status, 'valid')
        self.assertIs(lazy.execute(LowerSubstituteQuery('zero', ())).receipt.status, QueryStatus.UNKNOWN)

    def test_failed_candidate_does_not_hide_later_witness(self):
        p, candidates, cases = adapter_args()
        bad = replace(candidates[0], model=model('bad', 'outside'))
        r = self.lazy(p, (bad, candidates[1]), cases).execute(MacroAlternativeQuery('low'))
        self.assertIs(r.receipt.status, QueryStatus.FOUND)
        self.assertEqual(r.resolved_candidates, ('one',))
        self.assertEqual(len(r.diagnostics), 1)
        self.assertEqual(len(r.batch_bindings), 1)

    def test_original_model_mutation_is_isolated_and_new_instance_rebinds(self):
        p, candidates, cases = adapter_args()
        lazy = self.lazy(p, candidates, cases)
        before = lazy.execute(MacroAlternativeQuery('high'))
        candidates[0].model.states['s']['y'] = '1'
        after = lazy.execute(MacroAlternativeQuery('high'))
        self.assertEqual(before.declaration_fingerprint, after.declaration_fingerprint)
        self.assertEqual(after.simulations_used, 0)
        fresh = self.lazy(p, candidates, cases).execute(MacroAlternativeQuery('high'))
        self.assertNotEqual(fresh.declaration_fingerprint, before.declaration_fingerprint)
        self.assertIs(fresh.receipt.status, QueryStatus.ABSENT)

    def test_detected_internal_drift_rejects_cached_results(self):
        lazy = self.lazy()
        lazy.execute(MacroAlternativeQuery('high'))
        lazy._candidates[0].model.states['s']['y'] = '1'
        with self.assertRaisesRegex(ValueError, 'declaration changed'):
            lazy.execute(MacroAlternativeQuery('high'))

    def test_invalid_inputs_fail_before_prediction(self):
        with self.assertRaises(ValueError): self.lazy().execute(LowerSubstituteQuery('missing', ('zero',)))
        with self.assertRaises(ValueError): self.lazy().execute(ConstraintQuery(), max_simulations=-1)
        p, candidates, cases = adapter_args()
        with self.assertRaises(ValueError): self.lazy(p, candidates * 2, cases)

    def test_collector_drift_and_exception_never_enter_cache(self):
        from bidirectional_modeling.evaluation import SatisfactionEvaluator
        class Drifting:
            def collect(self, m, ctx, horizon, budget):
                m.states['s']['y'] = 'changed'
                return SatisfactionEvaluator().collect(m, ctx, horizon, budget)
        class Broken:
            def collect(self, *args):
                raise RuntimeError('collector failed')
        for evaluator in (Drifting(), Broken()):
            r = self.lazy(evaluator=evaluator).execute(MacroAlternativeQuery('high'), max_simulations=4)
            self.assertIs(r.receipt.status, QueryStatus.UNKNOWN)
            self.assertEqual(r.resolved_candidates, ())
            self.assertEqual(r.batch_bindings, ())
            self.assertTrue(r.diagnostics)

    def test_work_cutoffs_never_exceed_simulation_limit(self):
        q = MacroAlternativeQuery('low', (SearchObservation('read', '0', 'lab'),))
        for limit in range(30):
            r = self.lazy().execute(q, budget=SearchWorkBudget(limit), max_simulations=3)
            self.assertLessEqual(r.simulations_used, 3)
            self.assertLessEqual(r.receipt.work.total, limit)
            self.assertIs(r.receipt.status, QueryStatus.UNKNOWN)
