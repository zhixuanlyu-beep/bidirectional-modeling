"""Cross-interface regressions found during the 0.18.0 review."""
import unittest
from dataclasses import replace

from bidirectional_modeling import (
    LazyExecutableSearch, MacroAlternativeQuery, LowerSubstituteQuery,
    QueryStatus, SearchWorkBudget, collect_partial_prediction,
    verify_partial_prediction, verify_query_result,
)
from test_search_integration import adapter_args, model
import test_search_partial as partial_tests
from test_search_partial import ExternalResponse, partial_args


class ReviewRegressions(unittest.TestCase):
    def tearDown(self):
        ExternalResponse.output = '0'

    def test_partial_verification_separates_execution_and_original_limits(self):
        args = partial_args()
        for selected, required in ((('a',), 2), (('a', 'b'), 4)):
            for original in (required, 10000):
                prediction = collect_partial_prediction(*args, selected,
                    max_simulations=original).prediction
                self.assertEqual(prediction.simulation_limit, original)
                for verification in (0, required - 1, required, 10000):
                    r = verify_partial_prediction(*args, prediction, max_simulations=verification)
                    self.assertEqual(r.status, 'valid' if verification >= required else 'undecided')
                    self.assertLessEqual(r.simulations_used, verification)

    def test_collection_protocol_cannot_be_forged_or_guessed(self):
        args = partial_args()
        p = collect_partial_prediction(*args, ('a',), max_simulations=2).prediction
        self.assertEqual(verify_partial_prediction(*args, replace(p, simulation_limit=10000)).status, 'invalid')
        self.assertEqual(verify_partial_prediction(*args, replace(p, simulation_limit=True)).status, 'invalid')
        self.assertEqual(verify_partial_prediction(*args, replace(p, simulation_limit=None)).reason,
                         'missing_collection_protocol')
        self.assertEqual(verify_partial_prediction(*args, replace(p, responses=('1',)),
                                                  max_simulations=10).status, 'invalid')

    def test_replay_exception_respects_verification_ceiling(self):
        class Broken:
            def collect(self, *args): raise RuntimeError('failed')
        args = partial_args()
        p = collect_partial_prediction(*args, ('a',)).prediction
        r = verify_partial_prediction(*args, p, max_simulations=3, evaluator=Broken())
        self.assertEqual(r.status, 'undecided')
        self.assertLessEqual(r.simulations_used, 3)

    def test_domain_violation_invalidates_partial_and_full_caches(self):
        helper = partial_tests.PartialTests()
        for complete in (False, True):
            ExternalResponse.output = '0'
            lazy = helper.lazy(helper.external_args())
            if complete:
                lazy.execute(MacroAlternativeQuery('high'))
            else:
                lazy.predict_experiments('external', ('a',))
            historical = lazy.snapshot
            ExternalResponse.output = 'outside'
            with self.assertRaisesRegex(ValueError, 'prediction drift'):
                lazy.predict_experiments('external', ('b',))
            with self.assertRaises(ValueError): lazy.execute(MacroAlternativeQuery('high'), max_simulations=0)
            with self.assertRaises(ValueError): lazy.predict_experiments('external', ('a',), max_simulations=0)
            with self.assertRaises(ValueError): _ = lazy.snapshot
            # History is deliberately retained, but no longer exposed as current.
            self.assertEqual(len(historical.hypotheses), 1)

    def test_domain_violation_during_query_rejects_prior_partial(self):
        helper = partial_tests.PartialTests()
        ExternalResponse.output = '0'
        lazy = helper.lazy(helper.external_args())
        lazy.predict_experiments('external', ('a',))
        ExternalResponse.output = 'outside'
        with self.assertRaises(ValueError): lazy.execute(MacroAlternativeQuery('high'))
        with self.assertRaises(ValueError): _ = lazy.snapshot

    def test_snapshot_checks_declared_mutation(self):
        p, c, cases = adapter_args()
        lazy = LazyExecutableSearch(p, c, cases, target='out', world_answers=('low', 'high'))
        lazy._candidates[0].model.states['s']['y'] = '1'
        with self.assertRaises(ValueError): _ = lazy.snapshot

    def test_absence_scan_work_is_linear_in_candidate_count(self):
        for n in (10, 20, 40):
            p, c, cases = adapter_args()
            cs = tuple(replace(c[0], model=model(str(i), '0')) for i in range(n))
            lazy = LazyExecutableSearch(p, cs, cases, target='out', world_answers=('low', 'high'))
            q = MacroAlternativeQuery('low')
            r = lazy.execute(q)
            self.assertIs(r.receipt.status, QueryStatus.ABSENT)
            self.assertLessEqual(r.receipt.work.candidate_checks, 3 * n)
            self.assertEqual(r.simulations_used, 2 * n)
            self.assertEqual(verify_query_result(r.search, q, r.receipt).status, 'valid')

    def test_lower_query_uses_cached_alias_after_resolving_target(self):
        p, cs, cases = adapter_args()
        alias = replace(cs[0], model=model('alias', '0'))
        lazy = LazyExecutableSearch(p, cs + (alias,), cases, target='out', world_answers=('low', 'high'))
        lazy.predict_experiments('alias', ('read',))
        q = LowerSubstituteQuery('zero', ('alias', 'one'))
        r = lazy.execute(q)
        self.assertEqual(r.resolved_candidates, ('zero',))
        self.assertIs(r.receipt.status, QueryStatus.FOUND)
        self.assertEqual(verify_query_result(r.search, q, r.receipt).status, 'valid')

    def test_completed_predictions_survive_work_interruption(self):
        for cutoff in range(1, 30):
            lazy = LazyExecutableSearch(*adapter_args(), target='out', world_answers=('low', 'high'))
            q = MacroAlternativeQuery('low')
            r = lazy.execute(q, budget=SearchWorkBudget(cutoff))
            self.assertLessEqual(r.receipt.work.total, cutoff)
            if r.resolved_candidates:
                known = {h.name for h in lazy.snapshot.hypotheses if h.world is not None}
                self.assertEqual(known, set(r.resolved_candidates))
            if r.receipt.status is not QueryStatus.UNKNOWN:
                self.assertEqual(verify_query_result(r.search, q, r.receipt).status, 'valid')
