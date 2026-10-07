"""C-FOUND/C-ABSENT: generation faults must not become accepted proof claims."""
from dataclasses import replace
from itertools import product
import unittest
from unittest.mock import patch

from bidirectional_modeling import (ConstraintQuery, DescriptionLength, ExperimentHypothesisSearch,
    LowerSubstituteQuery, MacroAlternativeQuery, QueryStatus, ResponseConstraint, SearchExperiment,
    SearchHypothesis, SearchObservation, SearchProtocol, SearchWorkBudget)
from bidirectional_modeling.search_queries import FiniteSearchQueryBackend, verify_query_result


class IndependentQueryReview(unittest.TestCase):
    def problem(self):
        protocol = SearchProtocol('tiny', 'table', (SearchExperiment('x', 'read'),),
            (('0',), ('1',)), (ResponseConstraint('zero', (0,)),))
        candidates = tuple(SearchHypothesis(str(i), i, str(i), DescriptionLength()) for i in range(2))
        return ExperimentHypothesisSearch(protocol, candidates, 'out')

    def test_review_does_not_call_generator_matcher_or_filters(self):
        problem = self.problem()
        queries = (ConstraintQuery(('zero',), (SearchObservation('x', '1', 'lab'),)),
                   MacroAlternativeQuery('0', (SearchObservation('x', '0', 'lab'),)),
                   LowerSubstituteQuery('0', ('1',)))
        for query in queries:
            receipt = problem.query(query)
            with patch.object(FiniteSearchQueryBackend, '_execute', side_effect=AssertionError('generator used')), \
                 patch('bidirectional_modeling.search_queries._world_matcher', side_effect=AssertionError('matcher used')), \
                 patch.object(ExperimentHypothesisSearch, '_worlds', side_effect=AssertionError('filter used')), \
                 patch.object(ExperimentHypothesisSearch, '_evidence', side_effect=AssertionError('snapshot used')):
                self.assertEqual(verify_query_result(problem, query, receipt).status, 'valid')
        witness_query = ConstraintQuery(('zero',))
        receipt = problem.query(witness_query)
        with patch('bidirectional_modeling.search_queries._world_matcher', side_effect=AssertionError('matcher used')):
            self.assertEqual(verify_query_result(problem, witness_query, receipt).status, 'valid')

    def test_faulty_generator_cannot_establish_absence_or_false_witness(self):
        problem = self.problem()
        query = ConstraintQuery(('zero',))
        with patch('bidirectional_modeling.search_queries._world_matcher', return_value=lambda _: False):
            forged = problem.query(query)
            self.assertEqual(forged.status, QueryStatus.ABSENT)
            self.assertEqual(verify_query_result(problem, query, forged).status, 'invalid')
        query = ConstraintQuery(('zero',), (SearchObservation('x', '1', 'lab'),))
        with patch('bidirectional_modeling.search_queries._world_matcher', return_value=lambda _: True):
            forged = problem.query(query)
            self.assertEqual(forged.status, QueryStatus.FOUND)
            self.assertEqual(verify_query_result(problem, query, forged).status, 'invalid')

    def test_absence_metadata_and_unknowns_against_independent_catalogue(self):
        for first, second in product((None, 0, 1), repeat=2):
            problem = self.problem().with_hypotheses(tuple(SearchHypothesis(str(i), world, str(i),
                DescriptionLength()) for i, world in enumerate((first, second))))
            for query in (MacroAlternativeQuery('0'), LowerSubstituteQuery('0', ('1',)),
                          LowerSubstituteQuery('0', ())):
                receipt = problem.query(query)
                if receipt.status is QueryStatus.UNKNOWN:
                    self.assertEqual(verify_query_result(problem, query, receipt).status, 'undecided')
                    forged = replace(receipt, status=QueryStatus.ABSENT)
                    self.assertEqual(verify_query_result(problem, query, forged).status, 'undecided')
                else:
                    self.assertEqual(verify_query_result(problem, query, receipt).status, 'valid')
                    full = verify_query_result(problem, query, receipt)
                    for limit in range(full.work.total):
                        self.assertEqual(verify_query_result(problem, query, receipt,
                            budget=SearchWorkBudget(limit)).status, 'undecided')

    def test_forged_absence_with_bad_evidence_is_rejected(self):
        from bidirectional_modeling.search_queries import query_fingerprint
        problem = self.problem()
        receipt = problem.query(ConstraintQuery(('zero',), (SearchObservation('x', '1', 'lab'),)))
        for evidence in ((SearchObservation('outside', '0', 'lab'),),
                         (SearchObservation('x', 'outside', 'lab'),)):
            query = ConstraintQuery(evidence=evidence)
            forged = replace(receipt, query_fingerprint=query_fingerprint(problem, query))
            with self.assertRaises(ValueError):
                verify_query_result(problem, query, forged)
