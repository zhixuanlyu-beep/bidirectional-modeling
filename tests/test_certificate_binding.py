import unittest
from dataclasses import replace
from functools import partial

from bidirectional_modeling import (
    BidirectionalModelingEngine,
    ScaleGraph,
    ScenarioKey,
    correspondence_fingerprint,
)
from bidirectional_modeling.cli import build_demo_report
from bidirectional_modeling.examples import (
    scale_correspondence_scenario,
    scale_correspondence_suite,
)
from bidirectional_modeling.structural import callable_fingerprint


_GLOBAL_PROJECTION_OFFSET = 0


def _global_projection(snapshot, _context):
    return {
        "total": snapshot["left"]
        + snapshot["right"]
        + _GLOBAL_PROJECTION_OFFSET
    }


def _nested_global_projection(snapshot, _context):
    def offset():
        return _GLOBAL_PROJECTION_OFFSET

    return {"total": snapshot["left"] + snapshot["right"] + offset()}


def _offset_projection(snapshot, _context, offset=0):
    return {"total": snapshot["left"] + snapshot["right"] + offset}


class _BoundProjection:
    def __init__(self, offset):
        self.offset = offset

    def project(self, snapshot, _context):
        return {
            "total": snapshot["left"] + snapshot["right"] + self.offset
        }


class _CallableProjection:
    def __init__(self, offset):
        self.offset = offset

    def __call__(self, snapshot, _context):
        return {
            "total": snapshot["left"] + snapshot["right"] + self.offset
        }


class _SlottedProjection:
    __slots__ = ("offset",)

    def __init__(self, offset):
        self.offset = offset

    def __call__(self, snapshot, _context):
        return {
            "total": snapshot["left"] + snapshot["right"] + self.offset
        }


class CertificateBindingTests(unittest.TestCase):
    def setUp(self):
        (
            self.correspondence,
            self.lower_model,
            self.upper_model,
            self.lower_context,
            self.upper_context,
        ) = scale_correspondence_scenario()
        self.engine = BidirectionalModelingEngine()

    def validate(self, correspondence=None, lower_model=None, lower_context=None):
        return self.engine.verify_correspondence(
            correspondence or self.correspondence,
            lower_model or self.lower_model,
            self.upper_model,
            lower_context or self.lower_context,
            self.upper_context,
            horizon=2,
            record=False,
        )

    def test_certificate_cannot_be_replayed_for_a_different_projection(self):
        certificate = self.validate()
        substitute = replace(
            self.correspondence,
            projection=lambda snapshot, _context: {
                "total": snapshot["left"] + snapshot["right"] + 99
            },
        )

        self.assertTrue(certificate.passed)
        self.assertNotEqual(
            certificate.correspondence_fingerprint,
            correspondence_fingerprint(substitute),
        )
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            ScaleGraph().add_verified(substitute, certificate)

    def test_certificate_cannot_be_replayed_for_a_scenario_remapping(self):
        certificate = self.validate()
        substitute = replace(
            self.correspondence,
            scenario_projection=lambda key: ScenarioKey(
                "different-aggregate", key.intervention
            ),
        )

        self.assertNotEqual(
            certificate.correspondence_fingerprint,
            correspondence_fingerprint(substitute),
        )
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            ScaleGraph().add_verified(substitute, certificate)

    def test_global_projection_binding_drift_invalidates_the_certificate(self):
        global _GLOBAL_PROJECTION_OFFSET

        correspondence = replace(
            self.correspondence,
            name="global-sum",
            projection=_global_projection,
            projection_id=None,
        )
        certificate = self.validate(correspondence=correspondence)
        self.assertTrue(certificate.passed)
        nested_baseline = callable_fingerprint(_nested_global_projection)

        try:
            _GLOBAL_PROJECTION_OFFSET = 99
            self.assertNotEqual(
                nested_baseline,
                callable_fingerprint(_nested_global_projection),
            )
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                ScaleGraph().add_verified(correspondence, certificate)
        finally:
            _GLOBAL_PROJECTION_OFFSET = 0

    def test_callable_fingerprints_cover_partial_and_bound_state(self):
        self.assertNotEqual(
            callable_fingerprint(partial(_offset_projection, offset=0)),
            callable_fingerprint(partial(_offset_projection, offset=1)),
        )

        projector = _BoundProjection(0)
        baseline = callable_fingerprint(projector.project)
        projector.offset = 1
        self.assertNotEqual(baseline, callable_fingerprint(projector.project))

        callable_projector = _CallableProjection(0)
        baseline = callable_fingerprint(callable_projector)
        callable_projector.offset = 1
        self.assertNotEqual(
            baseline,
            callable_fingerprint(callable_projector),
        )

        with self.assertRaisesRegex(TypeError, "semantic ID"):
            callable_fingerprint(_SlottedProjection(0))
        self.assertEqual(
            len(
                callable_fingerprint(
                    _SlottedProjection(0),
                    semantic_id="slotted-projection-v1",
                )
            ),
            64,
        )

    def test_opaque_global_dependency_requires_a_semantic_id(self):
        scenario_projection = lambda key: ScenarioKey(
            "aggregate", key.intervention
        )
        implicit = replace(
            self.correspondence,
            scenario_projection=scenario_projection,
            scenario_projection_id=None,
        )
        with self.assertRaisesRegex(TypeError, "semantic ID"):
            correspondence_fingerprint(implicit)

        explicit = replace(
            implicit,
            scenario_projection_id="aggregate-scenario-v2",
        )
        self.assertEqual(len(correspondence_fingerprint(explicit)), 64)

    def test_certificate_records_complete_provenance_bindings(self):
        certificate = self.validate()

        self.assertTrue(certificate.binds_correspondence(self.correspondence))
        self.assertEqual(
            certificate.correspondence_fingerprint,
            correspondence_fingerprint(replace(self.correspondence)),
        )
        for field_name in (
            "correspondence_fingerprint",
            "lower_model_fingerprint",
            "upper_model_fingerprint",
            "protocol_fingerprint",
            "claim_fingerprint",
            "lower_context_fingerprint",
            "upper_context_fingerprint",
        ):
            fingerprint = getattr(certificate, field_name)
            self.assertEqual(len(fingerprint), 64)
            self.assertEqual(fingerprint, fingerprint.lower())

        with self.assertRaisesRegex(ValueError, "SHA-256"):
            replace(certificate, protocol_fingerprint="g" * 64)

    def test_correspondence_fingerprint_ignores_declaration_order(self):
        reordered = replace(
            self.correspondence,
            lower_scale=replace(
                self.correspondence.lower_scale,
                observables=tuple(
                    reversed(self.correspondence.lower_scale.observables)
                ),
            ),
            assumptions=tuple(reversed(self.correspondence.assumptions)),
        )

        self.assertEqual(
            correspondence_fingerprint(self.correspondence),
            correspondence_fingerprint(reordered),
        )

    def test_model_evidence_fingerprint_is_not_just_the_model_name(self):
        baseline = self.validate()
        states = {
            name: dict(state, hidden="different evidence")
            for name, state in self.lower_model.states.items()
        }
        altered_model = replace(self.lower_model, states=states)

        altered = self.validate(lower_model=altered_model)

        self.assertTrue(baseline.passed)
        self.assertTrue(altered.passed)
        self.assertEqual(baseline.lower_model_name, altered.lower_model_name)
        self.assertNotEqual(
            baseline.lower_model_fingerprint,
            altered.lower_model_fingerprint,
        )
        self.assertNotEqual(
            baseline.protocol_fingerprint,
            altered.protocol_fingerprint,
        )

    def test_context_change_changes_protocol_but_not_identical_trace_evidence(self):
        baseline = self.validate()
        shifted_context = replace(
            self.lower_context,
            environment={"provenance-domain": "shifted"},
        )

        shifted = self.validate(lower_context=shifted_context)

        self.assertTrue(shifted.passed)
        self.assertEqual(
            baseline.lower_model_fingerprint,
            shifted.lower_model_fingerprint,
        )
        self.assertNotEqual(
            baseline.lower_context_fingerprint,
            shifted.lower_context_fingerprint,
        )
        self.assertNotEqual(
            baseline.protocol_fingerprint,
            shifted.protocol_fingerprint,
        )

    def test_nondeterministic_projection_cannot_receive_a_certificate(self):
        calls = {"count": 0}

        def alternating(snapshot, _context):
            calls["count"] += 1
            return {
                "total": snapshot["left"]
                + snapshot["right"]
                + calls["count"] % 2
            }

        unstable = replace(
            self.correspondence,
            name="unstable-sum",
            projection=alternating,
            projection_id="unstable-sum-v1",
        )

        certificate = self.validate(correspondence=unstable)

        self.assertFalse(certificate.passed)
        self.assertFalse(certificate.complete)
        self.assertFalse(certificate.commutes)
        failures = [
            item.detail
            for item in certificate.counterexamples
            if item.kind == "projection-failed"
        ]
        self.assertTrue(
            any("non-deterministic snapshot projection" in item for item in failures)
        )

    def test_identity_that_becomes_opaque_fails_closed(self):
        binding = {"value": 0}

        def opaque_after_use(snapshot, _context):
            binding["value"] = object()
            return {"total": snapshot["left"] + snapshot["right"]}

        correspondence = replace(
            self.correspondence,
            name="opaque-after-use",
            projection=opaque_after_use,
            projection_id=None,
        )

        certificate = self.validate(correspondence=correspondence)

        self.assertFalse(certificate.passed)
        identity_failures = [
            item.detail
            for item in certificate.counterexamples
            if item.kind == "correspondence-identity-changed"
        ]
        self.assertTrue(
            any("could not be fingerprinted" in item for item in identity_failures)
        )
        self.assertFalse(certificate.binds_correspondence(correspondence))
        with self.assertRaises(ValueError):
            ScaleGraph().add_verified(correspondence, certificate)

    def test_projection_mutations_are_isolated_from_evidence_and_context(self):
        def mutating_projection(snapshot, context):
            total = snapshot["left"] + snapshot["right"]
            snapshot["left"] = 10_000
            context.environment["poisoned"] = True
            return {"total": total}

        isolated = replace(
            self.correspondence,
            name="isolated-sum",
            projection=mutating_projection,
        )

        certificate = self.validate(correspondence=isolated)

        self.assertTrue(certificate.passed)
        self.assertNotIn("poisoned", self.lower_context.environment)
        self.assertEqual(self.lower_model.states["partition-a"]["left"], 1)

    def test_suite_and_case_certificates_share_the_claim_fingerprint(self):
        correspondence, cases = scale_correspondence_suite()
        certificate = self.engine.verify_correspondence_suite(
            correspondence,
            cases,
            record=False,
        )

        self.assertTrue(certificate.passed)
        self.assertTrue(certificate.binds_correspondence(correspondence))
        self.assertEqual(len(certificate.protocol_fingerprint), 64)
        self.assertEqual(len(certificate.claim_fingerprint), 64)
        self.assertTrue(
            all(
                item.certificate.correspondence_fingerprint
                == certificate.correspondence_fingerprint
                for item in certificate.cases
            )
        )

    def test_stateful_correspondence_stops_a_suite_without_raising(self):
        correspondence, cases = scale_correspondence_suite()
        state = {"calls": 0}

        def stateful_projection(snapshot, _context):
            state["calls"] += 1
            return {"total": snapshot["left"] + snapshot["right"]}

        correspondence = replace(
            correspondence,
            name="stateful-sum",
            projection=stateful_projection,
            projection_id="stateful-sum-v1",
        )

        certificate = self.engine.verify_correspondence_suite(
            correspondence,
            cases,
            record=False,
        )

        self.assertFalse(certificate.passed)
        self.assertTrue(certificate.truncated)
        self.assertTrue(
            any("identity changed" in item for item in certificate.boundaries)
        )

    def test_demo_exposes_correspondence_provenance(self):
        report = build_demo_report()["correspondence"]

        self.assertEqual(len(report["correspondence_fingerprint"]), 64)
        self.assertEqual(len(report["protocol_fingerprint"]), 64)
        self.assertEqual(len(report["claim_fingerprint"]), 64)
        for case in report["cases"]:
            self.assertEqual(len(case["lower_model_fingerprint"]), 64)
            self.assertEqual(len(case["upper_model_fingerprint"]), 64)
            self.assertEqual(len(case["protocol_fingerprint"]), 64)
            self.assertEqual(len(case["claim_fingerprint"]), 64)


if __name__ == "__main__":
    unittest.main()
