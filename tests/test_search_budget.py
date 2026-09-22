"""Regression checks for bounded proof checking and unambiguous result domains."""
import unittest
from dataclasses import replace

from bidirectional_modeling import (
    DescriptionLength, ExperimentHypothesisSearch, SearchBudgetExceeded,
    SearchHypothesis, SearchObservation, SearchWorkBudget,
)
from bidirectional_modeling.search_examples import conflict_search_scenario


class SearchBudgetTests(unittest.TestCase):
    def setUp(self):
        self.search, self.data = conflict_search_scenario()
        self.certificate = self.search.compress_evidence(self.data)

    def test_sufficiency_is_independent_of_minimality_verification(self):
        result = self.search.verify_macro(self.certificate, self.data, check_minimality=False)
        self.assertEqual(result.sufficiency, 'valid')
        self.assertEqual(result.minimality, 'not_checked')
        self.assertFalse(result.valid)
        self.assertEqual(result.subsets_checked, 0)
        result = self.search.verify_macro(self.certificate, self.data, max_subsets=0)
        self.assertEqual(result.sufficiency, 'valid')
        self.assertEqual(result.minimality, 'undecided')
        self.assertEqual(result.stop_reason, 'subset_budget_exhausted')
        self.assertFalse(self.search.validates_macro(self.certificate, self.data, max_subsets=0))
        result = self.search.verify_macro(self.certificate, self.data)
        self.assertTrue(result.valid)
        self.assertEqual(result.minimality, 'valid')

    def test_operation_budget_preserves_completed_sufficiency(self):
        sufficient = self.search.verify_macro(self.certificate, self.data, check_minimality=False)
        budget = SearchWorkBudget(sufficient.work.total)
        result = self.search.verify_macro(self.certificate, self.data, budget=budget)
        self.assertEqual(result.sufficiency, 'valid')
        self.assertEqual(result.minimality, 'undecided')
        self.assertEqual(result.stop_reason, 'work_budget_exhausted')
        self.assertEqual(result.work.total, budget.max_operations)

    def test_zero_budget_is_undecided_not_invalid(self):
        budget = SearchWorkBudget(0)
        result = self.search.verify_macro(self.certificate, self.data, budget=budget)
        self.assertEqual(result.sufficiency, 'undecided')
        self.assertEqual(result.work.total, 0)
        self.assertFalse(result.valid)
        result = self.search.search(self.data, budget=SearchWorkBudget(0))
        self.assertEqual(set(result.undecided), {'x','z','xz'})
        self.assertFalse(result.determined)
        self.assertFalse(result.partition_complete)
        self.assertEqual(result.full_quotient, ())
        self.assertEqual(result.surviving_quotient, ())
        self.assertEqual(result.stop_reason, 'work_budget_exhausted')
        self.assertEqual({r for _,r in result.undecided_reasons}, {'work_budget_exhausted'})

    def test_report_separates_three_partition_domains(self):
        result = self.search.search(self.data)
        self.assertEqual(result.full_quotient, (('x',),('z',),('xz',)))
        self.assertEqual(result.quotient, result.full_quotient)  # compatibility alias
        self.assertEqual(result.surviving_quotient, (('xz',),))
        self.assertEqual(result.observed_quotient, (('xz',),))
        self.assertEqual(result.stop_reason, 'determined')
        self.assertTrue(result.partition_complete)
        self.assertEqual(result.experiment_domain, ('00','10','01','11'))
        partial = self.search.search(self.data[:1])
        self.assertEqual(partial.observed_experiments, ('00',))
        self.assertEqual(partial.observed_quotient, (('x','z','xz'),))
        self.assertEqual(partial.stop_reason, 'macro_ambiguous')
        self.assertEqual(partial.surviving_quotient, result.full_quotient)

    def test_unknown_and_replay_exhaustion_are_distinct(self):
        h = SearchHypothesis('unknown', None, 'other', DescriptionLength())
        search = ExperimentHypothesisSearch(self.search.protocol, self.search.hypotheses+(h,), self.search.target)
        result = search.search(self.data)
        self.assertEqual(result.stop_reason, 'unknown_predictions')
        self.assertEqual(result.undecided_reasons, (('unknown','unknown_prediction'),))
        result = search.search(self.data, max_replays=0)
        reasons = dict(result.undecided_reasons)
        self.assertEqual(reasons['unknown'], 'unknown_prediction')
        self.assertEqual(reasons['x'], 'replay_budget_exhausted')
        self.assertEqual(result.stop_reason, 'replay_budget_exhausted')
        self.assertEqual(result.replay_checks, 0)
        self.assertGreater(result.work.response_checks, 0)

    def test_distinguish_bad_evidence_from_inadequate_catalogue(self):
        result = self.search.search(self.data+(SearchObservation('00','1','other'),))
        self.assertEqual(result.stop_reason, 'inconsistent_evidence')
        self.assertFalse(result.determined)
        result = self.search.search((SearchObservation('00','1','lab'),))
        self.assertEqual(result.stop_reason, 'no_compatible_candidate')

    def test_shared_budget_accumulates_and_never_exceeds_limit(self):
        budget = SearchWorkBudget(10000)
        result = self.search.search(self.data, budget=budget)
        snapshot = result.work
        self.search.next_experiment(budget=budget)
        self.assertGreater(budget.work.total, snapshot.total)
        self.assertGreater(budget.work.pair_checks, 0)
        self.assertEqual(snapshot, result.work)
        baseline = self.search.search(self.data)
        for limit in (0, 1, 30, 100, baseline.work.total-1, baseline.work.total):
            result = self.search.search(self.data, budget=SearchWorkBudget(limit))
            self.assertLessEqual(result.work.total, limit)
            if result.stop_reason == 'work_budget_exhausted':
                self.assertEqual(result.work.total, limit)
            self.assertNotIn('xz', result.pruned+result.rejected)
            self.assertEqual(set(result.compatible+result.pruned+result.rejected+result.undecided), {'x','z','xz'})

    def test_completed_rejection_survives_interrupted_learning(self):
        # Stop immediately after the first replay, at entry into conflict learning.
        baseline = self.search.search(self.data, max_replays=0)
        # baseline has evaluated all three candidate dispatches; allow only the first.
        limit = baseline.work.total - 2
        result = self.search.search(self.data, budget=SearchWorkBudget(limit))
        self.assertEqual(result.rejected, ('x',))
        self.assertEqual(result.conflicts, ())
        self.assertEqual(set(result.undecided), {'z','xz'})
        self.assertEqual(result.stop_reason, 'work_budget_exhausted')

    def test_cancellation_is_sticky_and_does_not_turn_into_failure(self):
        signal = [True]
        budget = SearchWorkBudget(cancelled=lambda: signal[0])
        result = self.search.search(self.data, budget=budget)
        self.assertEqual(result.stop_reason, 'cancelled')
        self.assertFalse(result.determined)
        signal[0] = False
        with self.assertRaises(SearchBudgetExceeded) as caught:
            self.search.next_experiment(budget=budget)
        self.assertEqual(caught.exception.reason, 'cancelled')
        self.assertEqual(caught.exception.work.total, 0)
        # Cancellation after sufficiency has the same preservation semantics.
        sufficient = self.search.verify_macro(self.certificate, self.data, check_minimality=False)
        checks = [0]
        def cancel_after_sufficiency():
            checks[0] += 1
            return checks[0] > sufficient.work.total
        result = self.search.verify_macro(self.certificate, self.data,
                                         budget=SearchWorkBudget(cancelled=cancel_after_sufficiency))
        self.assertEqual(result.sufficiency, 'valid')
        self.assertEqual(result.stop_reason, 'cancelled')

    def test_legacy_operations_raise_instead_of_returning_false_conclusions(self):
        conflict = self.search.learn_conflict(('additive',), self.data)
        operations = (
            lambda b: self.search.learn_conflict(('additive',),self.data,budget=b),
            lambda b: self.search.validates_conflict(conflict,self.data,budget=b),
            lambda b: self.search.compress_evidence(self.data,budget=b),
            lambda b: self.search.partition(budget=b),
            lambda b: self.search.macro_identifiable(budget=b),
            lambda b: self.search.irreducible_against("xz",("x", "z"),budget=b),
            lambda b: self.search.next_experiment(budget=b),
        )
        for operation in operations:
            with self.assertRaises(SearchBudgetExceeded):
                operation(SearchWorkBudget(0))

    def test_invalid_claim_and_no_optimality_claim(self):
        larger = replace(self.certificate,retained_evidence=self.data)
        result = self.search.verify_macro(larger,self.data)
        self.assertEqual(result.sufficiency,'valid')
        self.assertEqual(result.minimality,'invalid')
        self.assertEqual(result.stop_reason,'smaller_basis_found')
        bounded = self.search.compress_evidence(self.data,max_subsets=0)
        result = self.search.verify_macro(bounded,self.data,max_subsets=0)
        self.assertEqual(result.minimality,'not_claimed')
        self.assertTrue(result.valid)
        # Exact subset boundary: the count is a cap, not a premature timeout.
        result = self.search.verify_macro(self.certificate,self.data)
        exact = self.search.verify_macro(self.certificate,self.data,max_subsets=result.subsets_checked)
        self.assertTrue(exact.valid)

    def test_budget_inputs(self):
        for value in (-1, True, 1.5):
            with self.assertRaises(ValueError): SearchWorkBudget(value)
        with self.assertRaises(TypeError): SearchWorkBudget(cancelled=True)
        with self.assertRaises(ValueError): SearchWorkBudget().consume('invented')
        with self.assertRaises(ValueError): self.search.verify_macro(self.certificate,self.data,max_subsets=-1)


if __name__ == '__main__':
    unittest.main()
