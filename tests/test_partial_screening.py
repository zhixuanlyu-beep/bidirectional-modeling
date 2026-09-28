import unittest
from dataclasses import replace
from bidirectional_modeling.search_lazy import (LazyExecutableSearch)
from bidirectional_modeling import (SearchObservation, SearchWorkBudget)
from bidirectional_modeling.search_partial import (verify_candidate_exclusion)
from bidirectional_modeling.search_adapter import (ExecutableSearchAdapter)
from test_search_partial import partial_args


class PartialScreeningTests(unittest.TestCase):
    def lazy(self):
        p, c, cases = partial_args()
        return LazyExecutableSearch(p, (c,), cases, target='out',
                                    world_answers=('low', 'mixed', 'mixed', 'high'))

    def test_one_mismatch_stops_before_other_experiments(self):
        p, c, cases = partial_args()
        evidence = (SearchObservation('a', '1', 'lab'), SearchObservation('b', '0', 'lab'))
        lazy = self.lazy()
        r = lazy.screen_evidence(evidence)
        self.assertEqual(r.excluded, ('zero',))
        self.assertEqual(r.simulations_used, 2)
        self.assertIsNone(lazy.snapshot.hypotheses[0].world)
        full = ExecutableSearchAdapter().prepare(p, (c,), cases, target='out',
                                                world_answers=('low', 'mixed', 'mixed', 'high'))
        self.assertLess(r.simulations_used, full.simulations_used)
        self.assertEqual(full.search.search(evidence).rejected, r.excluded)
        self.assertEqual(verify_candidate_exclusion(p, c, cases, r.certificates[0], evidence).status, 'valid')
        self.assertEqual(verify_candidate_exclusion(p, c, cases, r.certificates[0], ()).status, 'invalid')
        self.assertEqual(verify_candidate_exclusion(p, c, cases, r.certificates[0], evidence,
                                                   max_simulations=0).status, 'undecided')
        forged = replace(r.certificates[0], observation=SearchObservation('a', '0', 'lab'))
        self.assertEqual(verify_candidate_exclusion(p, c, cases, forged, (forged.observation,)).status, 'invalid')

    def test_matching_partial_is_not_a_full_prediction_and_retraction_is_safe(self):
        lazy = self.lazy()
        r = lazy.screen_evidence((SearchObservation('a', '0', 'lab'),))
        self.assertEqual(r.matching_evidence, ('zero',))
        self.assertFalse(r.excluded)
        self.assertIsNone(lazy.snapshot.hypotheses[0].world)
        r = lazy.screen_evidence((SearchObservation('a', '1', 'lab'),))
        self.assertEqual(r.excluded, ('zero',))
        r = lazy.screen_evidence(())
        self.assertFalse(r.excluded)
        self.assertEqual(r.matching_evidence, ('zero',))

    def test_unknown_budget_and_inconsistent_evidence_do_not_reject(self):
        evidence = (SearchObservation('a', '1', 'lab'),)
        for kwargs in ({'max_simulations': 0}, {'budget': SearchWorkBudget(0)}):
            r = self.lazy().screen_evidence(evidence, **kwargs)
            self.assertFalse(r.excluded)
            self.assertEqual(r.undecided, ('zero',))
        data = evidence + (SearchObservation('a', '0', 'lab'),)
        r = self.lazy().screen_evidence(data)
        self.assertEqual(r.reason, 'inconsistent_evidence')
        self.assertFalse(r.excluded)
        self.assertEqual(r.simulations_used, 0)
