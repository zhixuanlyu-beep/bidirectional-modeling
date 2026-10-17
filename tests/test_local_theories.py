"""Finite local-to-global contracts and complete base-path preservation."""
from dataclasses import replace
from itertools import product
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from bidirectional_modeling import computation_search as cs
from bidirectional_modeling.extensions import local_theories as lt
from bidirectional_modeling.search import SearchWorkBudget


def program(*code):
    return cs.BitProgram(code + ('halt',))


def theory(task, name, rows, witness):
    return lt.LocalBitTheory(name, task.scope, task.width,
        tuple(cs.BitEvidence(x, y, 'fixture:' + name) for x, y in rows), witness)


class LocalTheoryContracts(unittest.TestCase):
    def setUp(self):
        self.task = cs.FiniteBitTask('AND fixture', 2, (0, 0, 0, 1))
        self.left = theory(self.task, 'first-zero', self.task.cases[:2], program('zero'))
        self.right = theory(self.task, 'first-one', self.task.cases[2:], program('input:1'))
        self.theories = (self.left, self.right)
        self.program = program('input:0', 'input:1', 'and')

    def certify(self, **kwargs):
        return lt.certify_local_cover(self.task, self.theories, self.program,
                                      max_cost=9, **kwargs)

    def test_complete_cover_extraction_and_independent_review(self):
        report = self.certify()
        self.assertEqual(report.status, 'valid')
        certificate = report.certificate
        self.assertEqual(certificate.cost, 9)
        self.assertEqual(certificate.local_costs, (('first-zero', 5), ('first-one', 5)))
        with patch.object(lt, 'certify_local_cover', side_effect=AssertionError):
            self.assertEqual(lt.verify_local_cover(self.task, self.theories, certificate), 'valid')
        # Compatible overlaps are allowed; finite gluing is not a disjoint-only partition.
        overlap = theory(self.task, 'overlap', self.task.cases[1:3], program('zero'))
        full = self.theories + (overlap,)
        cert = lt.certify_local_cover(self.task, full, self.program, max_cost=9).certificate
        self.assertEqual(lt.verify_local_cover(self.task, full, cert), 'valid')

    def test_coverage_overlap_target_and_extraction_are_distinct(self):
        missing = lt.certify_local_cover(self.task, (self.left,), self.program, max_cost=9)
        self.assertEqual((missing.status, missing.reason), ('invalid', 'incomplete_cover'))
        conflict = theory(self.task, 'conflict', (((0, 0), 1),), program('one'))
        bad = lt.certify_local_cover(self.task, self.theories + (conflict,), self.program, max_cost=9)
        self.assertEqual(bad.reason, 'overlap_conflict')
        wrong = theory(self.task, 'wrong', self.task.cases[2:], program('zero'))
        self.assertEqual(lt.certify_local_cover(self.task, (self.left, wrong),
            self.program, max_cost=9).reason, 'local_witness_mismatch')
        all_zero = theory(self.task, 'all-zero', tuple((x, 0) for x, _ in self.task.cases), program('zero'))
        self.assertEqual(lt.certify_local_cover(self.task, (all_zero,),
            self.program, max_cost=9).reason, 'target_mismatch')
        self.assertEqual(lt.certify_local_cover(self.task, self.theories,
            program('input:0'), max_cost=9).reason, 'extraction_mismatch')
        self.assertEqual(lt.certify_local_cover(self.task, (), self.program,
            max_cost=9).reason, 'incomplete_cover')

    def test_every_budget_and_runtime_cutoff_stays_conservative(self):
        certificate = self.certify().certificate
        for n in range(10):
            expected = 'valid' if n >= 8 else 'undecided'
            report = self.certify(budget=SearchWorkBudget(n))
            self.assertEqual(report.status, expected)
            self.assertEqual(lt.verify_local_cover(self.task, self.theories, certificate,
                budget=SearchWorkBudget(n)), expected)
        for bound in range(10):
            report = lt.certify_local_cover(self.task, self.theories, self.program, max_cost=bound)
            self.assertEqual(report.status, 'valid' if bound >= 9 else 'undecided')
        loop = replace(self.left, witness=cs.BitProgram(('again',), (0,)))
        self.assertEqual(lt.certify_local_cover(self.task, (loop, self.right),
            self.program, max_cost=9).status, 'undecided')
        fault = cs.ComputationRun('execution_error', None, 0, 0, 0)
        with patch.object(lt, 'run_bit_program', return_value=fault):
            self.assertEqual(self.certify().status, 'undecided')
            self.assertEqual(lt.verify_local_cover(self.task, self.theories, certificate), 'undecided')
        # A global extraction can exhaust resources even after all local proofs pass.
        looping = cs.BitProgram(('again',), (0,))
        self.assertEqual(lt.certify_local_cover(self.task, self.theories,
            looping, max_cost=9).status, 'undecided')

    def test_forged_stale_and_incomplete_certificates_cannot_pass(self):
        cert = self.certify().certificate
        for forged in (None, replace(cert, task_fingerprint='other'),
                replace(cert, theories_fingerprint='other'), replace(cert, cost=True),
                replace(cert, cost=-1), replace(cert, cost=10), replace(cert, program='opaque'),
                replace(cert, program=program('zero')),
                replace(cert, local_costs=[]), replace(cert, local_costs=()),
                replace(cert, local_costs=(('other', 5), ('first-one', 5))),
                replace(cert, local_costs=(('first-zero', True), ('first-one', 5))),
                replace(cert, local_costs=(('first-zero', 6), ('first-one', 5))),
                replace(cert, local_costs=((), ('first-one', 5)))):
            self.assertEqual(lt.verify_local_cover(self.task, self.theories, forged), 'invalid')
        for forged in (replace(cert, cost=8), replace(cert,
                local_costs=(('first-zero', 4), ('first-one', 5)))):
            self.assertEqual(lt.verify_local_cover(self.task, self.theories, forged), 'undecided')
        changed = replace(self.left, cases=(replace(self.left.cases[0], source='new source'),)
                          + self.left.cases[1:])
        self.assertEqual(lt.verify_local_cover(self.task, (changed, self.right), cert), 'invalid')
        changed_task = replace(self.task, outputs=(0, 0, 0, 0))
        self.assertEqual(lt.verify_local_cover(changed_task, self.theories, cert), 'invalid')
        # Even recomputing a binding cannot make wrong local data or holes valid.
        incomplete = (self.left,)
        forged = replace(cert, theories_fingerprint=lt._binding(incomplete),
                         local_costs=(('first-zero', 5),))
        self.assertEqual(lt.verify_local_cover(self.task, incomplete, forged), 'invalid')
        wrong = replace(self.right, witness=program('zero'))
        forged = replace(cert, theories_fingerprint=lt._binding((self.left, wrong)))
        self.assertEqual(lt.verify_local_cover(self.task, (self.left, wrong), forged), 'invalid')

    def test_scope_index_is_local_and_hints_are_untrusted(self):
        library = lt.LocalTheoryLibrary(self.theories)
        unrelated = tuple(replace(self.left, name='unrelated-%d' % i,
                                  scope='other-%d' % i) for i in range(100))
        extended = lt.LocalTheoryLibrary(self.theories + unrelated)
        self.assertEqual(extended.activate(self.task), library.activate(self.task))
        self.assertEqual(extended.search(self.task, max_cost=9), library.search(self.task, max_cost=9))
        self.assertEqual(library.activate(replace(self.task, scope='unseen')), ())
        fallback = lt.LocalTheoryLibrary().search(self.task, max_cost=9)
        self.assertEqual(fallback, cs.search_computations(self.task, max_cost=9))
        # Local witnesses fit their patches, but fail the complete AND target.
        result = library.search(self.task, max_cost=9)
        self.assertEqual(result.certificate.cost, 9)
        self.assertEqual(cs.verify_computation_certificate(self.task, (),
            result.certificate).optimality, 'verified')
        self.assertTrue(any(c.program == self.left.witness for c in result.counterexamples))

    def test_fair_base_path_and_all_small_targets(self):
        hints = (program('zero'), program('input:0'), program('one'))
        for bound in range(10):
            base = tuple(cs.enumerate_bit_programs(1, bound))
            guided = tuple(cs._guided_programs(1, bound, hints))
            self.assertLessEqual(len(guided), 2 * len(base))
            # Every base candidate occurs, in its original order, as a subsequence.
            cursor = iter(guided)
            for p in base:
                self.assertTrue(any(q == p for q in cursor))
        for outputs in product((0, 1), repeat=2):
            task = cs.FiniteBitTask('all unary targets', 1, outputs)
            result = cs.search_computations(task, max_cost=7, hints=hints + hints)
            plain = cs.search_computations(task, max_cost=7)
            self.assertEqual(result.certificate.cost, plain.certificate.cost)
            self.assertEqual(cs.verify_computation_certificate(task, (), result.certificate).optimality,
                             'verified')
            for limit in range(result.trials):
                self.assertEqual(cs.search_computations(task, max_cost=7, hints=hints,
                    max_trials=limit).status, 'unknown')
        task = cs.FiniteBitTask('one bit', 1, (0, 1))
        self.assertEqual(cs.search_computations(task, max_cost=3, hints=hints).status,
                         'absent_within_bound')

    def test_translation_checks_behavior_and_cost_without_optimality(self):
        task = cs.FiniteBitTask('constant', 1, (0, 0))
        original, short = program('zero'), cs.BitProgram(('halt',), (0,))
        result = lt.verify_cost_preserving_translation(task, original, short, max_cost=9)
        self.assertEqual((result.status, result.original_cost, result.translated_cost), ('valid', 5, 4))
        self.assertEqual(lt.verify_cost_preserving_translation(task, short, original,
            max_cost=9).reason, 'cost_increased')
        self.assertEqual(lt.verify_cost_preserving_translation(task, short, program('one'),
            max_cost=9).reason, 'behavior_changed')
        for n in range(5):
            self.assertEqual(lt.verify_cost_preserving_translation(task, original, short,
                max_cost=9, budget=SearchWorkBudget(n)).status, 'valid' if n >= 4 else 'undecided')
        for bound in range(6):
            self.assertEqual(lt.verify_cost_preserving_translation(task, original, short,
                max_cost=bound).status, 'valid' if bound >= 5 else 'undecided')
        zero_width = cs.FiniteBitTask('nullary', 0, (0,))
        local = theory(zero_width, 'singleton', zero_width.cases, short)
        cert = lt.certify_local_cover(zero_width, (local,), short, max_cost=4).certificate
        self.assertEqual(lt.verify_local_cover(zero_width, (local,), cert), 'valid')

    def test_small_covers_against_plain_function_reference(self):
        functions = {
            (0, 0): cs.BitProgram(('halt',), (0,)),
            (0, 1): program('input:0'),
            (1, 0): program('input:0', 'not'),
            (1, 1): cs.BitProgram(('halt',), (1,)),
        }
        choices = tuple(product(range(1, 4), (0, 1)))
        for target, extracted in product(functions, repeat=2):
            task = cs.FiniteBitTask('finite reference', 1, target)
            for (mask_a, bit_a), (mask_b, bit_b) in product(choices, repeat=2):
                patches = []
                rows = []
                for name, mask, bit in (('a', mask_a, bit_a), ('b', mask_b, bit_b)):
                    local_rows = tuple(((i,), bit) for i in (0, 1) if mask & (1 << i))
                    rows.extend(local_rows)
                    patches.append(theory(task, name, local_rows, functions[(bit, bit)]))
                reference = ({x for x, _ in rows} == {(0,), (1,)}
                             and all(target[x[0]] == y for x, y in rows)
                             and target == extracted)
                report = lt.certify_local_cover(task, patches, functions[extracted], max_cost=7)
                self.assertEqual(report.status == 'valid', reference)
                if reference:
                    self.assertEqual(lt.verify_local_cover(task, patches, report.certificate), 'valid')

    def test_strict_declarations_and_optional_import(self):
        for change in ({'name': ''}, {'scope': ''}, {'width': True}, {'cases': ()},
                {'cases': (None,)}, {'cases': self.left.cases * 2},
                {'width': 1}, {'witness': program('and')}, {'cases': set(self.left.cases)}):
            with self.assertRaises((TypeError, ValueError)):
                replace(self.left, **change)
        for declarations in ((self.left, self.left), (None,), {self.left}):
            with self.assertRaises((TypeError, ValueError)):
                lt.LocalTheoryLibrary(declarations)
        with self.assertRaises(ValueError):
            lt.certify_local_cover(replace(self.task, scope='new'), self.theories,
                                  self.program, max_cost=9)
        for fn in (lambda: lt.LocalTheoryLibrary().activate(None),
                   lambda: lt.certify_local_cover(None, (), self.program, max_cost=9),
                   lambda: lt.verify_cost_preserving_translation(None, self.program,
                                                               self.program, max_cost=9),
                   lambda: cs.search_computations(self.task, max_cost=9, hints=(None,)),
                   lambda: cs.search_computations(self.task, max_cost=9, hints={self.program})):
            with self.assertRaises((TypeError, ValueError)):
                fn()
        code = ('import sys,json; import bidirectional_modeling; '
                'print(json.dumps("bidirectional_modeling.extensions.local_theories" in sys.modules))')
        env = dict(os.environ, PYTHONPATH=os.path.abspath('src'))
        self.assertFalse(json.loads(subprocess.check_output([sys.executable, '-c', code], env=env)))


if __name__ == '__main__':
    unittest.main()
