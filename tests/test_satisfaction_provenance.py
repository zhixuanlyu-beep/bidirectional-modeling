import unittest
from dataclasses import replace

from bidirectional_modeling import (
    BidirectionalModelingEngine,
    CheckResult,
    Context,
    CustomRequirement,
    EquivalenceSpec,
    MacroSpec,
    ResidualQuotientAnalyzer,
    RequirementCategory,
    SatisfactionEvaluator,
    macro_spec_fingerprint,
)
from bidirectional_modeling.examples import (
    residual_quotient_scenario,
    software_scenario,
)
from bidirectional_modeling.cli import build_demo_report


class SatisfactionProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.spec, self.context, models = software_scenario()
        self.model = models[0]
        self.evaluator = SatisfactionEvaluator()

    def test_certificate_binds_spec_context_and_observed_evidence(self):
        batch = self.evaluator.collect(
            self.model, self.context, self.spec.horizon
        )
        certificate = self.evaluator.evaluate_batch(
            self.model, self.spec, self.context, batch
        )

        self.assertTrue(certificate.satisfied)
        self.assertTrue(batch.binds(self.model, self.context, self.spec.horizon))
        self.assertTrue(certificate.binds_specification(self.spec))
        self.assertTrue(certificate.binds_context(self.context))
        self.assertTrue(certificate.binds_evidence(self.model, batch.traces))
        self.assertTrue(certificate.binds_trace_batch(batch))
        for fingerprint in (
            certificate.spec_fingerprint,
            certificate.model_fingerprint,
            certificate.context_fingerprint,
            certificate.protocol_fingerprint,
            batch.protocol_fingerprint,
        ):
            self.assertEqual(len(fingerprint), 64)

    def test_same_name_different_spec_cannot_reuse_certificate(self):
        certificate = self.evaluator.evaluate(
            self.model, self.spec, self.context
        )
        changed = replace(self.spec, horizon=self.spec.horizon + 1)

        self.assertEqual(certificate.spec_name, changed.name)
        self.assertNotEqual(
            certificate.spec_fingerprint,
            macro_spec_fingerprint(changed),
        )
        self.assertFalse(certificate.binds_specification(changed))

    def test_trace_batch_cannot_be_replayed_across_contexts(self):
        batch = self.evaluator.collect(
            self.model, self.context, self.spec.horizon
        )
        changed_context = replace(self.context, observer="automated-auditor")
        certificate = self.evaluator.evaluate_batch(
            self.model,
            self.spec,
            changed_context,
            batch,
        )

        self.assertFalse(batch.binds(
            self.model, changed_context, self.spec.horizon
        ))
        self.assertFalse(certificate.complete)
        self.assertFalse(certificate.satisfied)
        self.assertEqual(certificate.confidence.coverage, 0.0)
        self.assertTrue(
            any(
                "trace batch context" in boundary
                for boundary in certificate.failure_boundaries
            )
        )

    def test_trace_batch_cannot_be_replayed_at_another_horizon(self):
        batch = self.evaluator.collect(
            self.model, self.context, self.spec.horizon
        )
        changed = replace(self.spec, horizon=self.spec.horizon + 1)
        certificate = self.evaluator.evaluate_batch(
            self.model,
            changed,
            self.context,
            batch,
        )

        self.assertFalse(certificate.complete)
        self.assertFalse(certificate.satisfied)
        self.assertTrue(
            any(
                "does not match requested horizon" in boundary
                for boundary in certificate.failure_boundaries
            )
        )

    def test_trace_batch_metadata_tampering_is_detected(self):
        batch = self.evaluator.collect(
            self.model, self.context, self.spec.horizon
        )
        with self.assertRaisesRegex(ValueError, "protocol fingerprint"):
            replace(batch, coverage_authority="substituted-authority")

        tampered = batch
        object.__setattr__(tampered, "coverage", 0.25)
        certificate = self.evaluator.evaluate_batch(
            self.model,
            self.spec,
            self.context,
            tampered,
        )

        self.assertFalse(tampered.binds(
            self.model, self.context, self.spec.horizon
        ))
        self.assertFalse(certificate.complete)
        self.assertTrue(
            any(
                "metadata changed after collection" in boundary
                for boundary in certificate.failure_boundaries
            )
        )

    def test_certificate_metadata_tampering_is_rejected(self):
        certificate = self.evaluator.evaluate(
            self.model, self.spec, self.context
        )

        with self.assertRaisesRegex(ValueError, "protocol fingerprint"):
            replace(certificate, context_fingerprint="0" * 64)

    def test_realization_result_rejects_a_substituted_spec(self):
        result = BidirectionalModelingEngine().realize(
            self.spec,
            self.context,
            (self.model,),
        )
        self.assertTrue(result.candidates)

        with self.assertRaisesRegex(ValueError, "bind the result specification"):
            replace(result, spec=replace(self.spec, horizon=3))

    def test_context_and_requirement_callback_inputs_are_isolated(self):
        source_environment = {"queue_size": 3, "nested": {"owner": "caller"}}
        context = Context(
            environment=source_environment,
            scale=self.context.scale,
        )
        source_environment["nested"]["owner"] = "external mutation"
        self.assertEqual(context.environment["nested"]["owner"], "caller")

        original_transition = self.model.transition

        def mutating_transition(state, action, callback_context):
            callback_context.environment["nested"]["owner"] = "model mutation"
            return original_transition(state, action, callback_context)

        model = replace(self.model, transition=mutating_transition)
        batch = self.evaluator.collect(model, context, self.spec.horizon)
        batch_digest = batch.protocol_fingerprint

        def mutating_checker(_model, traces, callback_context):
            traces[0].snapshots[-1]["completed"] = -100
            callback_context.environment["nested"]["owner"] = "checker mutation"
            return CheckResult(
                "isolated callback",
                RequirementCategory.OBJECTIVE,
                True,
                "isolated",
                "isolated",
                1.0,
            )

        spec = MacroSpec(
            name="isolated callback inputs",
            observables=self.spec.observables,
            objectives=(
                CustomRequirement(
                    "isolated callback",
                    RequirementCategory.OBJECTIVE,
                    mutating_checker,
                    semantic_id="isolated-callback-v1",
                ),
            ),
            equivalence=self.spec.equivalence,
            horizon=self.spec.horizon,
        )
        certificate = self.evaluator.evaluate_batch(
            model, spec, context, batch
        )

        self.assertTrue(certificate.satisfied)
        self.assertEqual(context.environment["nested"]["owner"], "caller")
        self.assertEqual(batch.protocol_fingerprint, batch_digest)
        self.assertNotEqual(batch.traces[0].snapshots[-1]["completed"], -100)

    def test_requirement_without_semantic_identity_fails_closed(self):
        class OpaqueRequirement:
            category = RequirementCategory.OBJECTIVE

            def evaluate(self, _model, _traces, _context):
                return CheckResult(
                    "opaque",
                    RequirementCategory.OBJECTIVE,
                    True,
                    True,
                    True,
                    1.0,
                )

        spec = MacroSpec(
            name="opaque requirement",
            observables=self.spec.observables,
            objectives=(OpaqueRequirement(),),
            equivalence=self.spec.equivalence,
            horizon=self.spec.horizon,
        )
        certificate = self.evaluator.evaluate(self.model, spec, self.context)

        self.assertFalse(certificate.complete)
        self.assertFalse(certificate.satisfied)
        self.assertTrue(
            any(
                "semantic_signature" in boundary
                for boundary in certificate.failure_boundaries
            )
        )

    def test_cli_exposes_satisfaction_and_residual_provenance(self):
        report = build_demo_report()
        satisfaction = report["realize"]["pareto_candidates"][0]
        residual = report["residual_quotient"]

        for field_name in (
            "spec_fingerprint",
            "model_fingerprint",
            "context_fingerprint",
            "trace_batch_fingerprint",
            "protocol_fingerprint",
        ):
            self.assertEqual(len(satisfaction[field_name]), 64)
        for field_name in (
            "model_fingerprint",
            "context_fingerprint",
            "equivalence_fingerprint",
            "protocol_fingerprint",
        ):
            self.assertEqual(len(residual[field_name]), 64)
        self.assertEqual(residual["bounds"]["max_states"], 1_000)


class ResidualProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.equivalence, self.context, self.model = residual_quotient_scenario()
        self.analyzer = ResidualQuotientAnalyzer()

    def test_report_binds_context_equivalence_model_and_protocol(self):
        report = self.analyzer.analyze(
            self.model, self.equivalence, self.context
        )

        self.assertTrue(report.minimal)
        self.assertTrue(report.binds_context(self.context))
        self.assertTrue(report.binds_equivalence(self.equivalence))
        for fingerprint in (
            report.model_fingerprint,
            report.context_fingerprint,
            report.equivalence_fingerprint,
            report.protocol_fingerprint,
        ):
            self.assertEqual(len(fingerprint), 64)

        changed_equivalence = EquivalenceSpec(
            self.equivalence.fields,
            {"signal": 2.0},
        )
        self.assertFalse(report.binds_equivalence(changed_equivalence))

    def test_same_name_changed_model_has_different_observed_fingerprint(self):
        baseline = self.analyzer.analyze(
            self.model, self.equivalence, self.context
        )

        def changed_transition(state, action, _context):
            if action != "probe" or state["mode"] == "terminal":
                return dict(state)
            return {"mode": "terminal", "copy": 0, "signal": -1}

        changed_model = replace(self.model, transition=changed_transition)
        changed = self.analyzer.analyze(
            changed_model, self.equivalence, self.context
        )

        self.assertEqual(baseline.model_name, changed.model_name)
        self.assertNotEqual(
            baseline.model_fingerprint,
            changed.model_fingerprint,
        )
        self.assertNotEqual(
            baseline.protocol_fingerprint,
            changed.protocol_fingerprint,
        )

    def test_protocol_records_bounds_even_when_evidence_is_unchanged(self):
        baseline = self.analyzer.analyze(
            self.model, self.equivalence, self.context, max_states=1_000
        )
        different_bound = self.analyzer.analyze(
            self.model, self.equivalence, self.context, max_states=999
        )

        self.assertEqual(
            baseline.model_fingerprint,
            different_bound.model_fingerprint,
        )
        self.assertNotEqual(
            baseline.protocol_fingerprint,
            different_bound.protocol_fingerprint,
        )

    def test_equivalence_signature_tampering_is_rejected(self):
        report = self.analyzer.analyze(
            self.model, self.equivalence, self.context
        )

        with self.assertRaisesRegex(ValueError, "equivalence signature"):
            replace(report, equivalence_signature=(("other",), ()))

        with self.assertRaisesRegex(ValueError, "protocol fingerprint"):
            replace(report, max_states=report.max_states + 1)


if __name__ == "__main__":
    unittest.main()
