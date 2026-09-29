"""Relations between transformed problems; no probabilistic interpretation."""
from dataclasses import replace
from itertools import combinations, permutations, product
import unittest

from bidirectional_modeling import (ConstraintQuery, DescriptionLength,
    ExperimentHypothesisSearch, QueryStatus, ResponseConstraint, SearchExperiment,
    SearchHypothesis, SearchObservation, SearchProtocol, SearchWorkBudget)
from bidirectional_modeling.search_queries import verify_query_result


def problem(prefix='', order=(0, 1, 2, 3), backend='scan'):
    rows = tuple(product(('0', '1'), repeat=2))
    protocol = SearchProtocol('finite', 'table',
        tuple(SearchExperiment(prefix + name, name) for name in ('a', 'b')), rows,
        (ResponseConstraint(prefix + 'c', (0, 1)),))
    hypotheses = tuple(SearchHypothesis(prefix + str(i), i, str(i % 2),
        DescriptionLength(), (prefix + 'c',) if i < 2 else ()) for i in order)
    return ExperimentHypothesisSearch(protocol, hypotheses, 'parity', backend=backend)


class SearchMetamorphicTests(unittest.TestCase):
    def test_renaming_and_permutation_preserve_complete_semantics(self):
        for row in product(('0', '1'), repeat=2):
            evidence = tuple(SearchObservation(e, r, 'lab') for e, r in zip(('a', 'b'), row))
            baseline = problem().search(evidence)
            for order in permutations(range(4)):
                for backend in ('scan', 'indexed'):
                    transformed = problem('renamed-', order, backend)
                    data = tuple(replace(o, experiment='renamed-' + o.experiment) for o in evidence)
                    report = transformed.search(data)
                    for field in ('compatible', 'undecided'):
                        self.assertEqual(set(getattr(baseline, field)),
                            {name.removeprefix('renamed-') for name in getattr(report, field)})
                    self.assertEqual(set(baseline.rejected + baseline.pruned),
                        {name.removeprefix('renamed-') for name in report.rejected + report.pruned})
                    self.assertEqual(set(baseline.answers), set(report.answers))
                    self.assertEqual(baseline.determined, report.determined)
                    self.assertNotEqual(problem().fingerprint, transformed.fingerprint)

    def test_added_observations_can_only_remove_compatible_candidates(self):
        observations = tuple(SearchObservation(e, r, 'lab')
            for e, r in product(('a', 'b'), ('0', '1')))
        for backend in ('scan', 'indexed'):
            search = problem(backend=backend)
            for size in range(len(observations) + 1):
                for evidence in combinations(observations, size):
                    before = set(search.search(evidence).compatible)
                    for extra in observations:
                        if extra not in evidence:
                            after = set(search.search(evidence + (extra,)).compatible)
                            self.assertLessEqual(after, before)

    def test_more_budget_preserves_decided_claims_from_fresh_state(self):
        for backend in ('scan', 'indexed'):
            for response in ('0', '1'):
                query = ConstraintQuery(('c',), (SearchObservation('a', response, 'lab'),))
                complete = problem(backend=backend).query(query)
                decided = []
                for limit in range(complete.work.total + 2):
                    search = problem(backend=backend)
                    receipt = search.query(query, budget=SearchWorkBudget(limit))
                    self.assertLessEqual(receipt.work.total, limit)
                    if decided:
                        self.assertEqual(receipt.status, complete.status)
                    if receipt.status is not QueryStatus.UNKNOWN:
                        self.assertEqual(receipt.status, complete.status)
                        self.assertEqual(verify_query_result(search, query, receipt).status, 'valid')
                        decided.append(receipt)
                self.assertTrue(decided)
