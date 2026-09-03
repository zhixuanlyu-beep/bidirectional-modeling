import unittest
from dataclasses import replace
from enum import Enum

from bidirectional_modeling import (
    BidirectionalModelingEngine,
    Context,
    EquivalenceSpec,
    FiniteStateModel,
    ModelMetrics,
    MacroSpec,
    ResidualClass,
    ResidualQuotientAnalyzer,
    UndefinedTransition,
)
from bidirectional_modeling.examples import (
    partial_residual_scenario,
    residual_quotient_scenario,
)
from bidirectional_modeling.cli import build_demo_report


def finite_model(
    name,
    states,
    initial_states,
    actions,
    transition,
    readout=None,
    applicable=None,
):
    return FiniteStateModel(
        name,
        states,
        initial_states,
        actions,
        transition,
        readout or (lambda state, _context: {"signal": state["signal"]}),
        ModelMetrics(cost=1.0, complexity=1.0, risk=0.0),
        applicable=applicable,
    )


class ResidualQuotientTests(unittest.TestCase):
    def setUp(self):
        self.equivalence, self.context, self.model = residual_quotient_scenario()
        self.engine = BidirectionalModelingEngine()

    def discover(self, **kwargs):
        return self.engine.discover_residual_quotient(
            self.model, self.equivalence, self.context, **kwargs
        )

    def test_discovers_minimal_quotient_and_merges_irrelevant_detail(self):
        report = self.discover()

        self.assertTrue(report.complete)
        self.assertTrue(report.stable)
        self.assertTrue(report.congruent)
        self.assertTrue(report.minimal)
        self.assertEqual(report.explored_states, 5)
        self.assertEqual(report.quotient.class_count, 4)
        self.assertEqual(
            [level.class_count for level in report.filtration], [3, 4]
        )
        self.assertEqual(report.filtration[0].classes[0], (0, 1, 2))
        self.assertTrue(report.context_basis_reproduces_partition)
        self.assertEqual(report.context_basis, ((), ("probe",)))
        self.assertEqual(len(report.context_refinements), 1)
        self.assertEqual(report.context_refinements[0].class_count, 4)
        self.assertEqual(
            report.context_refinements[-1].state_classes,
            report.quotient.state_classes,
        )

        initial = dict(report.quotient.initial_state_classes)
        self.assertEqual(initial["left-a"], initial["left-b"])
        self.assertNotEqual(initial["left-a"], initial["right"])
        self.assertTrue(
            any(
                item.actions == ("probe",) and item.depth == 1
                for item in report.distinguishing_contexts
            )
        )
        self.assertTrue(
            any(
                item.actions == () and item.discovered_at_depth == 0
                for item in report.distinguishing_contexts
            )
        )
        negative = next(
            state.index
            for state in report.quotient.states
            if state.observation_signature == (-1,)
        )
        self.assertEqual(
            report.quotient.next_class(initial["left-a"], "probe"),
            report.quotient.class_for_state(negative),
        )
        with self.assertRaises(KeyError):
            report.quotient.next_class(initial["left-a"], "missing")
        with self.assertRaises(IndexError):
            report.quotient.class_for_state(100)

    def test_two_step_context_is_found_at_the_correct_filtration_depth(self):
        states = {
            "a0": {"node": "a0", "signal": 0},
            "b0": {"node": "b0", "signal": 0},
        }

        def transition(state, action, _context):
            if action != "advance":
                return state
            targets = {
                "a0": {"node": "a1", "signal": 0},
                "b0": {"node": "b1", "signal": 0},
                "a1": {"node": "negative", "signal": -1},
                "b1": {"node": "positive", "signal": 1},
                "negative": {"node": "negative", "signal": -1},
                "positive": {"node": "positive", "signal": 1},
            }
            return targets[state["node"]]

        model = finite_model(
            "two-step-separator",
            states,
            ("a0", "b0"),
            ("advance",),
            transition,
        )
        report = self.engine.discover_residual_quotient(
            model, EquivalenceSpec(("signal",)), Context()
        )

        self.assertTrue(report.minimal)
        self.assertEqual(
            [level.context_depth for level in report.filtration], [0, 1, 2]
        )
        initial = dict(report.quotient.initial_state_classes)
        self.assertNotEqual(initial["a0"], initial["b0"])
        initial_indices = {
            state.source_initial_state: state.index
            for state in report.quotient.states
            if not state.actions
        }
        self.assertTrue(
            any(
                {item.left_state, item.right_state}
                == {initial_indices["a0"], initial_indices["b0"]}
                and item.actions == ("advance", "advance")
                for item in report.distinguishing_contexts
            )
        )
        self.assertTrue(report.context_basis_reproduces_partition)
        self.assertIn(("advance",), report.context_basis)
        self.assertIn(("advance", "advance"), report.context_basis)
        self.assertTrue(
            all(
                left.class_count < right.class_count
                for left, right in zip(
                    report.context_refinements,
                    report.context_refinements[1:],
                )
            )
        )

    def test_context_depth_bound_returns_a_non_congruent_bounded_partition(self):
        report = self.discover(max_context_depth=0)

        self.assertTrue(report.complete)
        self.assertFalse(report.stable)
        self.assertFalse(report.congruent)
        self.assertFalse(report.minimal)
        self.assertEqual(report.quotient.class_count, 3)
        self.assertTrue(any("before stabilization" in item for item in report.boundaries))
        zero_class = dict(report.quotient.initial_state_classes)["left-a"]
        with self.assertRaises(ValueError):
            report.quotient.next_class(zero_class, "probe")

    def test_reachability_depth_bound_fails_closed(self):
        report = self.discover(max_reachability_depth=0)

        self.assertEqual(report.explored_states, 3)
        self.assertEqual(report.exploration_depth, 0)
        self.assertEqual(report.transition_evaluations, 0)
        self.assertFalse(report.complete)
        self.assertFalse(report.congruent)
        self.assertFalse(report.minimal)
        self.assertTrue(any("stopped at depth 0" in item for item in report.boundaries))
        self.assertTrue(any("not certified" in item for item in report.boundaries))

    def test_state_limit_fails_closed(self):
        report = self.discover(max_states=4)

        self.assertEqual(report.explored_states, 4)
        self.assertFalse(report.complete)
        self.assertFalse(report.minimal)
        self.assertTrue(any("max_states=4" in item for item in report.boundaries))

    def test_transition_failure_is_an_unknown_edge_not_bottom_semantics(self):
        def transition(state, action, _context):
            if action == "break":
                raise RuntimeError("not a declared partial transition")
            return state

        model = finite_model(
            "broken",
            {"s": {"signal": 0}},
            ("s",),
            ("break",),
            transition,
        )
        report = ResidualQuotientAnalyzer().analyze(
            model, self.equivalence, self.context
        )

        self.assertFalse(report.complete)
        self.assertFalse(report.congruent)
        self.assertFalse(report.minimal)
        self.assertTrue(any("not a declared partial" in item for item in report.boundaries))

    def test_declared_partial_support_is_semantic_and_keeps_completeness(self):
        equivalence, context, model = partial_residual_scenario()
        report = self.engine.discover_residual_quotient(
            model, equivalence, context
        )

        self.assertTrue(report.minimal)
        self.assertTrue(report.context_basis_reproduces_partition)
        self.assertEqual(report.quotient.class_count, 2)
        self.assertEqual(report.context_basis, ((), ("consume",)))
        classes = dict(report.quotient.initial_state_classes)
        enabled = classes["enabled"]
        disabled = classes["disabled"]
        self.assertEqual(
            report.quotient.next_class(enabled, "consume"), disabled
        )
        with self.assertRaises(UndefinedTransition):
            report.quotient.next_class(disabled, "consume")

        partial = next(
            transition
            for transition in report.quotient.transitions
            if transition.source_class == disabled
            and transition.action == "consume"
        )
        self.assertTrue(partial.complete)
        self.assertTrue(partial.undefined)
        self.assertTrue(partial.well_defined)
        self.assertFalse(partial.defined)
        self.assertEqual(partial.target_classes, ())
        witness = next(
            item
            for item in report.distinguishing_contexts
            if item.actions == ("consume",)
        )
        self.assertNotEqual(witness.left_defined, witness.right_defined)

        disabled_state = model.states["disabled"]
        self.assertFalse(model.supports(disabled_state, "consume", context))
        self.assertFalse(model.supports(disabled_state, "missing", context))
        with self.assertRaises(UndefinedTransition):
            model.step(disabled_state, "consume", context)
        with self.assertRaises(ValueError):
            model.step(disabled_state, "missing", context)

    def test_closure_reports_support_mismatch_and_can_refine_it(self):
        equivalence, context, model = partial_residual_scenario()
        spec = MacroSpec(
            "partial support",
            ("signal",),
            (),
            equivalence,
            horizon=1,
        )

        report = self.engine.check_closure(model, spec, context)
        self.assertFalse(report.closed)
        self.assertTrue(report.complete)
        self.assertEqual(
            report.counterexamples[0].kind,
            "dynamical-support-non-closure",
        )
        self.assertNotEqual(
            report.counterexamples[0].witness["left_defined"],
            report.counterexamples[0].witness["right_defined"],
        )
        self.assertIn("enabled", report.suggested_features)

        refined = self.engine.refine_until_closed(
            model,
            spec,
            context,
            lambda analysis, _spec, _model: analysis.suggested_features[0],
        )
        self.assertTrue(refined.closed)

    def test_applicability_hook_must_be_callable(self):
        with self.assertRaises(TypeError):
            finite_model(
                "invalid-applicability",
                {"s": {"signal": 0}},
                ("s",),
                (),
                lambda state, _action, _context: state,
                applicable="not-callable",
            )

    def test_empty_initial_domain_cannot_prove_a_vacuous_quotient(self):
        model = finite_model(
            "empty",
            {},
            (),
            (),
            lambda state, _action, _context: state,
        )
        report = ResidualQuotientAnalyzer().analyze(
            model, self.equivalence, self.context
        )

        self.assertFalse(report.complete)
        self.assertFalse(report.stable)
        self.assertFalse(report.congruent)
        self.assertFalse(report.minimal)
        self.assertEqual(report.explored_states, 0)
        self.assertEqual(report.filtration[0].class_count, 0)
        self.assertTrue(report.boundaries)

    def test_invalid_bounds_are_rejected(self):
        analyzer = ResidualQuotientAnalyzer()
        cases = (
            {"max_states": 0},
            {"max_reachability_depth": -1},
            {"max_context_depth": -1},
            {"max_context_tests": 0},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                analyzer.analyze(
                    self.model, self.equivalence, self.context, **kwargs
                )

    def test_context_test_budget_is_explicit_and_fail_closed(self):
        report = self.discover(max_context_tests=1)

        self.assertTrue(report.minimal)
        self.assertEqual(report.context_basis, ((),))
        self.assertFalse(report.context_basis_reproduces_partition)
        self.assertFalse(report.context_refinements)
        self.assertTrue(
            any("max_context_tests=1" in item for item in report.boundaries)
        )

    def test_structural_state_identity_handles_nested_supported_values(self):
        class Mode(Enum):
            READY = "ready"

        nested = {
            "signal": 0,
            "mapping": {"items": [1, True, None]},
            "tuple": (1.5, b"x"),
            "set": {"a", "b"},
            "frozen": frozenset({1, 2}),
            "mode": Mode.READY,
        }
        model = finite_model(
            "nested",
            {"first": nested, "duplicate": dict(nested)},
            ("first", "duplicate"),
            ("noop",),
            lambda state, _action, _context: state,
        )
        report = ResidualQuotientAnalyzer().analyze(
            model, self.equivalence, self.context
        )

        self.assertTrue(report.minimal)
        self.assertEqual(report.explored_states, 1)
        self.assertEqual(report.quotient.actions, ("noop",))
        self.assertEqual(
            report.quotient.initial_state_classes,
            (("first", 0), ("duplicate", 0)),
        )

    def test_opaque_state_values_fail_instead_of_using_process_repr(self):
        model = finite_model(
            "opaque",
            {"s": {"signal": 0, "opaque": object()}},
            ("s",),
            (),
            lambda state, _action, _context: state,
        )
        with self.assertRaisesRegex(TypeError, "deterministic structural identity"):
            ResidualQuotientAnalyzer().analyze(
                model, self.equivalence, self.context
            )

    def test_residual_class_requires_a_valid_representative(self):
        with self.assertRaises(ValueError):
            ResidualClass(0, (), 0)
        with self.assertRaises(ValueError):
            ResidualClass(0, (1,), 0)

    def test_context_is_bound_into_the_report(self):
        baseline = self.discover()
        shifted_context = replace(
            self.context, environment={"experiment": "holdout"}
        )
        shifted = self.engine.discover_residual_quotient(
            self.model, self.equivalence, shifted_context
        )

        self.assertNotEqual(
            baseline.context_fingerprint, shifted.context_fingerprint
        )

    def test_cli_demo_exposes_the_residual_certificate(self):
        section = build_demo_report()["residual_quotient"]

        self.assertTrue(section["minimal"])
        self.assertEqual(section["explored_states"], 5)
        self.assertEqual(section["class_count"], 4)
        self.assertEqual(
            [item["class_count"] for item in section["filtration"]], [3, 4]
        )
        self.assertTrue(section["distinguishing_contexts"])
        self.assertTrue(section["context_basis_reproduces_partition"])
        self.assertEqual(section["context_basis"], [[], ["probe"]])
        self.assertEqual(section["context_refinements"][0]["class_count"], 4)


if __name__ == "__main__":
    unittest.main()
