"""Regressions for finite proof work and explicit claim boundaries."""
from dataclasses import replace
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
from types import SimpleNamespace
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
import unittest

from bidirectional_modeling import ConstraintQuery, SearchWorkBudget
from bidirectional_modeling.search_examples import conflict_search_scenario
from bidirectional_modeling.search_queries import verify_query_result
from bidirectional_modeling.macro_certificates import certify_macro_sufficiency, verify_macro_sufficiency
from bidirectional_modeling.extensions.gluing import (
    GluingProblem, LocalDescription, solve_gluing, verify_gluing_report)
from test_gluing import triangle


def agreeing_patches():
    return GluingProblem((('x', ('0',)),), tuple(
        LocalDescription(str(i), ('x',), (('0',),)) for i in range(32)))


class GluingClaimBoundaries(unittest.TestCase):
    def test_existence_and_replay_need_no_overlap_computation(self):
        problem = agreeing_patches()
        budget = SearchWorkBudget(33)
        report = solve_gluing(problem, budget=budget)
        self.assertEqual(report.status, 'found')
        self.assertEqual(report.reason, 'global_assignment_found')
        self.assertIsNone(report.overlap_consistent)
        self.assertFalse(report.core_minimal)
        self.assertEqual(budget.work.total, 33)
        unlimited = SearchWorkBudget()
        solve_gluing(problem, budget=unlimited)
        self.assertEqual(unlimited.work.total, 33)
        self.assertEqual(verify_gluing_report(problem, report, budget=SearchWorkBudget(34)), 'valid')
        self.assertEqual(verify_gluing_report(problem, report, budget=SearchWorkBudget(33)), 'undecided')
        self.assertEqual(solve_gluing(problem, budget=SearchWorkBudget(32)).status, 'unknown')

    def test_extra_checks_preserve_already_proved_claims_when_interrupted(self):
        problem = agreeing_patches()
        report = solve_gluing(problem, check_overlap=True, budget=SearchWorkBudget(33))
        self.assertEqual(report.status, 'found')
        self.assertIsNone(report.overlap_consistent)
        self.assertEqual(verify_gluing_report(problem, report), 'valid')
        problem = triangle()
        base_budget = SearchWorkBudget()
        base = solve_gluing(problem, budget=base_budget)
        self.assertEqual(base.status, 'absent')
        for options in ({'check_overlap': True}, {'minimize_core': True},
                        {'check_overlap': True, 'minimize_core': True}):
            report = solve_gluing(problem, budget=SearchWorkBudget(base_budget.work.total), **options)
            self.assertEqual(report.status, 'absent')
            self.assertFalse(report.core_minimal)
            self.assertEqual(verify_gluing_report(problem, report), 'valid')
        with self.assertRaises(ValueError): solve_gluing(problem, check_overlap=1)

    def test_optional_properties_are_independently_rechecked(self):
        problem = triangle()
        report = solve_gluing(problem, check_overlap=True, minimize_core=True)
        self.assertTrue(report.overlap_consistent and report.core_minimal)
        self.assertEqual(verify_gluing_report(problem, report), 'valid')
        self.assertEqual(verify_gluing_report(problem,
            replace(report, overlap_consistent=False)), 'invalid')
        redundant = replace(problem, locals=problem.locals + (
            LocalDescription('extra', ('x',), (('0',), ('1',))),))
        base = solve_gluing(redundant)
        self.assertEqual(verify_gluing_report(redundant,
            replace(base, core_minimal=True)), 'invalid')


class ReceiptShapeBoundaries(unittest.TestCase):
    def test_malformed_receipts_are_rejected_before_proof_work(self):
        search, evidence = conflict_search_scenario()
        query = ConstraintQuery()
        receipt = search.query(query)
        macro = certify_macro_sufficiency(search, evidence).certificate
        problem = agreeing_patches()
        gluing = solve_gluing(problem)
        cases = (
            (lambda value, budget: verify_query_result(search, query, value, budget=budget).status,
             (None, replace(receipt, scope=[]), replace(receipt, witness_candidate=[]),
              replace(receipt, work=None), replace(receipt, witness_world=True))),
            (lambda value, budget: verify_macro_sufficiency(search, value, evidence, budget=budget),
             (None, replace(macro, exclusions=(('x',),)), replace(macro, evidence=(None,)),
              replace(macro, problem_fingerprint=[]), replace(macro, exclusions=(('x', []),)))),
            (lambda value, budget: verify_gluing_report(problem, value, budget=budget),
             (None, replace(gluing, conflict_core=None), replace(gluing, witness=([],)),
              replace(gluing, core_minimal='yes'), replace(gluing, overlap_consistent=1))),
        )
        for verify, receipts in cases:
            for malformed in receipts:
                with self.subTest(receipt=malformed):
                    budget = SearchWorkBudget(0)
                    self.assertEqual(verify(malformed, budget), 'invalid')
                    self.assertEqual(budget.work.total, 0)


class MutationBaselineBoundaries(unittest.TestCase):
    def test_new_target_failure_stops_before_any_mutation(self):
        path = Path(__file__).resolve().parents[1] / 'tools' / 'check_concept_mutations.py'
        spec = spec_from_file_location('mutation_runner', path)
        runner = module_from_spec(spec)
        spec.loader.exec_module(runner)
        targets = ('new.first', 'new.second', 'new.first')
        mutations = tuple(('label', 'file', 'before', 'after', target) for target in targets)
        calls = []
        def fake_run(source, target, cwd):
            calls.append(target)
            return SimpleNamespace(returncode=0 if target == targets[0] else 1,
                                   stderr='FAIL: already broken')
        output = StringIO()
        with patch.object(runner, 'MUTATIONS', mutations), patch.object(runner, 'run', fake_run), \
                patch.object(runner.shutil, 'copytree', side_effect=AssertionError('must not mutate')), \
                redirect_stdout(output):
            self.assertEqual(runner.main(), 1)
        self.assertEqual(calls, ['new.first', 'new.second'])
        self.assertIn('BASELINE FAILED: new.second', output.getvalue())
        self.assertNotIn('DETECTED:', output.getvalue())
