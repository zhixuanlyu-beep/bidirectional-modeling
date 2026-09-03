import math
import unittest
from dataclasses import replace

from bidirectional_modeling import (
    BidirectionalModelingEngine,
    CompositionExperiment,
    CompositionRule,
    CompositionRuleSelector,
    CompositionTest,
    Context,
    EquivalenceSpec,
)
from bidirectional_modeling.examples import composition_rule_scenario
from bidirectional_modeling.cli import build_demo_report


class CompositionRuleSelectionTests(unittest.TestCase):
    def setUp(self):
        self.experiments, self.rules = composition_rule_scenario()
        self.engine = BidirectionalModelingEngine()

    def select(self, rules=None, experiments=None, **kwargs):
        return self.engine.select_composition_rules(
            rules or self.rules,
            experiments or self.experiments,
            **kwargs,
        )

    def test_rejects_wrong_rules_and_selects_the_smallest_residual_model(self):
        report = self.select()

        self.assertTrue(report.unique_selection)
        self.assertEqual(report.selected_rule_names, ("parity",))
        self.assertEqual(
            [item.rule.name for item in report.ranked],
            ["parity", "delayed failure"],
        )
        self.assertEqual(
            {item.rule.name for item in report.rejected},
            {"constant zero", "forbid one"},
        )

        evaluations = {item.rule.name: item for item in report.evaluations}
        parity = evaluations["parity"]
        delayed = evaluations["delayed failure"]
        self.assertTrue(parity.certified)
        self.assertTrue(delayed.certified)
        self.assertEqual(parity.cases[0].class_count, 2)
        self.assertGreater(delayed.cases[0].class_count, 2)
        self.assertLess(
            parity.total_description_length,
            delayed.total_description_length,
        )
        self.assertEqual(parity.cases[0].state_description_length, 4.0)
        self.assertEqual(parity.cases[0].transition_description_length, 18.0)
        self.assertEqual(parity.cases[0].context_description_length, 1.0)
        self.assertEqual(parity.cases[0].exception_description_length, 0.0)
        self.assertEqual(parity.total_description_length, 31.0)

        constant_kinds = {
            item.kind for item in evaluations["constant zero"].counterexamples
        }
        partial_kinds = {
            item.kind for item in evaluations["forbid one"].counterexamples
        }
        self.assertIn("composition-observation-mismatch", constant_kinds)
        self.assertIn("composition-support-mismatch", partial_kinds)
        self.assertTrue(
            all(test.passed for test in parity.cases[0].tests)
        )

    def test_every_experiment_case_must_pass(self):
        base = self.experiments[0]
        transfer = replace(
            base,
            name="three-step transfer",
            tests=(
                CompositionTest(
                    "three ones",
                    "zero",
                    ("add-one", "add-one", "add-one"),
                    True,
                    {"parity": 1},
                ),
            ),
        )
        report = self.select(
            rules=self.rules[:2],
            experiments=(base, transfer),
        )

        self.assertEqual(report.experiment_names, (base.name, transfer.name))
        self.assertEqual(report.selected_rule_names, ("parity",))
        delayed = next(
            item
            for item in report.rejected
            if item.rule.name == "delayed failure"
        )
        self.assertTrue(delayed.cases[0].certified)
        self.assertFalse(delayed.cases[1].certified)
        self.assertIn(
            "composition-observation-mismatch",
            {item.kind for item in delayed.cases[1].counterexamples},
        )

    def test_state_bound_rejects_an_incomplete_rule_analysis(self):
        report = self.select(rules=(self.rules[0],), max_states=1)

        self.assertFalse(report.unique_selection)
        self.assertFalse(report.selected)
        self.assertFalse(report.ranked)
        evaluation = report.rejected[0]
        self.assertFalse(evaluation.cases[0].residual_report.complete)
        self.assertIn(
            "composition-analysis-incomplete",
            {item.kind for item in evaluation.counterexamples},
        )
        self.assertGreater(
            evaluation.cases[0].exception_description_length, 0.0
        )
        self.assertIn(
            "no candidate rule received a complete certificate",
            report.boundaries,
        )

    def test_context_basis_budget_is_a_blocking_certificate_boundary(self):
        report = self.select(
            rules=(self.rules[1],),
            max_context_tests=1,
        )

        evaluation = report.rejected[0]
        residual = evaluation.cases[0].residual_report
        self.assertTrue(residual.minimal)
        self.assertFalse(residual.context_basis_reproduces_partition)
        self.assertIn(
            "composition-context-basis-incomplete",
            {item.kind for item in evaluation.counterexamples},
        )

    def test_context_depth_bound_exposes_non_congruence(self):
        report = self.select(
            rules=(self.rules[1],),
            max_context_depth=0,
        )
        case = report.rejected[0].cases[0]
        kinds = {item.kind for item in case.counterexamples}

        self.assertTrue(case.residual_report.complete)
        self.assertFalse(case.residual_report.stable)
        self.assertFalse(case.residual_report.congruent)
        self.assertIn("composition-residual-unstable", kinds)
        self.assertIn("composition-non-congruence", kinds)

    def test_transition_errors_are_not_treated_as_bottom(self):
        def broken(state, action, _context):
            if action == "add-one":
                raise RuntimeError("broken candidate")
            return state

        rule = CompositionRule("broken", broken, 1.0)
        report = self.select(rules=(rule,))
        evaluation = report.rejected[0]
        kinds = {item.kind for item in evaluation.counterexamples}

        self.assertIn("composition-rule-error", kinds)
        self.assertIn("composition-analysis-incomplete", kinds)
        failed = [item for item in evaluation.cases[0].tests if not item.passed]
        self.assertTrue(failed)
        self.assertIsNone(failed[0].actual_defined)
        self.assertIn("RuntimeError", failed[0].detail)

    def test_analysis_exceptions_fail_closed(self):
        experiment = replace(
            self.experiments[0],
            name="opaque initial state",
            states={"zero": {"parity": 0, "phase": 0, "opaque": object()}},
            tests=(
                CompositionTest(
                    "empty", "zero", (), True, {"parity": 0}
                ),
            ),
        )
        report = self.select(
            rules=(self.rules[0],), experiments=(experiment,)
        )
        case = report.rejected[0].cases[0]

        self.assertIsNone(case.residual_report)
        self.assertIn("TypeError", case.analysis_error)
        self.assertEqual(case.class_count, 0)
        self.assertEqual(case.state_description_length, 0.0)
        self.assertIn(
            "composition-analysis-error",
            {item.kind for item in case.counterexamples},
        )

    def test_readout_errors_fail_both_test_and_residual_analysis(self):
        experiment = replace(
            self.experiments[0],
            name="broken readout",
            readout=lambda _state, _context: (_ for _ in ()).throw(
                RuntimeError("broken readout")
            ),
            tests=(
                CompositionTest(
                    "empty", "zero", (), True, {"parity": 0}
                ),
            ),
        )
        report = self.select(
            rules=(self.rules[0],), experiments=(experiment,)
        )
        case = report.rejected[0].cases[0]

        self.assertIsNone(case.tests[0].actual_defined)
        self.assertIn("RuntimeError", case.tests[0].detail)
        self.assertIsNone(case.residual_report)
        self.assertEqual(
            {item.kind for item in case.counterexamples},
            {"composition-rule-error", "composition-analysis-error"},
        )

    def test_expected_bottom_is_a_valid_operational_result(self):
        experiment = CompositionExperiment(
            name="partial test",
            states={"empty": {"signal": 0}},
            initial_states=("empty",),
            actions=("take",),
            readout=lambda state, _context: {"signal": state["signal"]},
            equivalence=EquivalenceSpec(("signal",)),
            tests=(
                CompositionTest(
                    "cannot take", "empty", ("take",), False
                ),
            ),
        )
        rule = CompositionRule(
            "partial",
            lambda state, _action, _context: state,
            1.0,
            applicable=lambda _state, action, _context: action != "take",
        )
        report = self.select(rules=(rule,), experiments=(experiment,))

        self.assertEqual(report.selected_rule_names, ("partial",))
        result = report.selected[0].cases[0].tests[0]
        self.assertTrue(result.passed)
        self.assertFalse(result.actual_defined)
        self.assertEqual(result.failure_step, 0)

    def test_equal_shortest_rules_remain_non_identifiable(self):
        transition = self.rules[0].transition
        rules = (
            CompositionRule("parity-a", transition, 8.0),
            CompositionRule("parity-b", transition, 8.0),
        )
        report = self.select(rules=rules)

        self.assertFalse(report.unique_selection)
        self.assertEqual(report.selected_rule_names, ("parity-a", "parity-b"))
        self.assertIn(
            "multiple candidate rules have the same shortest description length",
            report.boundaries,
        )

    def test_cli_demo_exposes_selection_and_description_components(self):
        section = build_demo_report()["composition_rules"]

        self.assertTrue(section["unique_selection"])
        self.assertEqual(section["selected"], ["parity"])
        self.assertEqual(
            [item["rule"] for item in section["ranked"]],
            ["parity", "delayed failure"],
        )
        self.assertEqual(
            section["ranked"][0]["cases"][0]["description_components"],
            {
                "states": 4.0,
                "transitions": 18.0,
                "contexts": 1.0,
                "exceptions": 0.0,
            },
        )
        self.assertEqual(section["ranked"][0]["rule_description_length"], 8.0)
        self.assertIn(
            "composition-observation-mismatch",
            section["rejected"][0]["counterexamples"],
        )

    def test_selector_and_input_validation_are_explicit(self):
        selector = CompositionRuleSelector()
        with self.assertRaises(ValueError):
            selector.select((), self.experiments)
        with self.assertRaises(ValueError):
            selector.select(self.rules, ())
        with self.assertRaises(ValueError):
            selector.select(
                (self.rules[0], replace(self.rules[0])), self.experiments
            )
        with self.assertRaises(ValueError):
            selector.select(
                (self.rules[0],),
                (self.experiments[0], replace(self.experiments[0])),
            )
        for penalty in (0, -1, math.inf, math.nan):
            with self.subTest(penalty=penalty), self.assertRaises(ValueError):
                selector.select(
                    (self.rules[0],),
                    self.experiments,
                    exception_penalty=penalty,
                )
        invalid_bounds = (
            {"max_states": 0},
            {"max_reachability_depth": -1},
            {"max_context_depth": -1},
            {"max_context_tests": 0},
        )
        for bounds in invalid_bounds:
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                selector.select((self.rules[0],), self.experiments, **bounds)

        with self.assertRaises(ValueError):
            CompositionRule("", lambda state, _action, _context: state, 1)
        with self.assertRaises(TypeError):
            CompositionRule("not callable", None, 1)
        with self.assertRaises(TypeError):
            CompositionRule(
                "bad support",
                lambda state, _action, _context: state,
                1,
                applicable="no",
            )
        for length in (-1, math.inf, math.nan):
            with self.subTest(length=length), self.assertRaises(ValueError):
                CompositionRule(
                    "bad length",
                    lambda state, _action, _context: state,
                    length,
                )
        normalized = CompositionRule(
            "numeric length",
            lambda state, _action, _context: state,
            "2.5",
        )
        self.assertEqual(normalized.description_length, 2.5)

    def test_test_and_experiment_validation_reject_ambiguous_domains(self):
        with self.assertRaises(ValueError):
            CompositionTest("", "s", (), True, {"signal": 0})
        with self.assertRaises(ValueError):
            CompositionTest("missing state", "", (), True, {"signal": 0})
        with self.assertRaises(ValueError):
            CompositionTest("missing result", "s", (), True)
        with self.assertRaises(ValueError):
            CompositionTest(
                "bottom with result", "s", (), False, {"signal": 0}
            )

        base = self.experiments[0]
        invalid_changes = (
            {"name": ""},
            {"initial_states": ()},
            {"initial_states": ("missing",)},
            {"initial_states": ("zero", "zero")},
            {"actions": ("add-one", "add-one")},
            {"readout": None},
            {"tests": ()},
            {"tests": (base.tests[0], replace(base.tests[0]))},
            {
                "tests": (
                    CompositionTest(
                        "bad initial", "missing", (), True, {"parity": 0}
                    ),
                )
            },
            {
                "tests": (
                    CompositionTest(
                        "bad action",
                        "zero",
                        ("missing",),
                        True,
                        {"parity": 0},
                    ),
                )
            },
            {
                "tests": (
                    CompositionTest(
                        "bad observation",
                        "zero",
                        (),
                        True,
                        {"wrong": 0},
                    ),
                )
            },
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes), self.assertRaises(
                (ValueError, TypeError, KeyError)
            ):
                replace(base, **changes)


if __name__ == "__main__":
    unittest.main()
