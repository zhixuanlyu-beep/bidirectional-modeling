import unittest
from dataclasses import replace

from bidirectional_modeling import (LocalDescription, GluingProblem, solve_gluing,
                                    verify_gluing_report, SearchWorkBudget)


def triangle():
    equal = (('0', '0'), ('1', '1'))
    unequal = (('0', '1'), ('1', '0'))
    return GluingProblem(tuple((n, ('0', '1')) for n in ('x', 'y', 'z')), (
        LocalDescription('xy', ('x', 'y'), equal), LocalDescription('yz', ('y', 'z'), equal),
        LocalDescription('xz', ('x', 'z'), unequal)))


class GluingTests(unittest.TestCase):
    def test_pairwise_overlap_does_not_guarantee_global_assignment(self):
        p = triangle()
        r = solve_gluing(p)
        self.assertTrue(r.overlap_consistent)
        self.assertEqual(r.status, 'absent')
        self.assertEqual(r.conflict_core, ('xy', 'yz', 'xz'))
        self.assertTrue(r.core_minimal)
        self.assertEqual(verify_gluing_report(p, r), 'valid')
        for patch in p.locals:
            q = replace(p, locals=tuple(x for x in p.locals if x != patch))
            self.assertEqual(solve_gluing(q).status, 'found')

    def test_witness_and_nonminimal_core_verification(self):
        p = triangle()
        q = replace(p, locals=p.locals[:2])
        r = solve_gluing(q)
        self.assertEqual(r.witness, ('0', '0', '0'))
        self.assertEqual(verify_gluing_report(q, r), 'valid')
        self.assertEqual(verify_gluing_report(q, replace(r, witness=('0', '1', '0'))), 'invalid')
        self.assertEqual(verify_gluing_report(p, r), 'invalid')
        r = solve_gluing(p, minimize_core=False)
        self.assertFalse(r.core_minimal)
        self.assertEqual(verify_gluing_report(p, r), 'valid')
        self.assertEqual(verify_gluing_report(p, r, budget=SearchWorkBudget(0)), 'undecided')

    def test_overlap_mismatch_and_empty_global_class(self):
        p = triangle()
        q = replace(p, locals=(LocalDescription('one', ('x',), (('0',),)),
                                LocalDescription('two', ('x',), (('1',),))))
        r = solve_gluing(q)
        self.assertFalse(r.overlap_consistent)
        self.assertEqual(r.status, 'absent')
        r = solve_gluing(replace(p, global_assignments=()))
        self.assertEqual(r.conflict_core, ())
        self.assertEqual(r.status, 'absent')

    def test_budget_never_turns_unknown_into_absence(self):
        p = triangle()
        r = solve_gluing(p, budget=SearchWorkBudget(0))
        self.assertEqual(r.status, 'unknown')
        self.assertIsNone(r.overlap_consistent)
        self.assertEqual(verify_gluing_report(p, r), 'undecided')
        for cap in range(70):
            r = solve_gluing(p, budget=SearchWorkBudget(cap))
            if r.status == 'absent':
                self.assertIsNone(r.witness)
                self.assertTrue(r.conflict_core)
                self.assertEqual(verify_gluing_report(p, r), 'valid')

    def test_input_validation(self):
        with self.assertRaises(ValueError):
            LocalDescription('p', ('x', 'x'), ())
        with self.assertRaises(ValueError):
            LocalDescription('p', ('x',), (('0', '1'),))
        with self.assertRaises(ValueError):
            replace(triangle(), global_assignments=(('bad', '0', '0'),))
        with self.assertRaises(ValueError):
            replace(triangle(), domains=(('x', ('0',)),))
        with self.assertRaises(ValueError):
            replace(triangle(), locals=triangle().locals * 2)
