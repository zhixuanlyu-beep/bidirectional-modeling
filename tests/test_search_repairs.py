"""Feasible repairs, all smallest repairs, and conflicts make different claims."""
from dataclasses import replace
import unittest

from bidirectional_modeling import (ExperimentHypothesisSearch, ResponseConstraint,
    SearchExperiment, SearchObservation, SearchProtocol, SearchWorkBudget)
from bidirectional_modeling.search_repairs import (
    suggest_consistency_repairs, verify_consistency_repairs,
)


def fixture():
    protocol = SearchProtocol('scope', 'code',
        (SearchExperiment('a', 'read a'), SearchExperiment('b', 'read b')),
        (('0','0'), ('0','1'), ('1','0'), ('1','1')), (
            ResponseConstraint('u', (0, 2)),
            ResponseConstraint('v', (1, 2)),
            ResponseConstraint('neutral', (0, 1, 2, 3)),
        ))
    return ExperimentHypothesisSearch(protocol, (), 'out'), (SearchObservation('a', '0', 'lab'),)


class RepairContracts(unittest.TestCase):
    def test_core_and_all_smallest_repairs_are_separate(self):
        problem, evidence = fixture()
        result = suggest_consistency_repairs(problem,
            ('u', 'v', 'neutral'), evidence)
        self.assertEqual(result.status, 'verified')
        self.assertEqual(set(result.certificate.core.commitments), {'u', 'v'})
        self.assertEqual(result.certificate.retractions, (('u',), ('v',)))
        self.assertEqual(verify_consistency_repairs(problem, result.certificate, evidence), 'valid')
        self.assertEqual(verify_consistency_repairs(problem,
            replace(result.certificate, retractions=(('u',),)), evidence), 'invalid')
        self.assertEqual(verify_consistency_repairs(problem,
            replace(result.certificate, retractions=(('u','v'),)), evidence), 'invalid')
        self.assertEqual(verify_consistency_repairs(problem, result.certificate, evidence,
            budget=SearchWorkBudget(0)), 'undecided')

    def test_budget_never_promotes_one_feasible_repair_to_optimum(self):
        problem, evidence = fixture()
        partial = suggest_consistency_repairs(problem, ('u','v','neutral'), evidence,
            max_subsets=1)
        self.assertEqual(partial.status, 'undecided')
        self.assertIsNone(partial.certificate)
        self.assertEqual(partial.feasible_retractions, (('u',),))
        self.assertEqual(suggest_consistency_repairs(problem, ('u','v','neutral'), evidence,
            budget=SearchWorkBudget(0)).status, 'undecided')

    def test_binding_retraction_and_independent_problem_scope(self):
        problem, evidence = fixture()
        cert = suggest_consistency_repairs(problem, ('u','v'), evidence).certificate
        self.assertEqual(verify_consistency_repairs(problem, cert, ()), 'invalid')
        changed = (replace(evidence[0], source='another lab'),)
        self.assertEqual(verify_consistency_repairs(problem, cert, changed), 'invalid')
        other = ExperimentHypothesisSearch(replace(problem.protocol, scope='new'), (), 'out')
        self.assertEqual(verify_consistency_repairs(other, cert, evidence), 'invalid')
        self.assertEqual(suggest_consistency_repairs(problem, ('u','v'), ()).status,
                         'already_consistent')
        inconsistent = (evidence[0], replace(evidence[0], response='1'))
        self.assertEqual(suggest_consistency_repairs(problem, ('u','v'), inconsistent).status,
                         'not_applicable')

    def test_fixing_an_inconsistent_commitment_without_data_is_a_separate_task(self):
        problem, evidence = fixture()
        changed = replace(problem.protocol, constraints=(
            ResponseConstraint('u', (0,)), ResponseConstraint('v', (1,)),
        ))
        other = ExperimentHypothesisSearch(changed, (), 'out')
        self.assertEqual(suggest_consistency_repairs(other, ('u','v'), evidence).reason,
                         'commitments_inconsistent_alone')
        with self.assertRaises(ValueError):
            suggest_consistency_repairs(problem, ('u','u'), evidence)
