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


class AntichainCostTests(unittest.TestCase):
    def test_incomparable_mixed_and_small_catalogues_have_bounded_extra_work(self):
        from importlib.util import spec_from_file_location, module_from_spec
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / 'benchmarks' / 'evidence_update_costs.py'
        spec = spec_from_file_location('evidence_costs', path)
        benchmark = module_from_spec(spec)
        spec.loader.exec_module(benchmark)
        rows = benchmark.run()['results']
        for row in rows:
            if row.get('shape') == 'incomparable' and row['candidates'] == 1:
                self.assertEqual(row['full_screen_work']['certificate_checks'], 128)
                self.assertEqual(row['full_screen_work'], row['direct_screen_work'])
        self.assertEqual({r['shape'] for r in rows if 'shape' in r}, {'incomparable', 'mixed'})


    def test_fallback_keeps_late_live_exclusion_and_withdrawal(self):
        protocol = SearchProtocol('fallback', 'table', (SearchExperiment('a', 'read'),),
            (('0',), ('1',)), tuple(ResponseConstraint('c%d' % i, (0,)) for i in range(64)))
        candidate = SearchHypothesis('last', 0, 'answer', DescriptionLength(), ('c63',))
        problem = ExperimentHypothesisSearch(protocol, (candidate,), 'out')
        evidence = tuple(SearchObservation('a', '1', str(i)) for i in range(64))
        certificates = tuple(ConflictCertificate(protocol.fingerprint, ('c%d' % i,), (evidence[i],))
                             for i in range(64))
        report = problem.search(evidence, certificates, learn_conflicts=False)
        self.assertEqual(report.pruned, ('last',))
        self.assertEqual(report.conflicts, certificates)
        withdrawn = problem.search(evidence[:-1], certificates, learn_conflicts=False)
        self.assertEqual(withdrawn.pruned, ())
        self.assertEqual(withdrawn.rejected, ('last',))
