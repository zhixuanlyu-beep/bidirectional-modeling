"""Public-path regressions for undecided semantics and immutable audit data."""
import json
import unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace
from itertools import permutations

from bidirectional_modeling import (
    BidirectionalModelingEngine, CheckResult, Context, CustomRequirement,
    DiscriminatingQuery, EquivalenceSpec, Experiment, FieldRequirement,
    FiniteStateModel, InterpretationObservation, InterpretationResult,
    MacroSpec, ModelMetrics, PurposeHypothesis, PurposeLevel, RequirementCategory,
    ResourceBudget, SatisfactionEvaluator,
)


def crash(model, traces, context):
    raise RuntimeError('checker unavailable')


def spec(field='x', expected=1):
    return MacroSpec(field, (field,), (FieldRequirement(field, field, 'eq', expected),),
                     EquivalenceSpec((field,)), horizon=1)


def hypothesis(name, field='x', allowed=None):
    return PurposeHypothesis(name, PurposeLevel.FUNCTION, spec(field),
                             allowed_outcomes=allowed or {})


def model(name='original', y=1):
    return FiniteStateModel(name, {'s': {'x': 1, 'y': y}}, ('s',), (),
                           lambda s, a, c: s, lambda s, c: dict(s), ModelMetrics(1, 1, 1))


class InterpretationStatusTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.model, self.context = BidirectionalModelingEngine(), model(), Context()
        self.good = hypothesis('good')
        self.bad = replace(hypothesis('broken'), spec=replace(spec(),
            objectives=(CustomRequirement('broken', RequirementCategory.OBJECTIVE, crash),)))

    def test_execution_error_is_undecided_not_a_uniqueness_proof(self):
        for hs in permutations((self.good, self.bad)):
            r = self.engine.interpret(self.model, self.context, hs)
            self.assertEqual(tuple(c.hypothesis.name for c in r.candidates), ('good',))
            self.assertEqual(r.undecided[0][0], 'broken')
            self.assertIn('checker unavailable', r.undecided[0][1])
            self.assertFalse(r.rejected)
            self.assertFalse(r.excluded)
            self.assertEqual(r.identification_status, 'undecided')
            self.assertTrue(r.non_identifiable)
            self.assertFalse(r.truncated)  # Execution error, not a budget truncation.
        certificate = SatisfactionEvaluator().evaluate(self.model, self.bad.spec, self.context)
        self.assertFalse(certificate.complete)
        self.assertEqual(certificate.checks[0].evaluation_error, 'checker unavailable')
        with self.assertRaises(ValueError):
            replace(certificate, complete=True)

    def test_false_requirement_is_a_rejection_and_can_establish_unique(self):
        false = replace(hypothesis('false'), spec=spec(expected=0))
        r = self.engine.interpret(self.model, self.context, (self.good, false))
        self.assertEqual(r.identification_status, 'unique')
        self.assertFalse(r.non_identifiable)
        self.assertEqual(r.rejected[0][0], 'false')
        self.assertTrue(r.rejected[0][1].complete)
        r = self.engine.interpret(self.model, self.context, (false,))
        self.assertEqual(r.identification_status, 'all_excluded')
        self.assertTrue(r.non_identifiable)

    def test_central_status_covers_empty_ambiguous_and_truncated(self):
        empty = InterpretationResult('m', (), (), None)
        self.assertEqual(empty.identification_status, 'empty_catalogue')
        self.assertTrue(empty.non_identifiable)
        self.assertEqual(replace(empty, truncated=True).identification_status, 'undecided')
        r = self.engine.interpret(self.model, self.context, (self.good, hypothesis('other')))
        self.assertEqual(r.identification_status, 'ambiguous')
        with self.assertRaises(TypeError):
            InterpretationResult('m', (), (), None, non_identifiable=False)

    def test_macro_budget_exhaustion_never_claims_identification(self):
        r = self.engine.macro_round_trip(spec(), self.context, (self.model,), (self.good,),
                                        budget=ResourceBudget(max_simulations=1))
        self.assertTrue(r.interpretations)
        child = r.interpretations[0]
        self.assertTrue(child.truncated)
        self.assertEqual(child.identification_status, 'undecided')
        self.assertTrue(child.non_identifiable)
        self.assertFalse(r.compatibility_passed)

    def test_renaming_or_reordering_cannot_select_a_different_round_trip_target(self):
        clone = model('clone', y=0)
        for names in (('A', 'B'), ('B', 'A')):
            hs = (hypothesis(names[0], 'x'), hypothesis(names[1], 'y'))
            for ordered in permutations(hs):
                with self.assertRaisesRegex(ValueError, 'select an explicit'):
                    self.engine.micro_round_trip(self.model, self.context, ordered, (clone,))
                r = self.engine.micro_round_trip(self.model, self.context, ordered, (clone,),
                                                selected_hypothesis=names[0])
                self.assertTrue(r.passed)
                self.assertEqual(r.selected_hypothesis, names[0])
                self.assertEqual(r.realization.spec.name, 'x')
                self.assertTrue(r.interpretation.non_identifiable)
                failed = self.engine.micro_round_trip(self.model, self.context, ordered, (clone,),
                                                     selected_hypothesis=names[1])
                self.assertFalse(failed.passed)
        with self.assertRaisesRegex(ValueError, 'not a verified'):
            self.engine.micro_round_trip(self.model, self.context, hs, (clone,),
                                        selected_hypothesis='missing')

    def test_undecided_alternative_requires_explicit_selection(self):
        with self.assertRaisesRegex(ValueError, 'select an explicit'):
            self.engine.micro_round_trip(self.model, self.context,
                                        (self.good, self.bad), (model('clone'),))
        r = self.engine.micro_round_trip(self.model, self.context, (self.good, self.bad),
                                        (model('clone'),), selected_hypothesis='good')
        self.assertTrue(r.passed)  # Proof only for the explicitly selected task.
        self.assertEqual(r.interpretation.identification_status, 'undecided')
        macro = self.engine.macro_round_trip(spec(), self.context, (self.model,),
                                            (self.good, self.bad))
        self.assertFalse(macro.compatibility_passed)

    def test_observations_reach_both_round_trip_interfaces(self):
        experiment = Experiment('e', 'result?', ('0', '1'))
        observation = InterpretationObservation('e', '0', 'lab')
        hs = (hypothesis('x', 'x', {'e': ('0',)}), hypothesis('y', 'y', {'e': ('1',)}))
        micro = self.engine.micro_round_trip(self.model, self.context, hs, (model('clone', 0),),
            experiments=(experiment,), observations=(observation,))
        self.assertTrue(micro.passed)
        self.assertEqual(micro.selected_hypothesis, 'x')
        macro = self.engine.macro_round_trip(spec(), self.context, (self.model, model('other')),
            hs, experiments=(experiment,), observations=iter((observation,)))
        self.assertEqual(len(macro.interpretations), 2)
        for result in macro.interpretations:
            self.assertEqual(result.excluded, (('y', observation),))
            self.assertEqual(result.identification_status, 'unique')

    def test_outcome_maps_copy_export_and_reject_mutation(self):
        inputs = {'e': ['0']}
        h = hypothesis('A', allowed=inputs)
        inputs['e'].append('1')
        self.assertEqual(h.allowed_outcomes['e'], ('0',))
        self.assertEqual(deepcopy(h), h)
        self.assertTrue(asdict(h))
        self.assertTrue(asdict(hypothesis('empty')))
        self.assertEqual(json.loads(json.dumps(h.to_dict()))['allowed_outcomes'], {'e': ['0']})
        with self.assertRaises(TypeError):
            h.allowed_outcomes['e'] = ('1',)
        with self.assertRaises(FrozenInstanceError):
            h.allowed_outcomes.entries = ()
        q = DiscriminatingQuery(Experiment('e', 'result?', ('0', '1')),
            ('A', 'B'), {'A': ('0',), 'B': ('1',)}, 1, 1, 1.0)
        self.assertEqual(deepcopy(q), q)
        self.assertTrue(asdict(q))
        exported = q.to_dict()
        exported['allowed_outcomes']['B'][0] = '0'
        self.assertEqual(q.allowed_outcomes['B'], ('1',))
        with self.assertRaises(TypeError):
            q.allowed_outcomes['B'] = ('0',)

    def test_audit_export_contains_bound_data_without_callbacks(self):
        r = self.engine.interpret(self.model, self.context, (self.good, self.bad))
        snapshot = json.loads(json.dumps(r.to_dict()))
        self.assertEqual(snapshot['identification_status'], 'undecided')
        self.assertEqual(snapshot['undecided'][0]['candidate'], 'broken')
        self.assertEqual(snapshot['candidates'][0]['binding']['spec_fingerprint'],
                         snapshot['candidates'][0]['hypothesis']['spec_fingerprint'])
        self.assertEqual(deepcopy(r), r)
        self.assertTrue(asdict(r))
        snapshot['candidates'][0]['hypothesis']['name'] = 'changed'
        self.assertEqual(r.candidates[0].hypothesis.name, 'good')
        # Do not silently export a new declaration beside an old certificate.
        r.candidates[0].hypothesis.spec.equivalence.tolerances['x'] = 0.1
        with self.assertRaisesRegex(ValueError, 'changed hypothesis'):
            r.to_dict()

    def test_error_check_cannot_claim_success(self):
        with self.assertRaises(ValueError):
            CheckResult('error', RequirementCategory.OBJECTIVE, True, None, 'result', 1,
                        evaluation_error='failed to run')
