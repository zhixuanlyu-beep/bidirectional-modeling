"""A local separation survives only a fresh replay under its bound context."""
from dataclasses import replace
import unittest

from bidirectional_modeling import Context, EquivalenceSpec
from bidirectional_modeling.examples import residual_quotient_scenario
from bidirectional_modeling.examples import partial_residual_scenario
from bidirectional_modeling.residual import (
    ResidualQuotientAnalyzer, verify_distinguishing_context,
)


class DistinguishingReplayTests(unittest.TestCase):
    def setUp(self):
        self.equivalence, self.context, self.model = residual_quotient_scenario()
        self.report = ResidualQuotientAnalyzer().analyze(
            self.model, self.equivalence, self.context)
        self.witness = next(w for w in self.report.distinguishing_contexts
                            if w.actions == ('probe',))

    def replay(self, **kwargs):
        return verify_distinguishing_context(self.report, self.witness,
            self.model, self.equivalence, self.context, **kwargs)

    def test_replay_binds_initial_paths_and_actual_terminal_readouts(self):
        result = self.replay()
        self.assertEqual(result.status, 'valid')
        self.assertGreater(result.operations_used, 0)
        self.assertEqual(self.replay(max_operations=result.operations_used).status, 'valid')
        limited = self.replay(max_operations=result.operations_used - 1)
        self.assertEqual((limited.status, limited.reason),
                         ('undecided', 'replay_budget_exhausted'))

    def test_scope_change_or_claim_tampering_cannot_pass(self):
        self.assertEqual(verify_distinguishing_context(self.report, self.witness,
            self.model, self.equivalence, Context(observer='changed')).status, 'invalid')
        self.assertEqual(verify_distinguishing_context(self.report, self.witness,
            self.model, EquivalenceSpec(('signal', 'node')), self.context).status, 'invalid')
        changed = replace(self.witness, left_terminal_signature=('forged',))
        self.assertEqual(verify_distinguishing_context(self.report, changed,
            self.model, self.equivalence, self.context).status, 'invalid')

    def test_model_drift_and_execution_error_are_distinct(self):
        changed = replace(self.model, transition=lambda state, action, context: state)
        self.assertEqual(verify_distinguishing_context(self.report, self.witness,
            changed, self.equivalence, self.context).status, 'invalid')
        def crash(state, action, context):
            raise RuntimeError('replay unavailable')
        broken = replace(self.model, transition=crash)
        report = verify_distinguishing_context(self.report, self.witness,
            broken, self.equivalence, self.context)
        self.assertEqual(report.status, 'undecided')
        self.assertIn('replay unavailable', report.reason)

    def test_a_changed_hidden_microstate_invalidates_the_source_path(self):
        state = self.report.quotient.states[self.witness.left_state]
        changed_state = replace(state, micro_state={**state.micro_state,
                                                     'unobserved': 'forged'})
        states = list(self.report.quotient.states)
        states[state.index] = changed_state
        changed_report = replace(self.report,
            quotient=replace(self.report.quotient, states=tuple(states)))
        result = verify_distinguishing_context(changed_report, self.witness,
            self.model, self.equivalence, self.context)
        self.assertEqual((result.status, result.reason), ('invalid', 'source_state_changed'))

    def test_explicit_undefined_transition_is_a_local_separation(self):
        equivalence, context, model = partial_residual_scenario()
        report = ResidualQuotientAnalyzer().analyze(model, equivalence, context)
        witness = next(w for w in report.distinguishing_contexts
                       if w.left_defined != w.right_defined)
        verified = verify_distinguishing_context(report, witness,
            model, equivalence, context)
        self.assertEqual(verified.status, 'valid')

    def test_local_distinction_does_not_promote_incomplete_global_partition(self):
        incomplete = self.report = ResidualQuotientAnalyzer().analyze(
            self.model, self.equivalence, self.context, max_context_depth=0)
        self.assertFalse(incomplete.minimal)
        self.witness = incomplete.distinguishing_contexts[0]
        self.assertEqual(self.replay().status, 'valid')
        with self.assertRaises(ValueError):
            self.replay(max_operations=-1)
