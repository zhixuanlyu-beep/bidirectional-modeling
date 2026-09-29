"""Independent tiny-world oracle for conflict, exclusion and repair claims.

The oracle uses ordinary set intersections over a declared table. It never
calls the searcher's private world filtering, certificate or repair helpers.
"""
from dataclasses import replace
from itertools import combinations, product
import unittest

from bidirectional_modeling import (DescriptionLength, ExperimentHypothesisSearch,
    ResponseConstraint, SearchExperiment, SearchHypothesis, SearchObservation,
    SearchProtocol, SearchWorkBudget)
from bidirectional_modeling.search_repairs import (
    suggest_consistency_repairs, verify_consistency_repairs,
)


def reference_worlds(rows, constraints, commitments=(), observations=()):
    return {i for i, row in enumerate(rows)
            if all(i in constraints[name] for name in commitments)
            and all(row[('a', 'b').index(obs.experiment)] == obs.response
                    for obs in observations)}


def reference_repairs(rows, constraints, names, observations):
    for size in range(1, len(names) + 1):
        repairs = tuple(removed for removed in combinations(names, size)
            if reference_worlds(rows, constraints,
                tuple(name for name in names if name not in removed), observations))
        if repairs:
            return repairs
    raise AssertionError('evidence-only consistency ensures a repair')


class TinyRelationReference(unittest.TestCase):
    def test_all_small_two_commitment_domains_agree_with_independent_sets(self):
        rows = (('0', '0'), ('0', '1'), ('1', '0'))
        names = ('A', 'B')
        observations = tuple(SearchObservation(experiment, response, 'lab')
            for experiment, response in product(('a', 'b'), ('0', '1')))
        for a_mask, b_mask in product(range(8), repeat=2):
            allowed = {name: {i for i in range(3) if mask & (1 << i)}
                for name, mask in zip(names, (a_mask, b_mask))}
            protocol = SearchProtocol('bounded', 'table',
                (SearchExperiment('a', 'read a'), SearchExperiment('b', 'read b')),
                rows, tuple(ResponseConstraint(name, tuple(sorted(allowed[name])))
                            for name in names))
            hypotheses = tuple(SearchHypothesis(name, None, 'out', DescriptionLength(), (name,))
                for name in names) + (
                    SearchHypothesis('joint', None, 'out', DescriptionLength(), names),)
            problem = ExperimentHypothesisSearch(protocol, hypotheses, 'out')
            for observation in observations:
                evidence = (observation,)
                with self.subTest(masks=(a_mask, b_mask), observation=observation):
                    data_ok = bool(reference_worlds(rows, allowed, observations=evidence))
                    rules_ok = bool(reference_worlds(rows, allowed, names))
                    joint_ok = bool(reference_worlds(rows, allowed, names, evidence))
                    empirical_conflict = data_ok and rules_ok and not joint_ok
                    certificate = problem.learn_conflict(names, evidence)
                    self.assertEqual(certificate is not None, empirical_conflict)
                    repair = suggest_consistency_repairs(problem, names, evidence)
                    if empirical_conflict:
                        self.assertTrue(problem.validates_conflict(certificate, evidence))
                        self.assertTrue(set(certificate.commitments) <= set(names))
                        self.assertEqual(repair.certificate.retractions,
                            reference_repairs(rows, allowed, names, evidence))
                        self.assertEqual(verify_consistency_repairs(problem,
                            repair.certificate, evidence), 'valid')
                        report = problem.search(evidence, (certificate,), learn_conflicts=False)
                        expected_pruned = {h.name for h in hypotheses
                            if set(certificate.commitments) <= set(h.commitments)}
                        self.assertEqual(set(report.pruned), expected_pruned)
                        self.assertFalse(problem.validates_conflict(certificate, ()))
                        changed = ExperimentHypothesisSearch(replace(protocol, scope='different'),
                            hypotheses, 'out')
                        self.assertFalse(changed.validates_conflict(certificate, evidence))
                    else:
                        self.assertIn(repair.status,
                            ('already_consistent', 'not_applicable'))

    def test_no_budget_or_self_contradiction_promotes_a_conflict(self):
        rows = (('0', '0'), ('1', '1'))
        protocol = SearchProtocol('bounded', 'table',
            (SearchExperiment('a', 'a'), SearchExperiment('b', 'b')), rows,
            (ResponseConstraint('A', (0,)), ResponseConstraint('B', (1,))))
        problem = ExperimentHypothesisSearch(protocol, (), 'out')
        evidence = (SearchObservation('a', '1', 'lab'),)
        self.assertEqual(suggest_consistency_repairs(problem, ('A', 'B'), evidence).reason,
                         'commitments_inconsistent_alone')
        self.assertEqual(suggest_consistency_repairs(problem, ('A',), evidence,
            budget=SearchWorkBudget(0)).status, 'undecided')
