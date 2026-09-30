"""Cross-phase bindings, atomic graph updates and claim-sized composition work."""
from dataclasses import replace
from unittest.mock import patch
import unittest

from bidirectional_modeling import Context, EquivalenceSpec, ResourceBudget
from bidirectional_modeling.composition import (
    CompositionExperiment, CompositionRule, CompositionRuleSelector, CompositionTest,
)
from bidirectional_modeling.correspondence import (
    CorrespondenceCaseRole, CorrespondenceCaseResult, CorrespondenceValidationCase, CorrespondenceValidator, ScaleGraph,
)
from bidirectional_modeling.evaluation import SatisfactionEvaluator
from bidirectional_modeling.examples import (
    composition_rule_scenario, residual_quotient_scenario,
    scale_correspondence_scenario, scale_correspondence_suite,
)
from bidirectional_modeling.refinement import ClosureAnalyzer
from bidirectional_modeling.residual import ResidualQuotientAnalyzer
from test_interpretation_status import model, spec


class CorrespondenceCollectionBoundaries(unittest.TestCase):
    def test_bad_collection_reserves_unknown_work_and_never_starts_other_side(self):
        for kind in ('none', 'exception', 'overspend'):
            c, lower, upper, lc, uc = scale_correspondence_scenario()
            class Broken(SatisfactionEvaluator):
                calls = 0
                def collect(self, *args):
                    self.calls += 1
                    if kind == 'exception': raise RuntimeError('collection unavailable')
                    if kind == 'none': return None
                    return super().collect(*args[:-1], replace(args[-1], max_simulations=7))
                def unstarted_batch(self, *args):
                    raise AssertionError('failure output must use the default constructor')
            evaluator = Broken()
            cap = 1 if kind == 'overspend' else 7
            checked = CorrespondenceValidator(evaluator).validate(c, lower, upper, lc, uc,
                budget=ResourceBudget(max_simulations=cap))
            self.assertEqual((checked.status, checked.simulations_used, evaluator.calls),
                             ('undecided', cap, 1))
            self.assertFalse(checked.passed or checked.counterexamples)
            self.assertTrue(any('reserved' in reason for reason in checked.boundaries))

    def test_batch_from_other_context_or_horizon_is_not_a_proof(self):
        for kind in ('context', 'horizon', 'model'):
            c, lower, upper, lc, uc = scale_correspondence_scenario()
            class Stale(SatisfactionEvaluator):
                def collect(self, m, ctx, horizon, budget):
                    if kind == 'context': ctx = replace(ctx, observer='other observer')
                    elif kind == 'horizon': horizon = 1
                    else: m = replace(m, name='other model')
                    return super().collect(m, ctx, horizon, budget)
            checked = CorrespondenceValidator(Stale()).validate(c, lower, upper, lc, uc,
                horizon=2, budget=ResourceBudget(max_simulations=7))
            self.assertEqual((checked.status, checked.simulations_used), ('undecided', 7))
            self.assertFalse(checked.passed or checked.counterexamples)

    def test_later_collection_cannot_rebind_old_lower_evidence(self):
        for kind in ('model', 'context'):
            c, lower, upper, lc, uc = scale_correspondence_scenario()
            class Drift(SatisfactionEvaluator):
                def collect(self, m, ctx, horizon, budget):
                    batch = super().collect(m, ctx, horizon, budget)
                    if m is upper:
                        if kind == 'model': lower.metrics = replace(lower.metrics, cost=99)
                        else: lc.environment['late'] = 'changed'
                    return batch
            checked = CorrespondenceValidator(Drift()).validate(c, lower, upper, lc, uc)
            self.assertEqual(checked.status, 'undecided')
            self.assertEqual(checked.simulations_used, 3)
            self.assertFalse(checked.passed)
            self.assertTrue(any('binding changed' in issue.detail for issue in checked.diagnostics))

    def test_failed_upper_collection_keeps_prefix_accounting_and_blocks_suite(self):
        c, cases = scale_correspondence_suite()
        class LateFailure(SatisfactionEvaluator):
            calls = 0
            def collect(self, *args):
                self.calls += 1
                return None if self.calls == 4 else super().collect(*args)
        evaluator = LateFailure()
        checked = CorrespondenceValidator(evaluator).validate_suite(c, cases,
            ResourceBudget(max_simulations=7))
        self.assertEqual((checked.simulations_used, evaluator.calls), (7, 4))
        self.assertTrue(checked.cases[0].certificate.passed)
        self.assertEqual(checked.independent_holdout_status, 'undecided')
        self.assertFalse(checked.passed)
        self.assertTrue(checked.truncated)

    def test_holdout_cannot_change_prior_case_inputs_and_leave_suite_verified(self):
        for kind in ('model', 'context', 'opaque_context'):
            c, cases = scale_correspondence_suite()
            class Drift(SatisfactionEvaluator):
                calls = 0
                def collect(self, *args):
                    self.calls += 1
                    batch = super().collect(*args)
                    if self.calls == 4:
                        if kind == 'model':
                            prior = cases[0].lower_model
                            prior.metrics = replace(prior.metrics, cost=99)
                        else:
                            cases[0].lower_context.environment['late'] = (
                                object() if kind == 'opaque_context' else 'changed')
                    return batch
            checked = CorrespondenceValidator(Drift()).validate_suite(c, cases)
            self.assertEqual(checked.simulations_used, 6)
            self.assertFalse(checked.passed or checked.compatibility_passed)
            self.assertTrue(checked.truncated)
            self.assertTrue(any('input binding changed' in reason for reason in checked.boundaries))

    def test_exhausted_remaining_allowance_does_not_invoke_upper_collection(self):
        c, lower, upper, lc, uc = scale_correspondence_scenario()
        evaluator = SatisfactionEvaluator()
        with patch.object(evaluator, 'collect', wraps=evaluator.collect) as collect:
            checked = CorrespondenceValidator(evaluator).validate(c, lower, upper, lc, uc,
                budget=ResourceBudget(max_simulations=2))
        self.assertEqual((checked.status, checked.simulations_used), ('undecided', 2))
        self.assertEqual(collect.call_count, 1)
        self.assertIs(collect.call_args.args[0], lower)

    def test_scope_and_holdout_metadata_are_typed_before_execution(self):
        c, lower, upper, lc, uc = scale_correspondence_scenario()
        for horizon in (True, 1.5, '1', 0):
            with self.assertRaises(ValueError):
                CorrespondenceValidator().validate(c, lower, upper, lc, uc, horizon=horizon)
            with self.assertRaises(ValueError):
                CorrespondenceValidationCase('case', lower, upper, lc, horizon=horizon)
        for fields in ({'role': 'holdout'}, {'independent': 1}):
            with self.assertRaises(TypeError):
                CorrespondenceValidationCase('case', lower, upper, lc, **fields)
        valid = CorrespondenceValidationCase('holdout', lower, upper, lc,
            role=CorrespondenceCaseRole.HOLDOUT, independent=True)
        self.assertTrue(valid.independent)
        certificate = CorrespondenceValidator().validate(c, lower, upper, lc, uc)
        for role, independent in (('holdout', True), (CorrespondenceCaseRole.HOLDOUT, 1)):
            with self.assertRaises(TypeError):
                CorrespondenceCaseResult('result', role, independent, certificate)


class GraphTransactionBoundaries(unittest.TestCase):
    def test_rejected_edge_cannot_publish_any_new_scale(self):
        c, lower, upper, lc, uc = scale_correspondence_scenario()
        graph, validator = ScaleGraph(), CorrespondenceValidator()
        original = validator.validate(c, lower, upper, lc, uc)
        graph.add_verified(c, original)
        baseline = (graph.scales, graph.correspondences, graph.find_paths('micro', 'macro'))
        for changed in (
                replace(c, upper_scale=replace(c.upper_scale, name='leaked')),
                replace(c, name='other edge', lower_scale=replace(c.lower_scale, name='leaked'),
                        upper_scale=replace(c.upper_scale, equivalence=EquivalenceSpec(('total',), {'total': 2})))):
            checked = validator.validate(changed, lower, upper, lc, uc)
            self.assertTrue(checked.passed)
            with self.assertRaises(ValueError): graph.add_verified(changed, checked)
            self.assertEqual((graph.scales, graph.correspondences,
                              graph.find_paths('micro', 'macro')), baseline)
            self.assertIs(graph.certificate(c.name), original)
        for bound in (True, 1.5, '1', 0):
            with self.assertRaises(ValueError): graph.find_paths('micro', 'macro', max_hops=bound)


class CompositionWorkBoundaries(unittest.TestCase):
    def experiment(self):
        return CompositionExperiment('first', {'s': {'x': 0}}, ('s',), ('go', 'poison'),
            lambda state, ctx: state, EquivalenceSpec(('x',)), (
                CompositionTest('refuter', 's', ('go',), True, {'x': 1}),
                CompositionTest('later', 's', ('poison',), True, {'x': 0})))

    def test_reliable_refutation_stops_remaining_tests_and_cases(self):
        calls = []
        def transition(state, action, ctx):
            calls.append(action)
            if action == 'poison': raise RuntimeError('unnecessary later work')
            return state
        first = self.experiment()
        report = CompositionRuleSelector().select((CompositionRule('wrong', transition),),
            (first, replace(first, name='later case')))
        self.assertEqual(calls, ['go', 'go'])
        self.assertEqual(len(report.rejected[0].cases), 1)
        self.assertEqual(len(report.rejected[0].cases[0].tests), 1)
        self.assertFalse(report.selected or report.undecided)
        self.assertEqual(report.rejected[0].counterexamples[0].kind, 'composition-observation-mismatch')

    def test_full_diagnostics_continues_after_refutation(self):
        first = self.experiment()
        class NoResidual:
            calls = 0
            def analyze(self, *args, **kwargs):
                self.calls += 1
                raise RuntimeError('diagnostic unavailable')
        analyzer = NoResidual()
        rule = CompositionRule('wrong', lambda state, action, ctx: state)
        report = CompositionRuleSelector(analyzer).select((rule,),
            (first, replace(first, name='later case')), full_diagnostics=True)
        self.assertEqual(analyzer.calls, 2)
        self.assertEqual(len(report.rejected[0].cases), 2)
        self.assertTrue(all(len(case.tests) == 2 for case in report.rejected[0].cases))
        self.assertFalse(report.selected or report.undecided)

    def test_invalid_analysis_limits_are_rejected_before_any_test(self):
        experiments, rules = composition_rule_scenario()
        for field in ('max_states', 'max_context_tests', 'max_reachability_depth', 'max_context_depth'):
            for value in (True, 1.5, '1'):
                selector = CompositionRuleSelector()
                with patch.object(selector, '_test_rule') as evaluate:
                    with self.assertRaises(ValueError):
                        selector.select(rules[:1], experiments, **{field: value})
                evaluate.assert_not_called()
        eq, ctx, candidate = residual_quotient_scenario()
        for field in ('max_states', 'max_context_tests', 'max_reachability_depth', 'max_context_depth'):
            with self.assertRaises(ValueError):
                ResidualQuotientAnalyzer().analyze(candidate, eq, ctx, **{field: 1.5})
        for field in ('max_states', 'max_depth'):
            with self.assertRaises(ValueError):
                ClosureAnalyzer().analyze(model(), spec(), Context(), **{field: '1'})

    def test_analyzer_receipt_must_bind_current_model_context_and_limits(self):
        experiments, rules = composition_rule_scenario()
        experiment = experiments[0]
        baseline = CompositionRuleSelector().select(rules[:1], experiments)
        proof = baseline.certified[0].cases[0].residual_report
        class Cached:
            def analyze(self, *args, **kwargs): return proof
        changes = (
            (replace(experiment, name='other experiment'), {}),
            (replace(experiment, context=Context(observer='other')), {}),
            (replace(experiment, equivalence=EquivalenceSpec(('parity',), {'parity': 2})), {}),
            (experiment, {'max_states': 99}),
        )
        for changed, limits in changes:
            checked = CompositionRuleSelector(Cached()).select(rules[:1], (changed,), **limits)
            self.assertFalse(checked.certified or checked.rejected)
            self.assertIsNone(checked.undecided[0].cases[0].residual_report)
            self.assertIn('does not bind', checked.undecided[0].cases[0].analysis_error)
        for fields in ({'quotient': None}, {'complete': 1}, {'stable': 'yes'},
                       {'congruent': 0}, {'context_basis_reproduces_partition': 1}):
            with self.assertRaises(TypeError): replace(proof, **fields)
        with patch.object(Cached, 'analyze', return_value=None):
            checked = CompositionRuleSelector(Cached()).select(rules[:1], experiments)
        self.assertFalse(checked.certified or checked.rejected)
        self.assertIn('does not bind', checked.undecided[0].cases[0].analysis_error)

    def test_composition_support_is_boolean_and_actions_own_their_sequence(self):
        for support in (1, 0, 'yes', None):
            with self.assertRaises(TypeError):
                CompositionTest('bad', 's', (), support, {'x': 0})
        actions = ['go']
        test = CompositionTest('owned', 's', actions, True, {'x': 0})
        actions.append('poison')
        self.assertEqual(test.actions, ('go',))

        for declaration in (
                lambda: CompositionRule(1, lambda state, action, ctx: state),
                lambda: CompositionTest(1, 's', (), True, {'x': 0}),
                lambda: CompositionTest('bad', 1, (), True, {'x': 0}),
                lambda: replace(self.experiment(), name=1)):
            with self.assertRaises(ValueError): declaration()
        with self.assertRaises(TypeError):
            replace(self.experiment(), tests=(CompositionTest('opaque', 's', (), True,
                                                           {'x': object()}),))
