import unittest

from bidirectional_modeling import (
    Aggregation,
    BidirectionalModelingEngine,
    Concept,
    ConceptLibrary,
    Context,
    EquivalenceSpec,
    Evidence,
    Experiment,
    FieldRequirement,
    FiniteStateModel,
    Intervention,
    MacroSpec,
    ModelMetrics,
    ObservedEffectGenerator,
    PurposeHypothesis,
    PurposeLevel,
    Realizer,
    RegistryGenerator,
    RequirementCategory,
    ResourceBudget,
    ScenarioKey,
    SatisfactionEvaluator,
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


def scenario_context(*pairs):
    return Context(
        scenario_manifest=tuple(ScenarioKey(initial, intervention) for initial, intervention in pairs)
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


class TupleTraceModel:
    metrics = ModelMetrics(1, 1, 1)
    assumptions = ()
    failure_boundaries = ()
    prior_reliability = 1.0
    capabilities = ()

    def __init__(self, name, traces):
        self.name = name
        self.traces = tuple(traces)
        self.calls = 0

    def simulate(self, context, horizon):
        self.calls += 1
        return self.traces


class LyingScenarioModel(LazyTwoScenarioModel):
    name = "lying-scenario-count"

    def scenario_count(self, context):
        return 1


class CrashingModel(TupleTraceModel):
    def __init__(self, name="crashing"):
        super().__init__(name, ())

    def simulate(self, context, horizon):
        self.calls += 1
        raise RuntimeError("deliberate simulation failure")


class ExplodingProbe:
    def probe(self, model, spec, context, evaluator, budget):
        raise RuntimeError("deliberate probe failure")


class RaisingEvaluator(SatisfactionEvaluator):
    def evaluate(self, model, spec, context, budget=None):
        raise RuntimeError("deliberate evaluator failure")


class RegressionTests(unittest.TestCase):
    def test_partial_verification_is_not_a_satisfaction_certificate(self):
        model = LazyTwoScenarioModel()
        result = BidirectionalModelingEngine().realize(
            x_spec(),
            scenario_context(("good", "baseline"), ("bad", "baseline")),
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

    def test_candidate_cannot_forge_completeness_with_scenario_count(self):
        model = LyingScenarioModel()
        result = BidirectionalModelingEngine().realize(
            x_spec(),
            scenario_context(("good", "baseline"), ("bad", "baseline")),
            (model,),
            ResourceBudget(max_simulations=1),
        )

        certificate = result.rejected[0].certificate
        self.assertFalse(certificate.complete)
        self.assertFalse(certificate.satisfied)
        self.assertEqual(model.produced, 1)
        self.assertTrue(
            any("not trusted" in boundary for boundary in certificate.failure_boundaries)
        )

    def test_third_party_scenarios_require_a_caller_manifest(self):
        model = TupleTraceModel(
            "unmanifested",
            (Trace("unmanifested", "s", "baseline", ({"x": 0}, {"x": 1})),),
        )
        certificate = SatisfactionEvaluator().evaluate(model, x_spec(), Context())

        self.assertFalse(certificate.complete)
        self.assertFalse(certificate.satisfied)
        self.assertEqual(certificate.coverage_authority, "none")
        self.assertTrue(
            any("scenario manifest" in item for item in certificate.failure_boundaries)
        )

    def test_manifest_rejects_an_omitted_scenario(self):
        model = TupleTraceModel(
            "omitting",
            (Trace("omitting", "good", "baseline", ({"x": 0}, {"x": 1})),),
        )
        certificate = SatisfactionEvaluator().evaluate(
            model,
            x_spec(),
            scenario_context(("good", "baseline"), ("bad", "baseline")),
        )

        self.assertFalse(certificate.complete)
        self.assertFalse(certificate.satisfied)
        self.assertEqual(certificate.confidence.coverage, 0.5)
        self.assertTrue(
            any("missing required scenarios" in item for item in certificate.failure_boundaries)
        )

    def test_untrusted_scenario_diagnostics_fail_closed(self):
        class NegativeCountModel(TupleTraceModel):
            def scenario_count(self, context):
                return -1

        negative = NegativeCountModel(
            "negative-count",
            (
                Trace("negative-count", "a", "baseline", ({"x": 0}, {"x": 1})),
                Trace("negative-count", "b", "baseline", ({"x": 0}, {"x": 1})),
            ),
        )
        partial = SatisfactionEvaluator().evaluate(
            negative,
            x_spec(),
            scenario_context(("a", "baseline"), ("b", "baseline")),
            ResourceBudget(max_simulations=1),
        )
        self.assertFalse(partial.complete)
        self.assertTrue(any("negative" in item for item in partial.failure_boundaries))
        self.assertTrue(any("1/2" in item for item in partial.failure_boundaries))

        mismatch = SatisfactionEvaluator().evaluate(
            LyingScenarioModel(),
            x_spec(),
            scenario_context(("good", "baseline"), ("bad", "baseline")),
            ResourceBudget(max_simulations=3),
        )
        self.assertTrue(mismatch.complete)
        self.assertFalse(mismatch.satisfied)
        self.assertTrue(any("enumerated 2" in item for item in mismatch.failure_boundaries))

        class MidStreamFailureModel(LazyTwoScenarioModel):
            name = "mid-stream-failure"

            def scenario_count(self, context):
                raise RuntimeError("count unavailable")

            def simulate(self, context, horizon):
                yield Trace(self.name, "a", "baseline", ({"x": 0}, {"x": 1}))
                raise RuntimeError("stream failed")

        failed = SatisfactionEvaluator().evaluate(
            MidStreamFailureModel(),
            x_spec(),
            scenario_context(("a", "baseline")),
        )
        self.assertFalse(failed.complete)
        self.assertTrue(any("count unavailable" in item for item in failed.failure_boundaries))
        self.assertTrue(any("stream failed" in item for item in failed.failure_boundaries))

    def test_simulation_budget_is_global_across_candidates(self):
        first = LazyTwoScenarioModel()
        second = LazyTwoScenarioModel()
        result = BidirectionalModelingEngine().realize(
            x_spec(),
            scenario_context(("good", "baseline"), ("bad", "baseline")),
            (first, second),
            ResourceBudget(max_simulations=1),
        )
        self.assertEqual(result.searched_candidates, 1)
        self.assertEqual(first.produced + second.produced, 1)
        self.assertTrue(result.truncated)

    def test_exact_budget_for_all_work_is_not_reported_as_truncated(self):
        model = TupleTraceModel(
            "eager-two",
            (
                Trace("eager-two", "good", "baseline", ({"x": 0}, {"x": 1})),
                Trace("eager-two", "bad", "baseline", ({"x": 0}, {"x": 0})),
            ),
        )
        result = BidirectionalModelingEngine().realize(
            x_spec(),
            scenario_context(("good", "baseline"), ("bad", "baseline")),
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
            x_spec(), Context(), (model,), ResourceBudget(max_simulations=3)
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

    def test_interpreter_reuses_one_trace_batch_for_same_horizon(self):
        model = TupleTraceModel(
            "one-trace",
            (Trace("one-trace", "s", "baseline", ({"x": 0}, {"x": 1})),),
        )
        hypotheses = (
            PurposeHypothesis("purpose A", PurposeLevel.FUNCTION, x_spec("A")),
            PurposeHypothesis("purpose B", PurposeLevel.FUNCTION, x_spec("B")),
        )
        result = BidirectionalModelingEngine().interpret(
            model,
            scenario_context(("s", "baseline")),
            hypotheses,
            budget=ResourceBudget(max_simulations=1),
        )

        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(model.calls, 1)
        self.assertEqual(result.simulations_used, 1)
        self.assertFalse(result.truncated)

    def test_interpreter_budget_is_global_across_distinct_horizons(self):
        model = TupleTraceModel(
            "one-trace",
            (Trace("one-trace", "s", "baseline", ({"x": 0}, {"x": 1})),),
        )
        second_spec = MacroSpec(
            "second horizon",
            ("x",),
            (FieldRequirement("x objective", "x", "eq", 1),),
            EquivalenceSpec(("x",)),
            horizon=2,
        )
        hypotheses = (
            PurposeHypothesis("first", PurposeLevel.FUNCTION, x_spec()),
            PurposeHypothesis("second", PurposeLevel.FUNCTION, second_spec),
        )
        result = BidirectionalModelingEngine().interpret(
            model,
            scenario_context(("s", "baseline")),
            hypotheses,
            budget=ResourceBudget(max_simulations=1),
        )

        self.assertEqual(tuple(item.hypothesis.name for item in result.candidates), ("first",))
        self.assertEqual(model.calls, 1)
        self.assertEqual(result.simulations_used, 1)
        self.assertTrue(result.truncated)

    def test_observed_effect_generation_shares_the_interpreter_budget(self):
        model = TupleTraceModel(
            "effect-source",
            (
                Trace(
                    "effect-source",
                    "s",
                    "baseline",
                    ({"x": 0}, {"x": 1}),
                ),
            ),
        )
        result = BidirectionalModelingEngine().interpret(
            model,
            scenario_context(("s", "baseline")),
            ObservedEffectGenerator(horizon=1),
            budget=ResourceBudget(max_simulations=1),
        )

        self.assertTrue(result.candidates)
        self.assertEqual(model.calls, 1)
        self.assertEqual(result.simulations_used, 1)
        self.assertFalse(result.truncated)

    def test_macro_round_trip_shares_one_cross_phase_budget(self):
        model = TupleTraceModel(
            "round-trip",
            (Trace("round-trip", "s", "baseline", ({"x": 0}, {"x": 1})),),
        )
        hypothesis = PurposeHypothesis(
            "same semantics", PurposeLevel.FUNCTION, x_spec()
        )
        report = BidirectionalModelingEngine().macro_round_trip(
            x_spec(),
            scenario_context(("s", "baseline")),
            (model,),
            (hypothesis,),
            budget=ResourceBudget(max_simulations=1),
        )

        self.assertFalse(report.passed)
        self.assertTrue(report.truncated)
        self.assertEqual(report.simulations_used, 1)
        self.assertEqual(model.calls, 1)

    def test_injected_hypothesis_catalog_is_not_independent_recovery(self):
        model = TupleTraceModel(
            "injected-round-trip",
            (
                Trace(
                    "injected-round-trip",
                    "s",
                    "baseline",
                    ({"x": 0}, {"x": 1}),
                ),
            ),
        )
        report = BidirectionalModelingEngine().macro_round_trip(
            x_spec("original"),
            scenario_context(("s", "baseline")),
            (model,),
            (
                PurposeHypothesis(
                    "injected equivalent",
                    PurposeLevel.FUNCTION,
                    x_spec("repackaged"),
                ),
            ),
        )

        self.assertTrue(report.compatibility_passed)
        self.assertFalse(report.independent_recovery)
        self.assertFalse(report.passed)

    def test_micro_round_trip_accounts_for_behavior_comparison(self):
        hypothesis = PurposeHypothesis(
            "same semantics", PurposeLevel.FUNCTION, x_spec()
        )
        insufficient_original = TupleTraceModel(
            "insufficient-original",
            (
                Trace(
                    "insufficient-original",
                    "s",
                    "baseline",
                    ({"x": 0}, {"x": 1}),
                ),
            ),
        )
        insufficient_replica = TupleTraceModel(
            "insufficient-replica",
            (
                Trace(
                    "insufficient-replica",
                    "s",
                    "baseline",
                    ({"x": 0}, {"x": 1}),
                ),
            ),
        )
        short_report = BidirectionalModelingEngine().micro_round_trip(
            insufficient_original,
            scenario_context(("s", "baseline")),
            (hypothesis,),
            RegistryGenerator((insufficient_replica,)),
            budget=ResourceBudget(max_simulations=2),
        )
        self.assertFalse(short_report.passed)
        self.assertTrue(short_report.truncated)
        self.assertEqual(short_report.simulations_used, 2)
        self.assertEqual(insufficient_original.calls, 1)
        self.assertEqual(insufficient_replica.calls, 1)

        sufficient_original = TupleTraceModel(
            "sufficient-original",
            (
                Trace(
                    "sufficient-original",
                    "s",
                    "baseline",
                    ({"x": 0}, {"x": 1}),
                ),
            ),
        )
        sufficient_replica = TupleTraceModel(
            "sufficient-replica",
            (
                Trace(
                    "sufficient-replica",
                    "s",
                    "baseline",
                    ({"x": 0}, {"x": 1}),
                ),
            ),
        )
        full_report = BidirectionalModelingEngine().micro_round_trip(
            sufficient_original,
            scenario_context(("s", "baseline")),
            (hypothesis,),
            RegistryGenerator((sufficient_replica,)),
            budget=ResourceBudget(max_simulations=4),
        )
        self.assertTrue(full_report.passed)
        self.assertFalse(full_report.truncated)
        self.assertEqual(full_report.simulations_used, 4)
        self.assertEqual(sufficient_original.calls, 2)
        self.assertEqual(sufficient_replica.calls, 2)

    def test_micro_round_trip_excludes_the_original_model_by_default(self):
        model = TupleTraceModel(
            "identity",
            (Trace("identity", "s", "baseline", ({"x": 0}, {"x": 1})),),
        )
        hypothesis = PurposeHypothesis(
            "same semantics", PurposeLevel.FUNCTION, x_spec()
        )
        report = BidirectionalModelingEngine().micro_round_trip(
            model,
            scenario_context(("s", "baseline")),
            (hypothesis,),
            RegistryGenerator((model,)),
            budget=ResourceBudget(max_simulations=3),
        )

        self.assertFalse(report.passed)
        self.assertFalse(report.behaviorally_equivalent_models)

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

    def test_behavior_equivalence_preserves_initial_scenario_identity(self):
        left = TupleTraceModel(
            "left",
            (
                Trace("left", "A", "baseline", ({"x": 0}, {"x": 0})),
                Trace("left", "B", "baseline", ({"x": 0}, {"x": 1})),
            ),
        )
        right = TupleTraceModel(
            "right",
            (
                Trace("right", "A", "baseline", ({"x": 0}, {"x": 1})),
                Trace("right", "B", "baseline", ({"x": 0}, {"x": 0})),
            ),
        )

        self.assertFalse(
            behaviorally_equivalent(left, right, x_spec(), Context())
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

    def test_human_refinement_does_not_overclaim_unbounded_closure(self):
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
        self.assertFalse(result.closed)
        self.assertIn("velocity", result.final_spec.observables)
        self.assertEqual(engine.concepts.get("position state").version, 2)
        self.assertFalse(result.steps[-1].closure_report.complete)
        self.assertEqual(result.stopped_reason, "closure-analysis-budget-exhausted")

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

    def test_depth_limited_closure_never_claims_a_global_proof(self):
        spec = MacroSpec(
            "delayed divergence",
            ("visible",),
            (),
            EquivalenceSpec(("visible",)),
            horizon=1,
        )

        def transition(state, action, context):
            return {"counter": state["counter"] + 1}

        model = FiniteStateModel(
            "delayed-divergence",
            {"start": {"counter": 0}},
            ("start",),
            (),
            transition,
            lambda state, context: {"visible": int(state["counter"] >= 3)},
            ModelMetrics(1, 1, 1),
        )
        engine = BidirectionalModelingEngine()
        shallow = engine.check_closure(model, spec, Context(), max_depth=1)
        deeper = engine.check_closure(model, spec, Context(), max_depth=2)

        self.assertFalse(shallow.complete)
        self.assertFalse(shallow.closed)
        self.assertFalse(deeper.closed)
        self.assertTrue(deeper.counterexamples)

    def test_closure_ignores_declared_but_unreachable_states(self):
        def transition(state, action, context):
            if state["hidden"] == 0:
                return dict(state)
            return {"x": state["hidden"], "hidden": state["hidden"]}

        model = FiniteStateModel(
            "unreachable-dead-states",
            {
                "start": {"x": 0, "hidden": 0},
                "dead-left": {"x": 0, "hidden": -1},
                "dead-right": {"x": 0, "hidden": 1},
            },
            ("start",),
            (),
            transition,
            lambda state, context: {"x": state["x"]},
            ModelMetrics(1, 1, 1),
        )
        report = BidirectionalModelingEngine().check_closure(
            model, x_spec(), Context(), max_depth=0
        )

        self.assertFalse(report.closed)
        self.assertFalse(report.complete)
        self.assertEqual(report.explored_states, 1)
        self.assertFalse(report.counterexamples)

    def test_crashing_candidate_is_rejected_without_aborting_search(self):
        crashing = CrashingModel()
        good = TupleTraceModel(
            "good",
            (Trace("good", "s", "baseline", ({"x": 0}, {"x": 1})),),
        )
        result = BidirectionalModelingEngine().realize(
            x_spec(),
            scenario_context(("s", "baseline")),
            (crashing, good),
            ResourceBudget(max_simulations=1),
        )

        self.assertEqual(result.searched_candidates, 2)
        self.assertEqual(tuple(item.model.name for item in result.candidates), ("good",))
        self.assertEqual(tuple(item.model.name for item in result.rejected), ("crashing",))
        self.assertTrue(
            any(
                "simulation failed" in boundary
                for boundary in result.rejected[0].certificate.failure_boundaries
            )
        )

    def test_evaluator_exception_isolated_with_fail_closed_certificate(self):
        model = TupleTraceModel(
            "evaluator-target",
            (Trace("evaluator-target", "s", "baseline", ({"x": 0}, {"x": 1})),),
        )
        result = Realizer(evaluator=RaisingEvaluator()).realize(
            x_spec(), Context(), (model,)
        )

        certificate = result.rejected[0].certificate
        self.assertFalse(certificate.satisfied)
        self.assertFalse(certificate.complete)
        self.assertEqual(certificate.checks[0].name, "candidate verification")
        self.assertIn("evaluator failure", certificate.checks[0].detail)

    def test_invalid_candidate_metadata_and_requirement_fail_closed(self):
        class InvalidRequirement:
            category = "not-a-category"

            def evaluate(self, model, traces, context):
                return object()

        class InvalidMetrics:
            cost = -1

        class InvalidMetadataModel:
            metrics = InvalidMetrics()
            prior_reliability = 2.0
            capabilities = ()

            @property
            def name(self):
                raise RuntimeError("name unavailable")

            @property
            def assumptions(self):
                raise RuntimeError("assumptions unavailable")

            @property
            def failure_boundaries(self):
                raise RuntimeError("boundaries unavailable")

            def simulate(self, context, horizon):
                return [
                    Trace("invalid", "s", "baseline", ({"x": 0}, {"x": 1}))
                ]

        spec = MacroSpec(
            "invalid requirement",
            ("x",),
            (InvalidRequirement(),),
            EquivalenceSpec(("x",)),
        )
        certificate = SatisfactionEvaluator().evaluate(
            InvalidMetadataModel(), spec, Context()
        )

        self.assertFalse(certificate.satisfied)
        self.assertEqual(certificate.model_name, "InvalidMetadataModel")
        self.assertEqual(certificate.confidence.assumption_reliability, 0.0)
        self.assertEqual(len(certificate.checks), 2)
        self.assertTrue(all(not item.passed for item in certificate.checks))
        self.assertTrue(any("metadata" in item for item in certificate.failure_boundaries))

    def test_probe_exception_becomes_a_blocking_counterexample(self):
        model = TupleTraceModel(
            "probe-target",
            (Trace("probe-target", "s", "baseline", ({"x": 0}, {"x": 1})),),
        )
        result = BidirectionalModelingEngine(
            realizer=Realizer(probes=(ExplodingProbe(),))
        ).realize(
            x_spec(),
            scenario_context(("s", "baseline")),
            (model,),
            ResourceBudget(max_simulations=2),
        )

        self.assertFalse(result.candidates)
        self.assertEqual(result.rejected[0].counterexamples[0].kind, "probe-error")
        self.assertTrue(result.rejected[0].counterexamples[0].blocking)
        self.assertTrue(result.truncated)

    def test_information_gain_uses_declared_priors_not_ranking_scores(self):
        model = TupleTraceModel(
            "information-source",
            (Trace("information-source", "s", "baseline", ({"x": 0}, {"x": 1})),),
        )
        hypotheses = (
            PurposeHypothesis(
                "A",
                PurposeLevel.FUNCTION,
                x_spec("A"),
                prior=0.5,
                predictions={"distinguish": 1.0},
            ),
            PurposeHypothesis(
                "B",
                PurposeLevel.FUNCTION,
                x_spec("B"),
                prior=0.5,
                predictions={"distinguish": 0.0},
            ),
        )
        experiment = Experiment("distinguish", "Which outcome occurs?")
        engine = BidirectionalModelingEngine()
        context = scenario_context(("s", "baseline"))
        baseline = engine.interpret(model, context, hypotheses, experiments=(experiment,))
        skewed = engine.interpret(
            model,
            context,
            hypotheses,
            evidence=(Evidence("extra support", "A", 1.0),),
            experiments=(experiment,),
        )

        self.assertAlmostEqual(
            baseline.discriminating_query.expected_information_gain, 1.0
        )
        self.assertAlmostEqual(
            skewed.discriminating_query.expected_information_gain,
            baseline.discriminating_query.expected_information_gain,
        )

    def test_macro_tolerance_is_the_default_numeric_error_bound(self):
        model = TupleTraceModel(
            "approximate",
            (Trace("approximate", "s", "baseline", ({"x": 0}, {"x": 1.5})),),
        )
        strict = x_spec("strict")
        approximate = MacroSpec(
            "approximate",
            ("x",),
            (FieldRequirement("x objective", "x", "eq", 1),),
            EquivalenceSpec(("x",)),
            tolerance=1.0,
        )
        context = scenario_context(("s", "baseline"))
        strict_result = BidirectionalModelingEngine().realize(strict, context, (model,))
        approximate_result = BidirectionalModelingEngine().realize(
            approximate, context, (model,)
        )

        self.assertFalse(strict_result.candidates)
        self.assertTrue(approximate_result.candidates)

    def test_effect_generator_excludes_fields_missing_mid_trace(self):
        model = TupleTraceModel(
            "sparse-snapshots",
            (
                Trace(
                    "sparse-snapshots",
                    "s",
                    "baseline",
                    ({"x": 0, "stable": 1}, {"stable": 1}, {"x": 1, "stable": 1}),
                ),
            ),
        )
        result = BidirectionalModelingEngine().interpret(
            model,
            scenario_context(("s", "baseline")),
            ObservedEffectGenerator(horizon=2),
        )
        names = tuple(item.hypothesis.name for item in result.candidates)

        self.assertTrue(any("stable" in name for name in names))
        self.assertFalse(any(" x" in name or name.startswith("x") for name in names))

    def test_negative_costs_and_tolerances_are_rejected(self):
        with self.assertRaises(ValueError):
            Experiment("bad", "bad", cost=-1)
        with self.assertRaises(ValueError):
            Experiment("bad", "bad", cost=float("nan"))
        with self.assertRaises(ValueError):
            ResourceBudget(max_cost=-1)
        with self.assertRaises(ValueError):
            FieldRequirement("bad", "x", "eq", 1, tolerance=-1)
        with self.assertRaises(ValueError):
            MacroSpec(
                "bad",
                ("x",),
                (),
                EquivalenceSpec(("x",)),
                tolerance=float("nan"),
            )

    def test_registry_exact_candidate_limit_is_not_truncation(self):
        model = TupleTraceModel(
            "only",
            (Trace("only", "s", "baseline", ({"x": 0}, {"x": 1})),),
        )
        result = BidirectionalModelingEngine().realize(
            x_spec(),
            scenario_context(("s", "baseline")),
            RegistryGenerator((model,)),
            ResourceBudget(max_candidates=1, max_simulations=1),
        )

        self.assertEqual(result.searched_candidates, 1)
        self.assertFalse(result.truncated)
        self.assertEqual(tuple(item.model.name for item in result.candidates), ("only",))

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
