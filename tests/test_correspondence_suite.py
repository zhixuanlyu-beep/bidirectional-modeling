import unittest
from dataclasses import replace

from bidirectional_modeling import (
    BidirectionalModelingEngine,
    Context,
    CorrespondenceCaseRole,
    CorrespondenceSuiteCertificate,
    CorrespondenceValidationCase,
    Intervention,
    ResourceBudget,
    ScenarioKey,
    context_fingerprint,
)
from bidirectional_modeling.examples import scale_correspondence_suite


class CorrespondenceSuiteTests(unittest.TestCase):
    def setUp(self):
        self.correspondence, self.cases = scale_correspondence_suite()
        self.engine = BidirectionalModelingEngine()

    def test_calibration_and_independent_holdout_create_a_suite_certificate(self):
        certificate = self.engine.verify_correspondence_suite(
            self.correspondence,
            self.cases,
        )

        self.assertTrue(certificate.passed)
        self.assertTrue(certificate.compatibility_passed)
        self.assertTrue(certificate.has_independent_holdout)
        self.assertTrue(certificate.complete)
        self.assertTrue(certificate.commutes)
        self.assertEqual(certificate.simulations_used, 6)
        self.assertEqual(len(certificate.cases), 2)
        self.assertNotEqual(
            certificate.cases[0].certificate.lower_context_fingerprint,
            certificate.cases[1].certificate.lower_context_fingerprint,
        )
        self.assertIsInstance(
            self.engine.scale_graph.certificate(self.correspondence.name),
            CorrespondenceSuiteCertificate,
        )

    def test_suite_evidence_upgrades_and_cannot_be_downgraded_by_one_case(self):
        calibration = self.cases[0]
        direct = self.engine.verify_correspondence(
            self.correspondence,
            calibration.lower_model,
            calibration.upper_model,
            calibration.lower_context,
            calibration.resolved_upper_context,
            horizon=calibration.horizon,
        )
        self.assertTrue(direct.passed)

        suite = self.engine.verify_correspondence_suite(
            self.correspondence,
            self.cases,
        )
        self.assertIs(
            self.engine.scale_graph.certificate(self.correspondence.name),
            suite,
        )

        repeated_direct = self.engine.verify_correspondence(
            self.correspondence,
            calibration.lower_model,
            calibration.upper_model,
            calibration.lower_context,
            calibration.resolved_upper_context,
            horizon=calibration.horizon,
        )
        self.assertTrue(repeated_direct.passed)
        self.assertIs(
            self.engine.scale_graph.certificate(self.correspondence.name),
            suite,
        )

    def test_calibration_only_proves_compatibility_not_holdout_validation(self):
        certificate = self.engine.verify_correspondence_suite(
            self.correspondence,
            (self.cases[0],),
        )

        self.assertTrue(certificate.compatibility_passed)
        self.assertFalse(certificate.has_independent_holdout)
        self.assertFalse(certificate.passed)
        self.assertFalse(
            self.engine.scale_graph.has_certified_direct("micro", "macro")
        )

    def test_a_holdout_label_without_independent_provenance_is_not_enough(self):
        unverified_holdout = replace(self.cases[1], independent=False)
        certificate = self.engine.verify_correspondence_suite(
            self.correspondence,
            (self.cases[0], unverified_holdout),
        )

        self.assertTrue(certificate.compatibility_passed)
        self.assertFalse(certificate.has_independent_holdout)
        self.assertFalse(certificate.passed)

    def test_a_failing_holdout_blocks_the_correspondence(self):
        holdout = self.cases[1]
        wrong_upper = replace(
            holdout.upper_model,
            name="wrong-holdout-aggregate",
            states={"aggregate": {"total": 6}},
        )
        failing_holdout = replace(holdout, upper_model=wrong_upper)
        certificate = self.engine.verify_correspondence_suite(
            self.correspondence,
            (self.cases[0], failing_holdout),
        )

        self.assertFalse(certificate.passed)
        self.assertFalse(certificate.compatibility_passed)
        self.assertTrue(certificate.has_independent_holdout)
        self.assertTrue(certificate.cases[0].certificate.passed)
        self.assertFalse(certificate.cases[1].certificate.commutes)
        self.assertIn(
            "non-commuting-step",
            {
                item.kind
                for item in certificate.cases[1].certificate.counterexamples
            },
        )

    def test_suite_budget_is_shared_across_calibration_and_holdout(self):
        certificate = self.engine.verify_correspondence_suite(
            self.correspondence,
            self.cases,
            ResourceBudget(max_simulations=3),
        )

        self.assertFalse(certificate.passed)
        self.assertTrue(certificate.truncated)
        self.assertEqual(certificate.simulations_used, 3)
        self.assertTrue(certificate.cases[0].certificate.passed)
        self.assertFalse(certificate.cases[1].certificate.complete)
        self.assertTrue(
            any(
                "suite simulation budget" in item
                for item in certificate.cases[1].certificate.boundaries
            )
        )

    def test_suite_case_names_are_unique(self):
        duplicate = replace(self.cases[1], name=self.cases[0].name)
        with self.assertRaises(ValueError):
            self.engine.verify_correspondence_suite(
                self.correspondence,
                (self.cases[0], duplicate),
            )

    def test_only_holdout_cases_may_claim_independent_provenance(self):
        with self.assertRaises(ValueError):
            CorrespondenceValidationCase(
                "invalid calibration",
                self.cases[0].lower_model,
                self.cases[0].upper_model,
                self.cases[0].lower_context,
                role=CorrespondenceCaseRole.CALIBRATION,
                independent=True,
            )

    def test_context_fingerprint_is_canonical_and_domain_sensitive(self):
        first = Context(
            environment={"b": {"value": 2}, "a": 1},
            assumptions=("second", "first"),
            interventions=(Intervention("stress", ("go",)),),
            scenario_manifest=(
                ScenarioKey("b", "stress"),
                ScenarioKey("a", "stress"),
            ),
        )
        reordered = Context(
            environment={"a": 1, "b": {"value": 2}},
            assumptions=("first", "second"),
            interventions=(Intervention("stress", ("go",)),),
            scenario_manifest=(
                ScenarioKey("a", "stress"),
                ScenarioKey("b", "stress"),
            ),
        )
        changed = replace(
            reordered,
            environment={"a": 1, "b": {"value": 3}},
        )

        self.assertEqual(context_fingerprint(first), context_fingerprint(reordered))
        self.assertNotEqual(context_fingerprint(first), context_fingerprint(changed))
        self.assertEqual(len(context_fingerprint(first)), 64)

        with self.assertRaises(TypeError):
            context_fingerprint(Context(environment={"opaque": object()}))


if __name__ == "__main__":
    unittest.main()
