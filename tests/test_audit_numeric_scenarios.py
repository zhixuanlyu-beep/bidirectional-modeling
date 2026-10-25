"""Exact experiment scheduling, explicit baselines and ordered proof inputs."""
from fractions import Fraction
from itertools import product
import json
import unittest

from bidirectional_modeling import (
    Context, Experiment, Intervention, PurposeHypothesis, PurposeLevel,
    ResourceBudget, ScenarioKey,
)
from bidirectional_modeling.evaluation import SatisfactionEvaluator
from bidirectional_modeling.finite_replay import (
    DeclaredFiniteModel, FiniteModelDeclaration, create_replay_bundle, replay_bundle,
)
from bidirectional_modeling.interpretation import Interpreter
from bidirectional_modeling.search import DescriptionLength, SearchExperiment, SearchProtocol, SearchWorkBudget
from bidirectional_modeling.search_adapter import ModelSearchCandidate, ModelSearchCase
from bidirectional_modeling.search_examples import conflict_search_scenario
from test_regressions import TupleTraceModel, x_spec, scenario_context
from bidirectional_modeling import Trace


class AuditNumericScenarioContracts(unittest.TestCase):
    def choose(self, experiments, declarations):
        model = TupleTraceModel('m', (Trace('m', 's', 'baseline', ({'x': 0}, {'x': 1})),))
        hypotheses = tuple(PurposeHypothesis(str(i), PurposeLevel.FUNCTION, x_spec(str(i)),
            allowed_outcomes={e.name: values[i] for e, values in zip(experiments, declarations)})
            for i in range(len(declarations[0])))
        return Interpreter().interpret(model, scenario_context(('s', 'baseline')),
            hypotheses, experiments=experiments)

    def test_exact_costs_cannot_collapse_to_a_more_expensive_tie(self):
        for low, high in ((2**54, 2**54+1), (10**400, 10**400+1),
                          (Fraction(1), Fraction(10**40+1, 10**40))):
            experiments = (Experiment('a-expensive', '?', ('0', '1'), high),
                           Experiment('z-cheap', '?', ('0', '1'), low))
            result = self.choose(experiments, ((('0',), ('1',)),) * 2)
            self.assertEqual(result.discriminating_query.experiment.name, 'z-cheap')
            self.assertEqual(experiments[0].cost, high)
            self.assertIs(type(experiments[0].cost), type(high))
            json.dumps(result.to_dict(), allow_nan=False)

    def test_exact_ratio_precedes_separated_pairs_in_scheduling(self):
        n = 2**54
        experiments = (Experiment('binary', '?', ('0', '1'), n),
                       Experiment('ternary', '?', ('0', '1', '2'), 2*n+2))
        result = self.choose(experiments, ((('0',), ('1',), ('0',)),
                                           (('0',), ('1',), ('2',))))
        self.assertGreater(Fraction(1, n+1), Fraction(2, 2*n+3))
        self.assertEqual(result.discriminating_query.experiment.name, 'binary')

    def test_rational_audit_export_and_invalid_costs(self):
        experiment = Experiment('e', '?', ('0', '1'), Fraction(1, 3))
        result = self.choose((experiment,), ((('0',), ('1',)),))
        exported = json.loads(json.dumps(result.to_dict()))
        self.assertEqual(exported['discriminating_query']['experiment']['cost'],
                         {'numerator': 1, 'denominator': 3})
        from bidirectional_modeling import InterpretationObservation
        model = TupleTraceModel('m', (Trace('m', 's', 'baseline', ({'x': 0}, {'x': 1})),))
        hypotheses = tuple(c.hypothesis for c in result.candidates)
        excluded = Interpreter().interpret(model, scenario_context(('s', 'baseline')), hypotheses,
            experiments=(experiment,), observations=(InterpretationObservation('e', '0', 'lab'),))
        self.assertEqual(json.loads(json.dumps(excluded.to_dict()))['excluded'][0]['experiment']['cost'],
                         {'numerator': 1, 'denominator': 3})
        for value in (True, '1', None):
            with self.assertRaises(TypeError): Experiment('e', '?', ('0',), value)
        for value in (-1, Fraction(-1, 3), float('nan'), float('inf'), -float('inf')):
            with self.assertRaises(ValueError): Experiment('e', '?', ('0',), value)

    def test_unordered_certificates_fail_before_budget_or_iteration(self):
        problem, evidence = conflict_search_scenario()
        certificates = problem.search(evidence).conflicts
        self.assertTrue(certificates)
        for values in (set(certificates), frozenset(certificates), set()):
            budget = SearchWorkBudget(0)
            with self.assertRaisesRegex(TypeError, 'unordered set'):
                problem.search(evidence, values, budget=budget)
            self.assertEqual(budget.work.total, 0)
        expected = problem.search(evidence, certificates)
        self.assertEqual(problem.search(evidence, iter(certificates)), expected)

    def test_audit_export_keeps_model_declaration_for_pass_and_failure(self):
        model = TupleTraceModel('m', (Trace('m', 's', 'baseline', ({'x': 0}, {'x': 1})),))
        hypotheses = tuple(PurposeHypothesis(name, PurposeLevel.FUNCTION, x_spec(name, expected=value))
                           for name, value in (('pass', 1), ('fail', 0)))
        result = Interpreter().interpret(model, scenario_context(('s', 'baseline')), hypotheses)
        exported = result.to_dict()
        for record, certificate in ((exported['candidates'][0], result.candidates[0].certificate),
                                     (exported['rejected'][0], result.rejected[0][1])):
            self.assertIn('model_declaration_fingerprint', record['binding'])
            self.assertEqual(record['binding']['model_declaration_fingerprint'],
                             certificate.model_declaration_fingerprint)

    def test_promoted_observables_preserve_declared_order_and_reject_sets(self):
        from test_interpretation_status import model
        original = model()
        for values in ({'x', 'y'}, frozenset(('x', 'y')), set()):
            with self.assertRaisesRegex(TypeError, 'unordered set'):
                original.with_promoted_observables(values)
        fields = ['y', 'x', 'y']
        promoted = original.with_promoted_observables(iter(fields))
        fields.reverse()
        self.assertEqual(promoted.name, original.name + '+y+x')
        self.assertEqual(promoted.observe({'x': 2, 'y': 3}, Context()), {'x': 2, 'y': 3})

    def table_model(self):
        return DeclaredFiniteModel(FiniteModelDeclaration('table', ('0', '1'), ('0',),
            ('noop', 'flip'), (('0', 'noop', '0'), ('0', 'flip', '1'),
                             ('1', 'noop', '1'), ('1', 'flip', '0')),
            (('0', (('y', '0'),)), ('1', (('y', '1'),)))))

    def test_explicit_baseline_runs_once_and_retains_its_actions(self):
        model = self.table_model()
        declarations = ((), (Intervention('probe', ('flip',)),),
            (Intervention('baseline', ('flip',)),),
            (Intervention('probe'), Intervention('baseline', ('flip',))))
        for include, interventions in product((False, True), declarations):
            expected = list(interventions)
            if (include and not any(i.name == 'baseline' for i in expected)) or not expected:
                expected.insert(0, Intervention('baseline'))
            context = Context(include_baseline=include, interventions=interventions,
                scenario_manifest=tuple(ScenarioKey('0', i.name) for i in expected))
            traces = tuple(model.simulate(context, 1))
            self.assertEqual(tuple(t.intervention for t in traces), tuple(i.name for i in expected))
            self.assertEqual(tuple(t.snapshots[-1]['y'] for t in traces),
                             tuple('1' if i.actions == ('flip',) else '0' for i in expected))
            batch = SatisfactionEvaluator().collect(model, context, 1, ResourceBudget())
            self.assertTrue(batch.complete, batch.diagnostics)

    def test_explicit_baseline_bundle_roundtrip(self):
        model = self.table_model()
        context = Context(interventions=(Intervention('baseline', ('flip',)),),
                          scenario_manifest=(ScenarioKey('0', 'baseline'),))
        protocol = SearchProtocol('explicit baseline', 'v1',
            (SearchExperiment('read', 'final y'),), (('0',), ('1',)))
        candidates = (ModelSearchCandidate(model, DescriptionLength()),)
        cases = (ModelSearchCase('read', context, ScenarioKey('0', 'baseline'), 'y', 1),)
        bundle = create_replay_bundle(protocol, candidates, cases, target='y', world_answers=('0', '1'))
        result = replay_bundle(bundle)
        self.assertEqual(result.status, 'verified')
        self.assertEqual(result.report.compatible, ('table',))
        self.assertEqual(result.session.search.hypotheses[0].world, 1)
