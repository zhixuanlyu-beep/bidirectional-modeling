"""A complete verdict must identify the current model declaration, not its name."""
from dataclasses import replace
import unittest
from unittest.mock import Mock

from bidirectional_modeling import Context, ModelMetrics, Realizer, ResourceBudget, SatisfactionEvaluator
from bidirectional_modeling.provenance import model_declaration_fingerprint
from test_interpretation_status import model, spec


class ModelCertificateBinding(unittest.TestCase):
    def replay(self, certificate, current):
        evaluator = Mock()
        evaluator.evaluate.return_value = certificate
        return Realizer(evaluator).realize(spec(), Context(), (current,),
            ResourceBudget(max_cost=10, max_simulations=7))

    def assert_pending(self, result):
        self.assertFalse(result.candidates or result.dominated or result.rejected)
        self.assertEqual(len(result.undecided), 1)
        self.assertTrue(result.truncated)
        self.assertEqual(result.simulations_used, 7)
        self.assertEqual(result.undecided[0].diagnostics[0].phase, 'base-budget')

    def test_same_name_different_declarations_cannot_accept_or_reject(self):
        original = model()
        changes = (
            replace(original, states={'s': {'x': 2}}),
            replace(original, actions=('noop', 'other')),
            replace(original, readout=lambda s, c: {'x': 1, 'hidden': 2}),
            replace(original, transition=lambda s, a, c: {'x': 2}),
            replace(original, applicable=lambda s, a, c: True),
            replace(original, metrics=ModelMetrics(1, 9, 9)),
        )
        for passed in (True, False):
            certificate = SatisfactionEvaluator().evaluate(original, spec(), Context(),
                ResourceBudget(max_cost=10))
            if not passed:
                certificate = replace(certificate, checks=(replace(certificate.checks[0], passed=False),))
            self.assertTrue(certificate.complete)
            for changed in changes:
                with self.subTest(passed=passed, changed=model_declaration_fingerprint(changed)):
                    self.assertFalse(certificate.binds_model(changed))
                    self.assert_pending(self.replay(certificate, changed))

    def test_cost_50_limit_10_reproduction_is_not_accepted(self):
        cheap = model()
        expensive = replace(cheap, metrics=ModelMetrics(50, 1, 1))
        certificate = SatisfactionEvaluator().evaluate(cheap, spec(), Context(),
            ResourceBudget(max_cost=10))
        self.assertTrue(certificate.satisfied)
        self.assert_pending(self.replay(certificate, expensive))
        fresh = SatisfactionEvaluator().evaluate(expensive, spec(), Context(),
            ResourceBudget(max_cost=10))
        self.assertTrue(fresh.complete)
        self.assertFalse(fresh.satisfied)

    def test_stale_pass_and_fail_after_mutation_are_pending(self):
        for passed in (True, False):
            current = model()
            certificate = SatisfactionEvaluator().evaluate(current, spec(), Context(),
                ResourceBudget(max_cost=10))
            if not passed:
                certificate = replace(certificate, checks=(replace(certificate.checks[0], passed=False),))
            current.states['s']['x'] = 0
            self.assert_pending(self.replay(certificate, current))

    def test_unavailable_declaration_is_an_unresolved_native_verdict(self):
        opaque = object()
        current = replace(model(), readout=lambda s, c: {'x': 1 if opaque else 0})
        certificate = SatisfactionEvaluator().evaluate(current, spec(), Context())
        self.assertFalse(certificate.complete or certificate.satisfied or certificate.binds_model(current))
        self.assertTrue(any('model declaration' in b for b in certificate.failure_boundaries))
        result = Realizer().realize(spec(), Context(), (current,), ResourceBudget(max_simulations=7))
        self.assertFalse(result.candidates or result.rejected)
        self.assertEqual(result.simulations_used, 1)
        self.assertEqual(len(result.undecided), 1)

    def test_declaration_field_is_protocol_bound_and_current_model_replays(self):
        current = model()
        certificate = SatisfactionEvaluator().evaluate(current, spec(), Context(), ResourceBudget(max_cost=10))
        self.assertTrue(certificate.binds_model(replace(current)))
        self.assertTrue(self.replay(certificate, current).candidates)
        with self.assertRaisesRegex(ValueError, 'protocol fingerprint'):
            replace(certificate, model_declaration_fingerprint='0' * 64)
