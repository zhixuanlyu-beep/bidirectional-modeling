import unittest
from dataclasses import replace

from bidirectional_modeling import (
    BidirectionalModelingEngine,
    Correspondence,
    EquivalenceSpec,
    ResourceBudget,
    Scale,
    ScenarioKey,
)
from bidirectional_modeling.examples import scale_correspondence_scenario


class DelegatingThirdPartyModel:
    """Expose the same traces without inheriting framework coverage authority."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.name = delegate.name
        self.metrics = delegate.metrics
        self.assumptions = delegate.assumptions
        self.failure_boundaries = delegate.failure_boundaries
        self.prior_reliability = delegate.prior_reliability
        self.capabilities = delegate.capabilities

    def simulate(self, context, horizon):
        return self.delegate.simulate(context, horizon)


class CorrespondenceTests(unittest.TestCase):
    def setUp(self):
        (
            self.correspondence,
            self.lower_model,
            self.upper_model,
            self.lower_context,
            self.upper_context,
        ) = scale_correspondence_scenario()
        self.engine = BidirectionalModelingEngine()

    def verify(self, correspondence=None, upper_model=None, budget=None):
        return self.engine.verify_correspondence(
            correspondence or self.correspondence,
            self.lower_model,
            upper_model or self.upper_model,
            self.lower_context,
            self.upper_context,
            horizon=2,
            budget=budget,
        )

    def test_many_to_one_dynamic_projection_is_certified(self):
        certificate = self.verify()

        self.assertTrue(certificate.passed)
        self.assertTrue(certificate.complete)
        self.assertTrue(certificate.commutes)
        self.assertEqual(certificate.lower_scenarios, 2)
        self.assertEqual(certificate.upper_scenarios, 1)
        self.assertEqual(certificate.paired_scenarios, 2)
        self.assertEqual(certificate.covered_upper_scenarios, 1)
        self.assertEqual(certificate.simulations_used, 3)
        self.assertEqual(
            certificate.lower_coverage_authority,
            "framework-finite-state",
        )
        self.assertTrue(self.engine.scale_graph.has_certified_direct("micro", "macro"))
        path = self.engine.scale_graph.find_paths("micro", "macro")[0]
        self.assertTrue(path.edgewise_certified)
        self.assertTrue(path.end_to_end_certified)

    def test_non_commuting_projection_returns_step_witness_and_is_not_recorded(self):
        bad = replace(
            self.correspondence,
            name="off-by-one-sum",
            projection=lambda snapshot, _context: {
                "total": snapshot["left"] + snapshot["right"] + 1
            },
        )
        certificate = self.verify(bad)

        self.assertFalse(certificate.passed)
        self.assertTrue(certificate.complete)
        self.assertFalse(certificate.commutes)
        mismatches = [
            item
            for item in certificate.counterexamples
            if item.kind == "non-commuting-step"
        ]
        self.assertTrue(mismatches)
        self.assertEqual(mismatches[0].step, 0)
        self.assertEqual(mismatches[0].projected_snapshot["total"], 3)
        self.assertFalse(
            self.engine.scale_graph.has_certified_direct("micro", "macro")
        )

    def test_shared_budget_cannot_prove_only_one_side_of_diagram(self):
        certificate = self.verify(
            budget=ResourceBudget(max_simulations=2)
        )

        self.assertFalse(certificate.passed)
        self.assertFalse(certificate.complete)
        self.assertEqual(certificate.simulations_used, 2)
        self.assertTrue(
            any("shared simulation budget" in item for item in certificate.boundaries)
        )
        self.assertFalse(
            self.engine.scale_graph.has_certified_direct("micro", "macro")
        )

    def test_third_party_lower_domain_still_requires_a_caller_manifest(self):
        lower_model = DelegatingThirdPartyModel(self.lower_model)
        certificate = self.engine.verify_correspondence(
            self.correspondence,
            lower_model,
            self.upper_model,
            self.lower_context,
            self.upper_context,
            horizon=2,
        )

        self.assertFalse(certificate.passed)
        self.assertFalse(certificate.complete)
        self.assertTrue(certificate.commutes)
        self.assertEqual(certificate.lower_coverage_authority, "none")
        self.assertTrue(
            any("scenario manifest" in item for item in certificate.boundaries)
        )

    def test_caller_manifest_can_certify_a_third_party_lower_domain(self):
        lower_model = DelegatingThirdPartyModel(self.lower_model)
        lower_context = replace(
            self.lower_context,
            scenario_manifest=(
                ScenarioKey("partition-a", "baseline"),
                ScenarioKey("partition-b", "baseline"),
            ),
        )
        certificate = self.engine.verify_correspondence(
            self.correspondence,
            lower_model,
            self.upper_model,
            lower_context,
            self.upper_context,
            horizon=2,
        )

        self.assertTrue(certificate.passed)
        self.assertEqual(certificate.lower_coverage_authority, "context-manifest")

    def test_empty_domains_do_not_create_a_vacuous_correspondence(self):
        empty_lower = replace(
            self.lower_model,
            name="empty-lower",
            states={},
            initial_states=(),
        )
        empty_upper = replace(
            self.upper_model,
            name="empty-upper",
            states={},
            initial_states=(),
        )
        certificate = self.engine.verify_correspondence(
            self.correspondence,
            empty_lower,
            empty_upper,
            self.lower_context,
            self.upper_context,
            horizon=2,
        )

        self.assertFalse(certificate.passed)
        self.assertFalse(certificate.complete)
        self.assertEqual(
            {item.kind for item in certificate.counterexamples},
            {"empty-lower-domain", "empty-upper-domain"},
        )

    def test_upper_scenario_without_a_lower_preimage_blocks_completeness(self):
        upper_model = replace(
            self.upper_model,
            name="aggregate-with-extra-domain",
            states={
                "aggregate": {"total": 2},
                "uncovered": {"total": 2},
            },
            initial_states=("aggregate", "uncovered"),
        )
        certificate = self.verify(upper_model=upper_model)

        self.assertFalse(certificate.passed)
        self.assertFalse(certificate.complete)
        self.assertTrue(certificate.commutes)
        self.assertEqual(certificate.covered_upper_scenarios, 1)
        self.assertIn(
            "unmapped-upper-scenario",
            {item.kind for item in certificate.counterexamples},
        )

    def test_projection_must_produce_the_declared_upper_interface(self):
        incomplete_projection = replace(
            self.correspondence,
            name="missing-total",
            projection=lambda _snapshot, _context: {"other": 1},
        )
        certificate = self.verify(incomplete_projection)

        self.assertFalse(certificate.passed)
        self.assertTrue(certificate.complete)
        self.assertFalse(certificate.commutes)
        self.assertEqual(
            {item.kind for item in certificate.counterexamples},
            {"projection-failed"},
        )

    def test_edgewise_scale_path_does_not_become_a_transitive_proof(self):
        first = self.verify()
        self.assertTrue(first.passed)

        semantic_scale = Scale(
            "semantic",
            ("total",),
            EquivalenceSpec(("total",)),
        )
        second_correspondence = Correspondence(
            "read-total",
            self.correspondence.upper_scale,
            semantic_scale,
            lambda snapshot, _context: {"total": snapshot["total"]},
        )
        semantic_model = replace(
            self.upper_model,
            name="semantic-total-dynamics",
        )
        second = self.engine.verify_correspondence(
            second_correspondence,
            self.upper_model,
            semantic_model,
            self.upper_context,
            self.upper_context,
            horizon=2,
        )
        self.assertTrue(second.passed)

        path = self.engine.scale_graph.find_paths("micro", "semantic")[0]
        self.assertEqual(path.scales, ("micro", "macro", "semantic"))
        self.assertTrue(path.edgewise_certified)
        self.assertFalse(path.end_to_end_certified)
        self.assertFalse(
            self.engine.scale_graph.has_certified_direct("micro", "semantic")
        )


if __name__ == "__main__":
    unittest.main()
