"""A declaration, a refuting object, and an unexamined domain are distinct."""
from dataclasses import replace
import unittest

from bidirectional_modeling.extensions.implications import (
    AttributeContext, AttributeObject, Implication, check_implication,
    explore_implications, verify_implication_assessment,
)


def context(complete=True):
    return AttributeContext(('a', 'b', 'c'), (
        AttributeObject('both', ('a', 'b')),
        AttributeObject('only-b', ('b',)),
        AttributeObject('neither', ()),
    ), complete, 'finite experiment P')


class ImplicationContracts(unittest.TestCase):
    def test_refutation_identifies_object_and_its_exact_attribute_row(self):
        ctx = context(False)
        result = check_implication(ctx, Implication(('b',), 'a'))
        self.assertEqual(result.status, 'refuted')
        self.assertEqual(result.counterexample, AttributeObject('only-b', ('b',)))
        self.assertEqual(result.supporting_objects, ('both', 'only-b'))
        self.assertEqual(verify_implication_assessment(ctx, result), 'valid')
        changed = replace(ctx, objects=(ctx.objects[0], ctx.objects[2]))
        self.assertEqual(verify_implication_assessment(changed, result), 'invalid')

    def test_no_refuter_needs_a_complete_declared_domain_and_support(self):
        claim = Implication(('a',), 'b')
        incomplete = check_implication(context(False), claim)
        self.assertEqual(incomplete.status, 'undecided')
        self.assertEqual(incomplete.reason, 'object_domain_incomplete')
        full = check_implication(context(), claim)
        self.assertEqual(full.status, 'verified')
        self.assertEqual(full.supporting_objects, ('both',))
        self.assertEqual(verify_implication_assessment(context(), full,
                                                       max_object_checks=1), 'undecided')
        unsupported = check_implication(context(), Implication(('c',), 'a'))
        self.assertEqual((unsupported.status, unsupported.reason),
                         ('undecided', 'no_supporting_object'))

    def test_budget_error_and_provenance_do_not_become_approval(self):
        ctx, claim = context(), Implication(('a',), 'b')
        pending = check_implication(ctx, claim, max_object_checks=1)
        self.assertEqual(pending.status, 'undecided')
        self.assertEqual(pending.reason, 'object_budget_exhausted')
        assert verify_implication_assessment(ctx, pending) == 'invalid'
        endorsed = check_implication(ctx, claim)
        changed = replace(ctx, source='new protocol')
        self.assertEqual(verify_implication_assessment(changed, endorsed), 'invalid')
        with self.assertRaises(ValueError):
            check_implication(ctx, Implication(('missing',), 'b'))

    def test_exploration_is_bounded_and_does_not_hide_refutations(self):
        ctx = context()
        complete = explore_implications(ctx, max_premises=1, max_candidates=100)
        self.assertTrue(complete.exhaustive)
        self.assertEqual(len(complete.assessments), 6)
        self.assertEqual(complete.candidate_count, 6)
        by_claim = {(a.implication.premises, a.implication.conclusion): a
                    for a in complete.assessments}
        self.assertEqual(by_claim[(('a',), 'b')].status, 'verified')
        self.assertEqual(by_claim[(('b',), 'a')].status, 'refuted')
        self.assertEqual(by_claim[(('c',), 'a')].status, 'undecided')
        limited = explore_implications(ctx, max_premises=1, max_candidates=1)
        self.assertFalse(limited.exhaustive)
        self.assertEqual(limited.candidate_count, 6)
        self.assertEqual(len(limited.assessments), 1)
        self.assertEqual(explore_implications(ctx, max_candidates=0).assessments, ())

    def test_schema_rejects_unknown_fields_and_duplicate_objects(self):
        with self.assertRaises(ValueError):
            AttributeContext(('a',), (AttributeObject('x', ('b',)),), True)
        with self.assertRaises(ValueError):
            AttributeContext(('a',), (AttributeObject('x', ()), AttributeObject('x', ('a',))), True)
        with self.assertRaises(ValueError):
            Implication(('a',), 'a')
