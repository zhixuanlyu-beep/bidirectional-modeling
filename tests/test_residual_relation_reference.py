"""Independent pair-reachability oracle, not partition refinement.

Exhausts three-state/one-action and two-state/two-action partial tables.
All states are initial; conclusions concern only these declared finite tables.
"""
from itertools import product
import unittest

from bidirectional_modeling import Context, EquivalenceSpec, UndefinedTransition
from bidirectional_modeling.residual import ResidualQuotientAnalyzer
from test_residual import finite_model


def equivalent_pair(table, outputs, left, right):
    pending, seen = [(left, right)], set()
    while pending:
        a, b = pending.pop()
        if (a, b) in seen:
            continue
        seen.add((a, b))
        if outputs[a] != outputs[b]:
            return False
        for x, y in zip(table[a], table[b]):
            if (x is None) != (y is None):
                return False
            if x is not None:
                pending.append((x, y))
    return True


def model_for(table, outputs, actions=None):
    actions = actions or tuple('a%d' % i for i in range(len(table[0])))
    states = {str(i): {'node': i, 'signal': outputs[i]} for i in range(len(table))}

    def step(state, action, context):
        if action == 'noop':
            return state
        target = table[state['node']][actions.index(action)]
        if target is None:
            raise UndefinedTransition('declared undefined edge')
        return states[str(target)]

    return finite_model('table', states, tuple(states), actions, step,
                        readout=lambda state, context: dict(state))


class ResidualRelationReference(unittest.TestCase):
    def test_all_small_partial_tables_preserve_exact_pair_relation_and_actions(self):
        for n, width in ((3, 1), (2, 2)):
            for cells in product((None,) + tuple(range(n)), repeat=n * width):
                table = tuple(cells[i * width:(i + 1) * width] for i in range(n))
                for outputs in product((0, 1), repeat=n):
                    with self.subTest(table=table, outputs=outputs):
                        report = ResidualQuotientAnalyzer().analyze(
                            model_for(table, outputs), EquivalenceSpec(('signal',)), Context())
                        self.assertTrue(report.complete and report.congruent and report.minimal)
                        classes = dict(report.quotient.initial_state_classes)
                        for left, right in product(range(n), repeat=2):
                            self.assertEqual(classes[str(left)] == classes[str(right)],
                                equivalent_pair(table, outputs, left, right))
                        for source in range(n):
                            for index, target in enumerate(table[source]):
                                action = 'a%d' % index
                                if target is None:
                                    with self.assertRaises(UndefinedTransition):
                                        report.quotient.next_class(classes[str(source)], action)
                                else:
                                    self.assertEqual(report.quotient.next_class(
                                        classes[str(source)], action), classes[str(target)])

    def test_observation_and_action_changes_require_new_partition(self):
        table = ((2, 0), (3, 1), (2, 2), (3, 3))
        model = model_for(table, (0, 0, 0, 1))
        analyzer = ResidualQuotientAnalyzer()
        full = analyzer.analyze(model, EquivalenceSpec(('signal',)), Context())
        classes = dict(full.quotient.initial_state_classes)
        self.assertNotEqual(classes['0'], classes['1'])
        restricted = model_for(tuple((row[1],) for row in table), (0, 0, 0, 1), ('stay',))
        coarse = analyzer.analyze(restricted, EquivalenceSpec(('signal',)), Context())
        self.assertEqual(dict(coarse.quotient.initial_state_classes)['0'],
                         dict(coarse.quotient.initial_state_classes)['1'])
        detailed = analyzer.analyze(restricted, EquivalenceSpec(('node',)), Context())
        self.assertTrue(detailed.complete and detailed.congruent)
        self.assertNotEqual(dict(detailed.quotient.initial_state_classes)['0'],
                            dict(detailed.quotient.initial_state_classes)['1'])
        self.assertNotEqual(coarse.equivalence_fingerprint, detailed.equivalence_fingerprint)
