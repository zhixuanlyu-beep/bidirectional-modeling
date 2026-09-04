import unittest
from dataclasses import replace

from bidirectional_modeling import (
    BidirectionalModelingEngine,
    CheckResult,
    CorrespondenceCaseRole,
    CustomRequirement,
    MacroSpec,
    ResidualQuotient,
    ResidualQuotientAnalyzer,
    RequirementCategory,
    SatisfactionEvaluator,
)
from bidirectional_modeling.examples import (
    residual_quotient_scenario,
    scale_correspondence_scenario,
    scale_correspondence_suite,
    software_scenario,
)


class ClaimIntegrityTests(unittest.TestCase):
    def test_satisfaction_result_substitution_is_rejected(self):
        spec, context, models = software_scenario()
        certificate = SatisfactionEvaluator().evaluate(models[0], spec, context)

        self.assertTrue(certificate.verify_integrity())
        self.assertEqual(len(certificate.claim_fingerprint), 64)
        with self.assertRaisesRegex(ValueError, "claim fingerprint"):
            replace(certificate, satisfied=not certificate.satisfied)
        with self.assertRaisesRegex(ValueError, "claim fingerprint"):
            replace(certificate, checks=())
        with self.assertRaisesRegex(ValueError, "claim fingerprint"):
            replace(certificate, verified_scenarios=0)

    def test_opaque_requirement_result_fails_closed(self):
        spec, context, models = software_scenario()

        def opaque_result(_model, _traces, _context):
            return CheckResult(
                "opaque result",
                RequirementCategory.OBJECTIVE,
                True,
                object(),
                "canonical evidence",
                1.0,
            )

        opaque_spec = MacroSpec(
            name="opaque result",
            observables=spec.observables,
            objectives=(
                CustomRequirement(
                    "opaque result",
                    RequirementCategory.OBJECTIVE,
                    opaque_result,
                    semantic_id="opaque-result-v1",
                ),
            ),
            equivalence=spec.equivalence,
            horizon=spec.horizon,
        )
        certificate = SatisfactionEvaluator().evaluate(
            models[0], opaque_spec, context
        )

        self.assertFalse(certificate.satisfied)
        self.assertTrue(certificate.verify_integrity())
        self.assertIn(
            "canonical primitive/container values",
            certificate.checks[0].detail,
        )

    def test_correspondence_protocol_and_claim_substitution_are_rejected(self):
        (
            correspondence,
            lower_model,
            upper_model,
            lower_context,
            upper_context,
        ) = scale_correspondence_scenario()
        certificate = BidirectionalModelingEngine().verify_correspondence(
            correspondence,
            lower_model,
            upper_model,
            lower_context,
            upper_context,
            horizon=2,
            record=False,
        )

        self.assertTrue(certificate.verify_integrity())
        self.assertEqual(len(certificate.claim_fingerprint), 64)
        with self.assertRaisesRegex(ValueError, "protocol fingerprint"):
            replace(certificate, protocol_fingerprint="0" * 64)
        with self.assertRaisesRegex(ValueError, "claim fingerprint"):
            replace(certificate, lower_scenarios=0)
        with self.assertRaisesRegex(ValueError, "protocol fingerprint"):
            replace(certificate, simulation_limit=certificate.simulation_limit + 1)

    def test_suite_case_role_substitution_is_rejected(self):
        correspondence, cases = scale_correspondence_suite()
        certificate = BidirectionalModelingEngine().verify_correspondence_suite(
            correspondence,
            (cases[0],),
            record=False,
        )
        promoted = replace(
            certificate.cases[0],
            role=CorrespondenceCaseRole.HOLDOUT,
            independent=True,
        )

        self.assertFalse(certificate.passed)
        self.assertTrue(certificate.verify_integrity())
        self.assertEqual(len(certificate.claim_fingerprint), 64)
        with self.assertRaisesRegex(ValueError, "protocol fingerprint"):
            replace(certificate, cases=(promoted,))
        with self.assertRaisesRegex(ValueError, "claim fingerprint"):
            replace(certificate, boundaries=("fabricated boundary",))

    def test_residual_result_and_nested_evidence_substitution_are_rejected(self):
        equivalence, context, model = residual_quotient_scenario()
        report = ResidualQuotientAnalyzer().analyze(model, equivalence, context)

        self.assertTrue(report.verify_integrity())
        self.assertEqual(len(report.claim_fingerprint), 64)
        with self.assertRaisesRegex(ValueError, "claim fingerprint"):
            replace(
                report,
                quotient=ResidualQuotient(
                    (),
                    (),
                    (),
                    (),
                    report.quotient.actions,
                    (),
                ),
            )

        report.quotient.states[0].micro_state["tampered"] = True
        self.assertFalse(report.verify_integrity())
        self.assertFalse(report.minimal)

    def test_scale_graph_evicts_evidence_after_projection_identity_drift(self):
        (
            correspondence,
            lower_model,
            upper_model,
            lower_context,
            upper_context,
        ) = scale_correspondence_scenario()
        projection_state = {"offset": 0}

        def mutable_projection(snapshot, _context):
            return {
                "total": snapshot["left"]
                + snapshot["right"]
                + projection_state["offset"]
            }

        correspondence = replace(
            correspondence,
            name="mutable-projection-sum",
            projection=mutable_projection,
            projection_id=None,
        )
        engine = BidirectionalModelingEngine()
        certificate = engine.verify_correspondence(
            correspondence,
            lower_model,
            upper_model,
            lower_context,
            upper_context,
            horizon=2,
        )

        self.assertTrue(certificate.passed)
        self.assertTrue(
            engine.scale_graph.has_certified_direct("micro", "macro")
        )
        projection_state["offset"] = 1
        self.assertFalse(certificate.binds_correspondence(correspondence))
        with self.assertRaisesRegex(ValueError, "no longer valid"):
            engine.scale_graph.certificate(correspondence.name)
        self.assertFalse(
            engine.scale_graph.has_certified_direct("micro", "macro")
        )
        self.assertEqual(engine.scale_graph.find_paths("micro", "macro"), ())


if __name__ == "__main__":
    unittest.main()
