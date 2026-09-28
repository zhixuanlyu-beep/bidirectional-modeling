from bidirectional_modeling.extensions.concepts import ConceptLibrary

from bidirectional_modeling.refinement import ClosureAnalyzer
import unittest
from dataclasses import replace

from bidirectional_modeling.engine import (BidirectionalModelingEngine)
from bidirectional_modeling.core import (Concept)
from bidirectional_modeling.probes import (HorizonExtensionProbe)
from bidirectional_modeling import (ObservedEffectGenerator, ParametricCandidateGenerator, PurposeHypothesis, PurposeLevel, Realizer, ResourceBudget)
from bidirectional_modeling.examples import (
    organization_interpretation_scenario,
    science_closure_scenario,
    software_scenario,
)


class FrameworkTests(unittest.TestCase):
    def setUp(self):
        self.engine = BidirectionalModelingEngine(
            realizer=Realizer(probes=(HorizonExtensionProbe(extra_steps=2, blocking=True),))
        )

    def test_explicit_extended_acceptance_returns_pareto_set(self):
        spec, context, models = software_scenario()
        result = self.engine.realize(spec, context, models)

        self.assertEqual(
            {candidate.model.name for candidate in result.candidates},
            {"sequential-safe-worker", "parallel-audited-worker"},
        )
        rejected = {candidate.model.name: candidate for candidate in result.rejected}
        self.assertIn("drop-and-report-worker", rejected)
        self.assertIn("short-horizon-worker", rejected)
        self.assertEqual(
            rejected["short-horizon-worker"].counterexamples[0].kind,
            "extended-specification-failure",
        )
        self.assertTrue(
            all(candidate.certificate.satisfied for candidate in result.candidates)
        )

    def test_interpretation_preserves_multiple_purposes_and_intention_caveats(self):
        context, model, hypotheses, experiments, evidence = (
            organization_interpretation_scenario()
        )
        result = self.engine.interpret(
            model, context, hypotheses, evidence, experiments
        )

        self.assertEqual(len(result.candidates), 3)
        self.assertTrue(result.non_identifiable)
        self.assertIsNotNone(result.discriminating_query)
        self.assertEqual(
            result.discriminating_query.experiment.name, "delegate-low-risk"
        )
        by_name = {item.hypothesis.name: item for item in result.candidates}
        self.assertEqual(tuple(by_name), tuple(sorted(by_name)))
        self.assertFalse(by_name["preserve central control"].direct_intent_evidence)
        self.assertTrue(by_name["preserve central control"].caveats)

    def test_observed_effects_are_generated_without_claiming_intention(self):
        context, model, _, _, _ = organization_interpretation_scenario()
        result = self.engine.interpret(
            model, context, ObservedEffectGenerator(horizon=1)
        )
        self.assertTrue(result.candidates)
        self.assertTrue(
            all(item.hypothesis.level == PurposeLevel.EFFECT for item in result.candidates)
        )

    def test_non_closure_exposes_hidden_velocity(self):
        spec, context, model = science_closure_scenario()
        report = ClosureAnalyzer().analyze(model, spec, context)

        self.assertFalse(report.closed)
        self.assertTrue(report.counterexamples)
        suggestions = sum(
            (item.suggested_refinements for item in report.counterexamples), ()
        )
        self.assertTrue(any("velocity" in suggestion for suggestion in suggestions))

    def test_counterexample_versions_concept_memory(self):
        spec, context, model = science_closure_scenario()
        counterexample = ClosureAnalyzer().analyze(model, spec, context).counterexamples[0]
        library = ConceptLibrary()
        library.add(
            Concept("position state", "equal position means equal macro state")
        )
        updated = library.refine_from_counterexample(
            "position state", counterexample
        , source="explicit caller review", reason="paired states refute the declared grouping", applicability="reviewed relation to the named concept")

        self.assertEqual(updated.version, 2)
        self.assertTrue(updated.negative_examples)
        self.assertTrue(updated.boundaries)
        self.assertTrue(any("velocity" in item for item in updated.candidate_definitions))

    def test_round_trips_require_independent_recovery_and_a_new_realization(self):
        spec, context, models = software_scenario()
        class IndependentRecoveryGenerator:
            independence_declared = True

            def generate(self, model, _context):
                return (
                    PurposeHypothesis(
                        "recovered reliable operation",
                        PurposeLevel.FUNCTION,
                        replace(spec, name="independently recovered operation"),
                    ),
                )

        hypotheses = IndependentRecoveryGenerator()
        macro_report = self.engine.macro_round_trip(
            spec, context, models, hypotheses
        )
        self.assertTrue(macro_report.compatibility_passed)
        self.assertFalse(hasattr(macro_report, "passed"))
        self.assertTrue(macro_report.independence_declared)

        clone = replace(models[0], name="sequential-safe-worker-clone")
        micro_report = self.engine.micro_round_trip(
            models[0],
            context,
            tuple(hypotheses.generate(models[0], context)),
            (clone,),
        )
        self.assertTrue(micro_report.passed)
        self.assertIn(
            "sequential-safe-worker-clone",
            micro_report.behaviorally_equivalent_models,
        )

    def test_parametric_generator_and_budget_are_explicit(self):
        spec, context, models = software_scenario()
        by_name = {model.name: model for model in models}

        def factory(parameters, _spec, _context):
            return by_name[parameters["design"]]

        generator = ParametricCandidateGenerator(
            {"design": tuple(by_name)}, factory
        )
        result = self.engine.realize(
            spec,
            context,
            generator,
            ResourceBudget(max_candidates=2),
        )
        self.assertEqual(result.searched_candidates, 2)
        self.assertTrue(result.truncated)


if __name__ == "__main__":
    unittest.main()

