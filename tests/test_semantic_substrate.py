import math
import unittest
from enum import Enum, IntEnum

from bidirectional_modeling import (
    ClosureAnalyzer,
    CompositionExperiment,
    CompositionRule,
    CompositionRuleSelector,
    CompositionTest,
    Context,
    EquivalenceSpec,
    Experiment,
    FieldRequirement,
    FiniteStateModel,
    InterpretationScoringPolicy,
    MacroSpec,
    ModelMetrics,
    ResidualQuotientAnalyzer,
    ResourceBudget,
    SatisfactionEvaluator,
    context_fingerprint,
)


ZERO_METRICS = ModelMetrics(0.0, 0.0, 0.0)


class SemanticSubstrateRegressionTests(unittest.TestCase):
    def test_closure_never_uses_repr_as_state_identity(self):
        class Token:
            def __init__(self, name):
                self.name = name

            def __repr__(self):
                return "<token>"

        def transition(state, action, _context):
            if action == "advance" and state["phase"] == 0:
                return {
                    "phase": 1,
                    "token": state["token"],
                    "signal": 0,
                }
            if action == "reveal" and state["phase"] == 1:
                return {
                    "phase": 1,
                    "token": state["token"],
                    "signal": int(state["token"].name == "b"),
                }
            return state

        model = FiniteStateModel(
            "repr-collision",
            {
                "a": {"phase": 0, "token": Token("a"), "signal": 0},
                "b": {"phase": 0, "token": Token("b"), "signal": 0},
            },
            ("a", "b"),
            ("advance", "reveal"),
            transition,
            lambda state, _context: {"signal": state["signal"]},
            ZERO_METRICS,
        )
        spec = MacroSpec(
            "signal",
            ("signal",),
            (),
            EquivalenceSpec(("signal",)),
            horizon=2,
        )

        report = ClosureAnalyzer().analyze(model, spec, Context(), max_depth=2)

        self.assertFalse(report.closed)
        self.assertFalse(report.complete)
        self.assertIn(
            "closure-analysis-error",
            {item.kind for item in report.counterexamples},
        )

    def test_opaque_macro_values_cannot_collide_through_repr(self):
        class Opaque:
            __hash__ = None

            def __init__(self, value):
                self.value = value

            def __repr__(self):
                return "<opaque>"

            def __eq__(self, other):
                return isinstance(other, Opaque) and self.value == other.value

        left = MacroSpec(
            "left",
            ("x",),
            (FieldRequirement("goal", "x", "eq", Opaque("a")),),
            EquivalenceSpec(("x",)),
        )
        right = MacroSpec(
            "right",
            ("x",),
            (FieldRequirement("goal", "x", "eq", Opaque("b")),),
            EquivalenceSpec(("x",)),
        )

        with self.assertRaisesRegex(TypeError, "deterministic structural identity"):
            left.semantically_equivalent(right)

    def test_nested_state_mutation_cannot_contaminate_later_rules(self):
        shared_states = {"seed": {"box": {"value": 0}}}
        experiment = CompositionExperiment(
            "mutation isolation",
            shared_states,
            ("seed",),
            ("go",),
            lambda state, _context: {"value": state["box"]["value"]},
            EquivalenceSpec(("value",)),
            (
                CompositionTest(
                    "stays zero",
                    "seed",
                    ("go",),
                    True,
                    {"value": 0},
                ),
            ),
        )

        def mutator(state, _action, _context):
            state["box"]["value"] = 1
            return state

        def unchanged(state, _action, _context):
            return state

        mutating_rule = CompositionRule("mutator", mutator, 1.0)
        clean_rule = CompositionRule("clean", unchanged, 1.0)
        selector = CompositionRuleSelector()

        report = selector.select(
            (mutating_rule, clean_rule),
            (experiment,),
        )
        clean = next(
            item for item in report.evaluations if item.rule.name == "clean"
        )

        self.assertTrue(clean.cases[0].tests[0].passed)
        self.assertEqual(shared_states["seed"]["box"]["value"], 0)

    def test_residual_certificate_rejects_nondeterministic_transition(self):
        calls = {"count": 0}

        def alternating(_state, _action, _context):
            calls["count"] += 1
            return {"signal": calls["count"] % 2}

        model = FiniteStateModel(
            "alternating",
            {"start": {"signal": 0}},
            ("start",),
            ("tick",),
            alternating,
            lambda state, _context: {"signal": state["signal"]},
            ZERO_METRICS,
        )

        report = ResidualQuotientAnalyzer().analyze(
            model,
            EquivalenceSpec(("signal",)),
            Context(),
        )

        self.assertFalse(report.complete)
        self.assertFalse(report.minimal)
        self.assertTrue(
            any("non-deterministic" in item for item in report.boundaries)
        )

    def test_residual_certificate_rejects_nondeterministic_action_support(self):
        calls = {"count": 0}

        def alternating_support(_state, action, _context):
            if action == "noop":
                return True
            calls["count"] += 1
            return calls["count"] % 2 == 1

        model = FiniteStateModel(
            "alternating-support",
            {"start": {"signal": 0}},
            ("start",),
            ("tick",),
            lambda state, _action, _context: state,
            lambda state, _context: {"signal": state["signal"]},
            ZERO_METRICS,
            applicable=alternating_support,
        )

        report = ResidualQuotientAnalyzer().analyze(
            model,
            EquivalenceSpec(("signal",)),
            Context(),
        )

        self.assertFalse(report.complete)
        self.assertFalse(report.minimal)
        self.assertTrue(
            any(
                "non-deterministic action support" in item
                for item in report.boundaries
            )
        )

    def test_satisfaction_certificate_rejects_nondeterministic_readout(self):
        calls = {"count": 0}

        def alternating_readout(_state, _context):
            calls["count"] += 1
            return {"signal": calls["count"] % 2}

        model = FiniteStateModel(
            "alternating-readout",
            {"start": {"signal": 0}},
            ("start",),
            (),
            lambda state, _action, _context: state,
            alternating_readout,
            ZERO_METRICS,
        )
        spec = MacroSpec(
            "signal",
            ("signal",),
            (FieldRequirement("zero", "signal", "eq", 0),),
            EquivalenceSpec(("signal",)),
        )

        certificate = SatisfactionEvaluator().evaluate(model, spec, Context())

        self.assertFalse(certificate.satisfied)
        self.assertFalse(certificate.complete)
        self.assertTrue(
            any(
                "non-deterministic readout" in boundary
                for boundary in certificate.failure_boundaries
            )
        )

    def test_cyclic_context_values_fail_explicitly(self):
        cyclic = []
        cyclic.append(cyclic)

        with self.assertRaisesRegex(TypeError, "cyclic containers"):
            Context(environment={"cycle": cyclic}).semantic_signature()

    def test_enum_values_do_not_collide_with_their_primitive_values(self):
        class TextMode(str, Enum):
            READY = "ready"

        class NumericMode(IntEnum):
            READY = 1

        self.assertNotEqual(
            context_fingerprint(Context(environment={"mode": TextMode.READY})),
            context_fingerprint(Context(environment={"mode": "ready"})),
        )
        self.assertNotEqual(
            context_fingerprint(Context(environment={"mode": NumericMode.READY})),
            context_fingerprint(Context(environment={"mode": 1})),
        )

    def test_initial_states_respect_the_residual_state_budget(self):
        states = {"s%d" % index: {"signal": index} for index in range(3)}
        model = FiniteStateModel(
            "initial-overflow",
            states,
            tuple(states),
            (),
            lambda state, _action, _context: state,
            lambda state, _context: {"signal": state["signal"]},
            ZERO_METRICS,
        )

        report = ResidualQuotientAnalyzer().analyze(
            model,
            EquivalenceSpec(("signal",)),
            Context(),
            max_states=1,
        )

        self.assertLessEqual(report.explored_states, 1)
        self.assertFalse(report.complete)
        self.assertFalse(report.minimal)
        self.assertTrue(any("max_states=1" in item for item in report.boundaries))

    def test_numeric_metadata_must_be_finite(self):
        with self.assertRaises(ValueError):
            ModelMetrics(0.0, math.nan, 0.0)
        with self.assertRaises(ValueError):
            ModelMetrics(0.0, math.inf, 0.0)
        with self.assertRaises(ValueError):
            EquivalenceSpec(("x",), {"x": math.nan})
        with self.assertRaises(ValueError):
            EquivalenceSpec(("x",), {"x": math.inf})
        with self.assertRaises(ValueError):
            FieldRequirement("x", "x", "eq", 0, tolerance=math.inf)
        with self.assertRaises(ValueError):
            MacroSpec(
                "x",
                ("x",),
                (),
                EquivalenceSpec(("x",)),
                tolerance=math.inf,
            )
        with self.assertRaises(ValueError):
            Experiment("x", "x", cost=math.inf)
        with self.assertRaises(ValueError):
            InterpretationScoringPolicy(coverage_weight=math.nan)
        with self.assertRaises(TypeError):
            ResourceBudget(max_candidates=1.5)

    def test_residual_state_indices_do_not_accept_python_negative_indexing(self):
        model = FiniteStateModel(
            "one-state",
            {"start": {"signal": 0}},
            ("start",),
            (),
            lambda state, _action, _context: state,
            lambda state, _context: {"signal": state["signal"]},
            ZERO_METRICS,
        )
        report = ResidualQuotientAnalyzer().analyze(
            model,
            EquivalenceSpec(("signal",)),
            Context(),
        )

        with self.assertRaises(IndexError):
            report.quotient.class_for_state(-1)


if __name__ == "__main__":
    unittest.main()
