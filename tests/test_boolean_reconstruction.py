import unittest
from dataclasses import replace
from itertools import product

from bidirectional_modeling.extensions.boolean import (BooleanExpression, BooleanLanguage, enumerate_boolean_language, find_boolean_substitute, reconstruct_boolean, verify_boolean_substitute)
from bidirectional_modeling import (ReconstructionRule, SearchHypothesis, DescriptionLength, SearchProtocol, SearchExperiment, SearchWorkBudget, ModelSearchCase, Context, ScenarioKey)

X = BooleanExpression(('var', 'x'))
Z = BooleanExpression(('var', 'z'))
AND = BooleanExpression(('and', X.tree, Z.tree))
INPUTS = tuple(dict(zip(('x', 'z'), row)) for row in product((False, True), repeat=2))


class BooleanTests(unittest.TestCase):
    def test_bounded_language_excludes_all_unary_substitutes_for_interaction(self):
        lower = BooleanLanguage(('x', 'z'), operations=('not',), max_nodes=4)
        r = find_boolean_substitute(AND, lower, INPUTS)
        self.assertEqual(r.status, 'absent')
        self.assertEqual(r.reason, 'bounded_language_exhausted')
        self.assertEqual(verify_boolean_substitute(AND, lower, INPUTS, r), 'valid')
        self.assertEqual(verify_boolean_substitute(AND, lower, INPUTS, replace(r, reason='forged')), 'invalid')
        self.assertEqual(verify_boolean_substitute(AND, lower, INPUTS, r, budget=SearchWorkBudget(0)), 'undecided')
        self.assertEqual(verify_boolean_substitute(X, lower, INPUTS, r), 'invalid')
        full = replace(lower, operations=('not', 'and'), max_nodes=3)
        r = find_boolean_substitute(AND, full, INPUTS)
        self.assertEqual(r.status, 'found')
        self.assertEqual(r.witness, AND)
        self.assertNotEqual(lower.fingerprint, full.fingerprint)
        self.assertEqual(find_boolean_substitute(AND, lower, ()).status, 'unknown')
        self.assertEqual(find_boolean_substitute(AND, lower, INPUTS, budget=SearchWorkBudget(0)).status, 'unknown')

    def test_reconstruction_generates_predictions_through_existing_adapter(self):
        language = BooleanLanguage(('x', 'z'))
        names = ('00', '01', '10', '11')
        worlds = tuple(product(('0', '1'), repeat=4))
        protocol = SearchProtocol('bool', 'code', tuple(SearchExperiment(n, n) for n in names), worlds)
        cases = tuple(ModelSearchCase(n, Context(environment=row), ScenarioKey('s', 'baseline'), 'y')
                      for n, row in zip(names, INPUTS))
        parent = SearchHypothesis('x', worlds.index(('0', '0', '1', '1')), 'other', language.description(X))
        expr, candidate, result = reconstruct_boolean((X, parent), AND, (), language,
            ReconstructionRule('interaction'), name='xz', protocol=protocol, cases=cases,
            target='interaction', world_answers=tuple('and' if row == ('0', '0', '0', '1') else 'other' for row in worlds))
        h = result.search.hypotheses[0]
        self.assertEqual(expr, AND)
        self.assertEqual(h.world, worlds.index(('0', '0', '0', '1')), result.diagnostics)
        self.assertEqual(h.macro_answer, 'and')
        self.assertEqual(result.simulations_used, 8)
        self.assertEqual(h.description, language.description(AND))
        self.assertEqual(candidate.materials, ('x', 'z'))

    def test_enumeration_and_encoding_are_reproducible(self):
        language = BooleanLanguage(('x', 'z'), max_nodes=4)
        a, b = enumerate_boolean_language(language), enumerate_boolean_language(language)
        self.assertEqual(a, b)
        self.assertTrue(a.complete)
        self.assertEqual(len(a.expressions), len(set(a.expressions)))
        self.assertTrue(all(language.accepts(e) for e in a.expressions))
        self.assertGreater(language.description(AND).total, language.description(X).total)
        self.assertFalse(enumerate_boolean_language(language, budget=SearchWorkBudget(1)).complete)
        self.assertEqual(BooleanExpression(('and', Z.tree, X.tree)), AND)
        self.assertEqual(AND.replace((0,), Z).evaluate({'z': True}), True)

    def test_truth_tables(self):
        for row in INPUTS:
            for op in ('and', 'or', 'xor'):
                e = BooleanExpression((op, X.tree, Z.tree))
                truth = {'and': row['x'] and row['z'], 'or': row['x'] or row['z'], 'xor': row['x'] != row['z']}
                self.assertEqual(e.evaluate(row), truth[op])
            self.assertEqual(BooleanExpression(('not', X.tree)).evaluate(row), not row['x'])
        self.assertFalse(BooleanExpression(('false',)).evaluate({}))
        self.assertTrue(BooleanExpression(('true',)).evaluate({}))

    def test_input_validation(self):
        for tree in (('bad',), ('and', X.tree), ('var', '')):
            with self.assertRaises(ValueError):
                BooleanExpression(tree)
        with self.assertRaises(ValueError):
            X.evaluate({'x': 1})
        with self.assertRaises(ValueError):
            X.replace((0,), Z)
        with self.assertRaises(ValueError):
            BooleanLanguage(('x',), operations=('bad',))
        with self.assertRaises(ValueError):
            BooleanLanguage(('x',), max_nodes=0)
        with self.assertRaises(ValueError):
            BooleanLanguage(('x',)).description(AND)
        with self.assertRaises(ValueError):
            find_boolean_substitute(AND, BooleanLanguage(('x',)), INPUTS)
