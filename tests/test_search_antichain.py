"""Snapshot screening must not destroy independently supported certificates."""
from itertools import permutations
import unittest

from bidirectional_modeling import (DescriptionLength, ExperimentHypothesisSearch,
    ResponseConstraint, SearchExperiment, SearchHypothesis, SearchObservation,
    SearchProtocol, SearchWorkBudget)
from bidirectional_modeling.search import ConflictCertificate
from bidirectional_modeling.search_session import SearchSession


def fixture():
    p = SearchProtocol('finite', 'table', (SearchExperiment('a', 'read'),),
        (('0',), ('1',)), (ResponseConstraint('A', (0,)), ResponseConstraint('B', (0,))))
    candidates = tuple(SearchHypothesis(name, 0 if core else 1, 'answer',
        DescriptionLength(), core) for name, core in
        (('a', ('A',)), ('b', ('B',)), ('ab', ('A', 'B')), ('free', ())))
    problem = ExperimentHypothesisSearch(p, candidates, 'out')
    evidence = tuple(SearchObservation('a', '1', source) for source in ('x', 'y', 'z'))
    certificates = tuple(ConflictCertificate(p.fingerprint, core, (obs,)) for core, obs in
        zip((('A',), ('A', 'B'), ('B',)), evidence))
    return problem, evidence, certificates


class AntichainTests(unittest.TestCase):
    def test_every_order_and_withdrawal_agree_with_all_live_cones(self):
        problem, evidence, certificates = fixture()
        for order in permutations(certificates):
            for withdrawn in ((), (0,), (2,), (0, 2)):
                live = tuple(o for i, o in enumerate(evidence) if i not in withdrawn)
                expected = tuple(c for c in order if set(c.evidence) <= set(live))
                report = problem.search(live, order, learn_conflicts=False)
                self.assertEqual(report.conflicts, expected)
                pruned = {h.name for h in problem.hypotheses if any(
                    set(c.commitments) <= set(h.commitments) for c in expected)}
                self.assertEqual(set(report.pruned), pruned)
                self.assertEqual(report.compatible, ('free',))
        session = SearchSession(problem, evidence, certificates)
        session.run()
        self.assertEqual(set(session.certificates), set(certificates))
        session.replace_evidence((evidence[1],))
        self.assertEqual(session.certificates, (certificates[1],))
        self.assertEqual(session.run().pruned, ('ab',))

    def test_interrupted_antichain_never_invents_an_exclusion(self):
        problem, evidence, certificates = fixture()
        full = problem.search(evidence, certificates, learn_conflicts=False)
        for limit in range(full.work.total + 1):
            report = problem.search(evidence, certificates, learn_conflicts=False,
                                    budget=SearchWorkBudget(limit))
            self.assertLessEqual(report.work.total, limit)
            self.assertLessEqual(set(report.pruned), set(full.pruned))
            self.assertLessEqual(set(report.compatible), set(full.compatible))
            self.assertEqual(report.rejected, ())
