import unittest

from bidirectional_modeling import (
    Aggregation,
    BidirectionalModelingEngine,
    Concept,
    ConceptLibrary,
    Context,
    EquivalenceSpec,
    Evidence,
    FieldRequirement,
    FiniteStateModel,
    Intervention,
    MacroSpec,
    ModelMetrics,
    PurposeHypothesis,
    PurposeLevel,
    Realizer,
    RequirementCategory,
    ResourceBudget,
    Trace,
    behaviorally_equivalent,
)
from bidirectional_modeling.probes import HorizonExtensionProbe
from bidirectional_modeling.examples import (
    organization_interpretation_scenario,
    science_closure_scenario,
)


def x_spec(name="x-is-one", operator="eq", expected=1):
    return MacroSpec(
        name,
        ("x",),
        (FieldRequirement("x objective", "x", operator, expected),),
        EquivalenceSpec(("x",)),
    )


class LazyTwoScenarioModel:
    name = "lazy-two"
    metrics = ModelMetrics(1, 1, 1)
    assumptions = ()
    failure_boundaries = ()
    prior_reliability = 1.0
    capabilities = ()

    def __init__(self):
        self.produced = 0

    def scenario_count(self, context):
        return 2

    def simulate(self, context, horizon):
        self.produced += 1
        yield Trace(self.name, "good", "baseline", ({"x": 0}, {"x": 1}))
        self.produced += 1
        yield Trace(self.name, "bad", "baseline", ({"x": 0}, {"x": 0}))


class RegressionTests(unittest.TestCase):
    def test_partial_verification_is_not_a_satisfaction_certificate(self):
        model = LazyTwoScenarioModel()
        result = BidirectionalModelingEngine().realize(
            x_spec(),
            Context(),
            (model,),
            ResourceBudget(max_simulations=1),
        )

        self.assertFalse(result.candidates)
        self.assertEqual(model.produced, 1)
        self.assertEqual(result.simulations_used, 1)
        certificate = result.rejected[0].certificate
        self.assertFalse(certificate.complete)
        self.assertTrue(certificate.requirements_passed)
        self.assertFalse(certificate.satisfied)
        self.assertEqual(certificate.confidence.coverage, 0.5)

    def test_simulation_budget_is_global_across_candidates(self):
        first = LazyTwoScenarioModel()
        second = LazyTwoScenarioModel()
        result = BidirectionalModelingEngine().realize(
            x_spec(),
            Context(),
            (first, second),
            ResourceBudget(max_simulations=1),
        )
        self.assertEqual(result.searched_candidates, 1)
        self.assertEqual(first.produced + second.produced, 1)
        self.assertTrue(result.truncated)

    def test_exact_budget_for_all_work_is_not_reported_as_truncated(self):
        model = LazyTwoScenarioModel()
        result = BidirectionalModelingEngine().realize(
            x_spec(),
            Context(),
            (model,),
            ResourceBudget(max_simulations=2),
        )
        self.assertFalse(result.truncated)
        self.assertEqual(result.simulations_used, 2)

    def test_probe_certificates_are_preserved(self):
        model = FiniteStateModel(
            "probe-target",
            {"start": {"x": 0}},
            ("start",),
            (),
            lambda state, action, context: {"x": 1},
            lambda state, context: dict(state),
            ModelMetrics(1, 1, 1),
        )
        result = BidirectionalModelingEngine(
            realizer=Realizer(probes=(HorizonExtensionProbe(extra_steps=1),))
        ).realize(
            x_spec(), Context(), (model,), ResourceBudget(max_simulations=2)
        )

        evaluation = (result.candidates + result.rejected)[0]
        self.assertEqual(len(evaluation.probe_certificates), 1)
        self.assertTrue(evaluation.probe_certificates[0].complete)
        self.assertEqual(
            evaluation.verification_score,
            min(
                evaluation.certificate.verification_score,
                evaluation.probe_certificates[0].verification_score,
            ),
        )

    def test_macro_round_trip_does_not_accept_name_only_match(self):
        def transition(state, action, context):
            return {"x": 1}

        def readout(state, context):
            return dict(state)

        model = FiniteStateModel(
            "m",
            {"s": {"x": 0}},
            ("s",),
            ("noop",),
            transition,
            readout,
            ModelMetrics(1, 1, 1),
        )
        original = x_spec("same-name", "eq", 1)
        different = x_spec("same-name", "ge", 0)
        hypothesis = PurposeHypothesis(
            "different semantics", PurposeLevel.FUNCTION, different
        )
        report = BidirectionalModelingEngine().macro_round_trip(
            original, Context(), (model,), (hypothesis,)
        )
        self.assertFalse(report.passed)
        self.assertEqual(report.semantic_preservation, (False,))

    def test_macro_semantic_signature_is_order_insensitive(self):
        first = FieldRequirement("first", "x", "ge", 0)
        second = FieldRequirement(
            "second",
            "y",
            "eq",
            0,
            category=RequirementCategory.INVARIANT,
            aggregation=Aggregation.EACH,
        )
        left = MacroSpec(
            "left",
            ("x", "y"),
            (first,),
            EquivalenceSpec(("x", "y")),
            invariants=(second,),
            assumptions=("a", "b"),
        )
        right = MacroSpec(
            "right",
            ("y", "x"),
            (first,),
            EquivalenceSpec(("y", "x")),
            invariants=(second,),
            assumptions=("b", "a"),
        )
        self.assertTrue(left.semantically_equivalent(right))

    def test_behavior_equivalence_preserves_intervention_identity(self):
        context = Context(
            interventions=(Intervention("A", ("a",)), Intervention("B", ("b",)))
        )

        def readout(state, _context):
            return {"x": state["x"]}

        def first_transition(state, action, _context):
            return {"x": {"noop": 0, "a": 0, "b": 1}[action]}

        def second_transition(state, action, _context):
            return {"x": {"noop": 0, "a": 1, "b": 0}[action]}

        first = FiniteStateModel(
            "first",
            {"s": {"x": 0}},
            ("s",),
            ("a", "b"),
            first_transition,
            readout,
            ModelMetrics(1, 1, 1),
        )
        second = FiniteStateModel(
            "second",
            {"s": {"x": 0}},
            ("s",),
            ("a", "b"),
            second_transition,
            readout,
            ModelMetrics(1, 1, 1),
        )
        self.assertFalse(
            behaviorally_equivalent(first, second, x_spec(), context)
        )

    def test_equivalence_and_signature_use_the_same_partition(self):
        relation = EquivalenceSpec(("x",), {"x": 1.0})
        samples = ({"x": -0.6}, {"x": -0.4}, {"x": 0.4}, {"x": 0.6})
        for left in samples:
            for right in samples:
                self.assertEqual(
                    relation.equivalent(left, right),
                    relation.signature(left) == relation.signature(right),
                )

    def test_baseline_is_checked_alongside_interventions(self):
        _, _, model = science_closure_scenario()
        context = Context(interventions=(Intervention("push", ("push-right",)),))
        traces = tuple(model.simulate(context, 1))
        self.assertEqual(
            {trace.intervention for trace in traces}, {"baseline", "push"}
        )
        self.assertEqual(model.scenario_count(context), 6)

    def test_one_refinement_is_one_version_and_judgments_do_not_conflict(self):
        spec, context, model = science_closure_scenario()
        counterexample = BidirectionalModelingEngine().check_closure(
            model, spec, context
        ).counterexamples[0]
        library = ConceptLibrary((Concept("c", "definition"),))
        refined = library.refine_from_counterexample("c", counterexample)
        self.assertEqual(refined.version, 2)
        self.assertEqual(
            library.refine_from_counterexample("c", counterexample).version, 2
        )
        switched = library.record_judgment(
            "c", refined.negative_examples[0], accepted=True
        )
        self.assertIn(refined.negative_examples[0], switched.positive_examples)
        self.assertNotIn(refined.negative_examples[0], switched.negative_examples)

    def test_human_approved_refinement_closes_the_science_model(self):
        spec, context, model = science_closure_scenario()
        engine = BidirectionalModelingEngine()
        result = engine.refine_until_closed(
            model,
            spec,
            context,
            lambda report, _spec, _model: report.suggested_features[0],
            concept_name="position state",
            max_iterations=1,
        )
        self.assertTrue(result.closed)
        self.assertIn("velocity", result.final_spec.observables)
        self.assertEqual(engine.concepts.get("position state").version, 2)
        self.assertTrue(result.steps[-1].closure_report.closed)

    def test_closure_searches_reachable_states(self):
        spec = MacroSpec(
            "reachable closure",
            ("position",),
            (),
            EquivalenceSpec(("position",)),
            horizon=2,
        )

        def transition(state, action, context):
            if state["phase"] == 0:
                return {"phase": 1, "position": 0}
            return {"phase": 1, "position": 1}

        def readout(state, context):
            return {"position": state["position"]}

        model = FiniteStateModel(
            "reachable",
            {"start": {"phase": 0, "position": 0}},
            ("start",),
            ("advance",),
            transition,
            readout,
            ModelMetrics(1, 1, 1),
        )
        report = BidirectionalModelingEngine().check_closure(model, spec, Context())
        self.assertFalse(report.closed)
        self.assertGreater(report.explored_states, 1)
        self.assertIn("phase", report.suggested_features)

    def test_closure_also_checks_the_baseline_transition(self):
        spec = MacroSpec(
            "baseline closure",
            ("x",),
            (),
            EquivalenceSpec(("x",)),
            horizon=1,
        )

        def transition(state, action, context):
            if action == "noop":
                return {"x": state["hidden"], "hidden": state["hidden"]}
            return dict(state)

        model = FiniteStateModel(
            "baseline-sensitive",
            {
                "left": {"x": 0, "hidden": -1},
                "right": {"x": 0, "hidden": 1},
            },
            ("left", "right"),
            ("intervene",),
            transition,
            lambda state, context: {"x": state["x"]},
            ModelMetrics(1, 1, 1),
        )
        report = BidirectionalModelingEngine().check_closure(
            model, spec, Context(), max_depth=0
        )
        self.assertFalse(report.closed)
        self.assertEqual(report.counterexamples[0].witness["action"], "noop")

    def test_incomplete_closure_search_never_claims_a_proof(self):
        spec, context, model = science_closure_scenario()
        report = BidirectionalModelingEngine().check_closure(
            model, spec, context, max_states=1
        )
        self.assertFalse(report.complete)
        self.assertFalse(report.closed)
        self.assertEqual(report.explored_states, 1)

    def test_weak_intent_evidence_does_not_remove_the_cap(self):
        context, model, hypotheses, experiments, _ = (
            organization_interpretation_scenario()
        )
        weak = Evidence(
            "a vague recollection",
            "preserve central control",
            0.1,
            kind="statement",
        )
        strong = Evidence(
            "the process owner explicitly selected central control",
            "preserve central control",
            0.8,
            kind="statement",
        )
        engine = BidirectionalModelingEngine()
        weak_result = engine.interpret(
            model, context, hypotheses, (weak,), experiments
        )
        strong_result = engine.interpret(
            model, context, hypotheses, (strong,), experiments
        )
        weak_candidate = next(
            item
            for item in weak_result.candidates
            if item.hypothesis.name == "preserve central control"
        )
        strong_candidate = next(
            item
            for item in strong_result.candidates
            if item.hypothesis.name == "preserve central control"
        )
        self.assertLessEqual(weak_candidate.ranking_score, 0.49)
        self.assertGreater(strong_candidate.ranking_score, 0.49)
        self.assertIn("not a probability", weak_result.score_semantics)


if __name__ == "__main__":
    unittest.main()
