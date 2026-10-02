"""Invocation-local evidence validation, including worst-position responses."""
from dataclasses import replace
from unittest.mock import patch
import unittest

from bidirectional_modeling import (ConstraintQuery, DescriptionLength, ExperimentHypothesisSearch,
    ResponseConstraint, SearchExperiment, SearchHypothesis, SearchObservation, SearchProtocol,
    SearchWorkBudget)
from bidirectional_modeling.search import SearchBudgetExceeded
from bidirectional_modeling.search_index import ResponseIndex
from bidirectional_modeling.search_queries import FiniteSearchQueryBackend, verify_query_result


def problem(size=64, backend='scan'):
    protocol = SearchProtocol('scope', 'code',
        (SearchExperiment('a', 'read'), SearchExperiment('b', 'read')),
        tuple((str(i), str(i)) for i in range(size)),
        (ResponseConstraint('all', tuple(range(size))), ResponseConstraint('first', (0,))))
    h = SearchHypothesis('zero', 0, 'yes', DescriptionLength(), ('all', 'first'))
    return ExperimentHypothesisSearch(protocol, (h,), 'out', backend=backend)


class EvidenceSnapshotCosts(unittest.TestCase):
    def compare(self, operation):
        optimized = SearchWorkBudget()
        result = operation(optimized)
        original = FiniteSearchQueryBackend._execute
        def repeated(backend, p, q, **kwargs):
            kwargs.pop('_evidence_snapshot', None)
            return original(backend, p, q, **kwargs)
        repeated_budget = SearchWorkBudget()
        with patch.object(FiniteSearchQueryBackend, '_execute', repeated):
            baseline = operation(repeated_budget)
        if hasattr(result, 'work'):
            self.assertEqual(replace(result, work=baseline.work), baseline)
        else:
            self.assertEqual(result, baseline)
        return optimized.work, repeated_budget.work

    def test_first_and_last_row_validation_is_done_once(self):
        p = problem()
        for row in (0, 63):
            data = (SearchObservation('a', str(row), 'lab'),)
            work, old = self.compare(lambda b: p.learn_conflict(('all',), data, budget=b))
            self.assertEqual(old.response_checks - work.response_checks, 2 * (row + 1))
            self.assertLess(work.total, old.total)
        with patch.object(ExperimentHypothesisSearch, '_evidence', autospec=True,
                          side_effect=ExperimentHypothesisSearch._evidence) as scans:
            p.learn_conflict(('all',), (SearchObservation('a', '63', 'lab'),))
        self.assertEqual(scans.call_count, 1)

    def test_joint_core_reduction_and_search_reuse_entry_validation(self):
        for backend in ('scan', 'indexed'):
            p = problem(backend=backend)
            if backend == 'indexed':
                p._index(SearchWorkBudget())
            data = tuple(SearchObservation(e, '63', 'lab') for e in ('a', 'b'))
            work, old = self.compare(lambda b: p.learn_conflict(('all', 'first'), data, budget=b))
            self.assertLess(work.total, old.total)
            certificate = p.learn_conflict(('all', 'first'), data)
            self.assertEqual(certificate.commitments, ('first',))
            self.assertEqual(len(certificate.evidence), 1)
            with patch.object(ExperimentHypothesisSearch, '_evidence', autospec=True,
                              side_effect=ExperimentHypothesisSearch._evidence) as scans:
                report = p.search(data)
            self.assertEqual(scans.call_count, 1)
            self.assertEqual(report.rejected, ('zero',))
            self.assertEqual(report.conflicts, (certificate,))
            self.assertTrue(p.validates_conflict(certificate, data))

    def test_independent_review_reads_original_declarations_and_retraction(self):
        p = problem(backend='indexed')
        data = (SearchObservation('a', '63', 'lab'),)
        cert = p.learn_conflict(('first',), data)
        receipt = p.query(ConstraintQuery(('first',), ()))
        with patch.object(ResponseIndex, 'allows', return_value=False), \
             patch.object(ResponseIndex, 'filter', side_effect=AssertionError('cached index used')):
            self.assertTrue(p.validates_conflict(cert, data))
            self.assertFalse(p.validates_conflict(cert, ()))
            self.assertEqual(verify_query_result(p, ConstraintQuery(('first',), ()), receipt).status, 'valid')
        for _ in range(2):
            b = SearchWorkBudget()
            p.validates_conflict(cert, data, budget=b)
            self.assertGreaterEqual(b.work.response_checks, 64)

    def test_cutoffs_keep_pending_work_undecided(self):
        p = problem(4)
        data = (SearchObservation('a', '3', 'lab'),)
        full = SearchWorkBudget()
        p.learn_conflict(('all', 'first'), data, budget=full)
        for limit in range(full.work.total):
            b = SearchWorkBudget(limit)
            with self.assertRaises(SearchBudgetExceeded):
                p.learn_conflict(('all', 'first'), data, budget=b)
            self.assertLessEqual(b.work.total, limit)
        b = SearchWorkBudget(3)
        r = p.search(data, budget=b)
        self.assertEqual(r.undecided, ('zero',))
        self.assertFalse(r.compatible or r.pruned or r.rejected)
        self.assertEqual(r.stop_reason, 'work_budget_exhausted')
        self.assertLessEqual(r.work.total, 3)
