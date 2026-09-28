"""Truth, unresolved execution, and user preferences have different authorities."""
import json
import subprocess
import sys
import unittest
from dataclasses import replace

from bidirectional_modeling.engine import (BidirectionalModelingEngine)
from bidirectional_modeling import (Context, CustomRequirement, EquivalenceSpec, FieldRequirement, FiniteStateModel, MacroSpec, ModelMetrics, PurposeHypothesis, PurposeLevel, Realizer, RequirementCategory, ResourceBudget, SatisfactionEvaluator)
from bidirectional_modeling.core import (ProbeOutcome, VerificationIssue)
from bidirectional_modeling.core import _compare
from bidirectional_modeling.composition import CompositionRuleSelector
from bidirectional_modeling.examples import composition_rule_scenario
from bidirectional_modeling.extensions.concepts import ConceptLibrary
from bidirectional_modeling.interpretation import CatalogHypothesisGenerator, ObservedEffectGenerator
from bidirectional_modeling.probes import HorizonExtensionProbe
from bidirectional_modeling.residual import ResidualQuotientAnalyzer
from test_interpretation_status import model, spec, crash


def mixed_transition(state, action, context):
    if state['k'] == 2:
        raise RuntimeError('transition unavailable')
    return {'x': state['k'], 'k': state['k']}


def mixed_readout(state, context):
    return {'x': state['x']}


class NumericMeaningTests(unittest.TestCase):
    def test_large_integer_difference_is_a_real_public_counterexample(self):
        for value in (2**53, 2**100, 10**400, -(2**100)):
            with self.subTest(value=value):
                m = FiniteStateModel('large', {'s': {'x': value}}, ('s',), (),
                    lambda s, a, c: s, lambda s, c: s, ModelMetrics(0, 0, 0))
                certificate = SatisfactionEvaluator().evaluate(m, spec(expected=value+1), Context())
                self.assertTrue(certificate.complete)
                self.assertFalse(certificate.satisfied)
                self.assertEqual(certificate.checks[0].margin, -1)
                self.assertFalse(_compare(value, 'ge', value+1, 0)[0])
                self.assertTrue(_compare(value, 'lt', value+1, 0)[0])

    def test_mixed_numeric_types_do_not_round_the_integer_operand(self):
        self.assertFalse(_compare(2**53+1, 'eq', float(2**53), 0)[0])
        self.assertTrue(_compare(2**53+1, 'gt', float(2**53), 0)[0])
        self.assertTrue(_compare(2**53+1, 'eq', float(2**53), 1)[0])
        for v in (float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                _compare(v, 'eq', 1, 0)

    def test_margins_preserve_units_without_manufacturing_strength(self):
        metre = _compare(1, 'ge', 0, 0)
        kilometre = _compare(.001, 'ge', 0, 0)
        self.assertTrue(metre[0] and kilometre[0])
        self.assertEqual(metre[1], kilometre[1]*1000)
        c = SatisfactionEvaluator().evaluate(model(), spec(), Context())
        self.assertFalse(hasattr(c.verification, 'robustness'))
        self.assertFalse(hasattr(c.checks[0], 'robustness'))

    def test_observation_equality_matches_the_residual_partition(self):
        for values in ((True, 1), (1, 1.0), ({'a': True}, {'a': 1}), (1, 1)):
            eq = EquivalenceSpec(('x',))
            m = FiniteStateModel('typed', {'a': {'x': values[0]}, 'b': {'x': values[1]}},
                ('a', 'b'), (), lambda s, a, c: s, lambda s, c: s, ModelMetrics(0, 0, 0))
            r = ResidualQuotientAnalyzer().analyze(m, eq, Context())
            self.assertTrue(r.minimal)
            self.assertEqual(eq.equivalent({'x': values[0]}, {'x': values[1]}),
                             r.quotient.class_count == 1)

    def test_explicit_buckets_work_on_integers_beyond_float_range(self):
        eq = EquivalenceSpec(('x',), {'x': 1})
        self.assertFalse(eq.equivalent({'x': 10**400}, {'x': 10**400+1}))
        self.assertTrue(eq.equivalent({'x': .1}, {'x': .2}))


class UnknownBoundaryTests(unittest.TestCase):
    def test_checker_error_is_not_a_rejected_realization(self):
        broken = replace(spec(), objectives=(CustomRequirement('broken', RequirementCategory.OBJECTIVE, crash),))
        result = Realizer().realize(broken, Context(), (model(),))
        self.assertFalse(result.candidates or result.rejected)
        self.assertEqual(len(result.undecided), 1)
        self.assertIn('checker unavailable', result.undecided[0].diagnostics[0].reason)

    def test_incomplete_probe_cannot_admit_a_candidate(self):
        class Probe:
            def probe(self, m, s, c, evaluator, budget):
                certificate = evaluator.evaluate(m, s, c, budget)
                return ProbeOutcome(None, replace(certificate, complete=False, satisfied=False))
        result = Realizer(probes=(Probe(),)).realize(spec(), Context(), (model(),))
        self.assertFalse(result.candidates or result.rejected)
        self.assertEqual(len(result.undecided), 1)
        self.assertTrue(result.undecided[0].diagnostics)

    def test_complete_failed_probe_needs_no_manual_counterexample_to_block(self):
        class Probe:
            def probe(self, m, s, c, evaluator, budget):
                return ProbeOutcome(None, evaluator.evaluate(m, spec(expected=0), c, budget))
        result = Realizer(probes=(Probe(),)).realize(spec(), Context(), (model(),))
        self.assertFalse(result.candidates or result.undecided)
        self.assertEqual(len(result.rejected), 1)
        self.assertEqual(result.rejected[0].counterexamples[0].kind, 'probe-requirement-failure')

    def test_probe_budget_is_a_diagnostic_not_a_contradiction(self):
        result = Realizer(probes=(HorizonExtensionProbe(blocking=True),)).realize(
            spec(), Context(), (model(),), ResourceBudget(max_simulations=1))
        self.assertFalse(result.rejected or result.candidates)
        self.assertTrue(result.truncated)
        self.assertFalse(result.undecided[0].counterexamples)
        self.assertEqual(result.undecided[0].diagnostics[0].phase, 'probe-budget')

    def test_mixed_closure_errors_never_pollute_concept_negatives(self):
        m = FiniteStateModel('mixed', {str(k): {'x': 0, 'k': k} for k in range(3)},
            ('0','1','2'), ('go',), mixed_transition, mixed_readout, ModelMetrics(0,0,0))
        s = replace(spec(expected=0), horizon=2)
        engine = BidirectionalModelingEngine()
        report = engine.refine_until_closed(m, s, Context(),
            lambda report, s, m: 'k', max_iterations=1)
        self.assertFalse(report.closed)
        self.assertTrue(report.steps[0].closure_report.diagnostics)
        self.assertTrue(report.steps[0].closure_report.counterexamples)
        from bidirectional_modeling.core import Concept
        library = ConceptLibrary((Concept('state', 'declared state'),))
        for step in report.steps:
            if step.accepted_feature and step.closure_report.counterexamples:
                library.refine_from_counterexample('state', step.closure_report.counterexamples[0], source="explicit caller review", reason="paired states refute the declared grouping", applicability="reviewed relation to the named concept")
        self.assertEqual(library.history[0].source, 'explicit caller review')
        self.assertNotIn('unavailable', library.history[0].example)
        with self.assertRaises(ValueError):
            library.refine_from_counterexample('state', VerificationIssue('execution', 'unavailable'), source="explicit caller review", reason="paired states refute the declared grouping", applicability="reviewed relation to the named concept")


class PolicyBoundaryTests(unittest.TestCase):
    def test_a_declared_independent_catalogue_does_not_prove_recovery(self):
        class ClaimedCatalogue(CatalogHypothesisGenerator):
            independence_declared = True
        s = spec()
        catalogue = ClaimedCatalogue((PurposeHypothesis('injected', PurposeLevel.EFFECT, s),))
        r = BidirectionalModelingEngine().macro_round_trip(s, Context(), (model(),), catalogue)
        self.assertTrue(r.compatibility_passed)
        self.assertTrue(r.independence_declared)
        self.assertEqual(r.generation_source, 'generator')
        self.assertFalse(hasattr(r, 'passed'))
        self.assertFalse(ObservedEffectGenerator.independence_declared)

    def test_default_composition_keeps_all_verified_rules_without_encoding(self):
        experiments, rules = composition_rule_scenario()
        rules = tuple(replace(rule, description_length=None) for rule in rules)
        selector = CompositionRuleSelector()
        for order in (rules, rules[::-1]):
            r = selector.select(order, experiments)
            self.assertEqual({e.rule.name for e in r.certified}, {'parity', 'delayed failure'})
            self.assertFalse(r.selected)
            self.assertIsNone(r.selection_policy)
            self.assertTrue(all(e.total_description_length is None for e in r.certified))
        with self.assertRaises(ValueError):
            selector.select(rules, experiments, selection_policy='shortest_description')

    def test_description_choice_is_an_explicit_policy_only(self):
        experiments, rules = composition_rule_scenario()
        selector = CompositionRuleSelector()
        plain = selector.select(rules, experiments)
        chosen = selector.select(rules, experiments, selection_policy='shortest_description')
        self.assertEqual({e.rule.name for e in plain.certified}, {e.rule.name for e in chosen.certified})
        self.assertEqual(chosen.selected_rule_names, ('parity',))
        self.assertFalse(plain.unique_selection)

    def test_concept_memory_is_optional_and_preserves_judgment_provenance(self):
        from bidirectional_modeling.core import (Concept)
        library = ConceptLibrary((Concept('c', 'definition'),))
        library.record_judgment('c', 'example', True, source='operator A')
        library.record_judgment('c', 'example', False, source='operator B')
        self.assertEqual(tuple(item.source for item in library.history), ('operator A', 'operator B'))
        self.assertEqual(tuple(item.accepted for item in library.history), (True, False))
        p = subprocess.run([sys.executable, '-c',
            'import sys; from bidirectional_modeling.engine import BidirectionalModelingEngine; '
            'e=BidirectionalModelingEngine(); '
            'assert "bidirectional_modeling.extensions.concepts" not in sys.modules'],
            capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
