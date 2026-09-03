"""Executable demonstration of the full modeling loop."""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict

from .engine import BidirectionalModelingEngine
from .examples import (
    composition_rule_scenario,
    organization_interpretation_scenario,
    residual_quotient_scenario,
    scale_correspondence_suite,
    science_closure_scenario,
    software_scenario,
)
from .interpretation import ObservedEffectGenerator
from .probes import HorizonExtensionProbe
from .realization import Realizer


def build_demo_report() -> Dict[str, Any]:
    engine = BidirectionalModelingEngine(
        realizer=Realizer(probes=(HorizonExtensionProbe(extra_steps=2),))
    )

    software_spec, software_context, software_models = software_scenario()
    realized = engine.realize(software_spec, software_context, software_models)

    residual_equivalence, residual_context, residual_model = (
        residual_quotient_scenario()
    )
    residual = engine.discover_residual_quotient(
        residual_model,
        residual_equivalence,
        residual_context,
    )

    composition_experiments, composition_rules = composition_rule_scenario()
    composition = engine.select_composition_rules(
        composition_rules,
        composition_experiments,
    )

    science_spec, science_context, science_model = science_closure_scenario()
    closure = engine.check_closure(science_model, science_spec, science_context)
    refinement = engine.refine_until_closed(
        science_model,
        science_spec,
        science_context,
        lambda report, _spec, _model: report.suggested_features[0],
        concept_name="position state",
    )
    refined = engine.concepts.get("position state")

    org_context, org_model, hypotheses, experiments, evidence = (
        organization_interpretation_scenario()
    )
    interpreted = engine.interpret(
        org_model, org_context, hypotheses, evidence, experiments
    )
    observed_effects = engine.interpret(
        org_model, org_context, ObservedEffectGenerator(horizon=1)
    )

    correspondence, correspondence_cases = scale_correspondence_suite()
    correspondence_certificate = engine.verify_correspondence_suite(
        correspondence,
        correspondence_cases,
    )
    scale_paths = engine.scale_graph.find_paths("micro", "macro")

    return {
        "realize": {
            "goal": software_spec.name,
            "pareto_candidates": [
                {
                    "model": item.model.name,
                    "verification_score": round(item.verification_score, 4),
                    "complete": item.certificate.complete,
                    "coverage_authority": item.certificate.coverage_authority,
                    "metrics": {
                        "cost": item.model.metrics.cost,
                        "complexity": item.model.metrics.complexity,
                        "risk": item.model.metrics.risk,
                    },
                    "checks": [
                        {"name": check.name, "passed": check.passed}
                        for check in item.certificate.checks
                    ],
                }
                for item in realized.candidates
            ],
            "rejected": [
                {
                    "model": item.model.name,
                    "failed_checks": [
                        check.name for check in item.certificate.checks if not check.passed
                    ],
                    "counterexamples": [counterexample.kind for counterexample in item.counterexamples],
                }
                for item in realized.rejected
            ],
            "dominated": [item.model.name for item in realized.dominated],
            "simulations_used": realized.simulations_used,
        },
        "interpret": {
            "structure": org_model.name,
            "ranked_hypotheses": [
                {
                    "name": item.hypothesis.name,
                    "level": item.hypothesis.level.value,
                    "ranking_score": round(item.ranking_score, 4),
                    "caveats": list(item.caveats),
                }
                for item in interpreted.candidates
            ],
            "non_identifiable": interpreted.non_identifiable,
            "score_semantics": interpreted.score_semantics,
            "discriminating_query": (
                {
                    "experiment": interpreted.discriminating_query.experiment.name,
                    "question": interpreted.discriminating_query.experiment.question,
                    "information_gain": round(
                        interpreted.discriminating_query.expected_information_gain, 4
                    ),
                    "predictions": dict(interpreted.discriminating_query.predictions),
                }
                if interpreted.discriminating_query
                else None
            ),
            "automatically_observed_effects": [
                item.hypothesis.name for item in observed_effects.candidates
            ],
        },
        "residual_quotient": {
            "model": residual.model_name,
            "minimal": residual.minimal,
            "complete": residual.complete,
            "stable": residual.stable,
            "congruent": residual.congruent,
            "explored_states": residual.explored_states,
            "class_count": residual.quotient.class_count,
            "context_basis_reproduces_partition": (
                residual.context_basis_reproduces_partition
            ),
            "context_basis": [list(item) for item in residual.context_basis],
            "context_refinements": [
                {
                    "iteration": item.iteration,
                    "left_state": item.left_state,
                    "right_state": item.right_state,
                    "context": list(item.context),
                    "class_count": item.class_count,
                }
                for item in residual.context_refinements
            ],
            "filtration": [
                {
                    "context_depth": level.context_depth,
                    "class_count": level.class_count,
                    "new_distinguishing_contexts": [
                        list(context)
                        for context in level.new_distinguishing_contexts
                    ],
                }
                for level in residual.filtration
            ],
            "distinguishing_contexts": [
                {
                    "left_state": item.left_state,
                    "right_state": item.right_state,
                    "actions": list(item.actions),
                    "depth": item.depth,
                }
                for item in residual.distinguishing_contexts
            ],
            "boundaries": list(residual.boundaries),
        },
        "composition_rules": {
            "unique_selection": composition.unique_selection,
            "selected": list(composition.selected_rule_names),
            "ranked": [
                {
                    "rule": item.rule.name,
                    "description_length": item.total_description_length,
                    "rule_description_length": item.rule.description_length,
                    "cases": [
                        {
                            "experiment": case.experiment_name,
                            "class_count": case.class_count,
                            "residual_minimal": (
                                case.residual_report.minimal
                                if case.residual_report is not None
                                else False
                            ),
                            "context_basis": (
                                [
                                    list(word)
                                    for word in case.residual_report.context_basis
                                ]
                                if case.residual_report is not None
                                else []
                            ),
                            "description_components": {
                                "states": case.state_description_length,
                                "transitions": case.transition_description_length,
                                "contexts": case.context_description_length,
                                "exceptions": case.exception_description_length,
                            },
                        }
                        for case in item.cases
                    ],
                }
                for item in composition.ranked
            ],
            "rejected": [
                {
                    "rule": item.rule.name,
                    "counterexamples": [
                        counterexample.kind
                        for counterexample in item.counterexamples
                    ],
                }
                for item in composition.rejected
            ],
            "boundaries": list(composition.boundaries),
        },
        "closure": {
            "model": science_model.name,
            "closed": closure.closed,
            "complete": closure.complete,
            "counterexample_count": len(closure.counterexamples),
            "first_witness": (
                dict(closure.counterexamples[0].witness)
                if closure.counterexamples
                else None
            ),
            "suggested_refinements": (
                list(closure.counterexamples[0].suggested_refinements)
                if closure.counterexamples
                else []
            ),
            "concept_version": refined.version if refined else 1,
            "closed_after_refinement": refinement.closed,
            "refinement_stopped_reason": refinement.stopped_reason,
            "final_observables": list(refinement.final_spec.observables),
        },
        "correspondence": {
            "name": correspondence.name,
            "lower_scale": correspondence.lower_scale.name,
            "upper_scale": correspondence.upper_scale.name,
            "passed": correspondence_certificate.passed,
            "compatibility_passed": correspondence_certificate.compatibility_passed,
            "independent_holdout": correspondence_certificate.has_independent_holdout,
            "complete": correspondence_certificate.complete,
            "commutes": correspondence_certificate.commutes,
            "lower_scenarios": sum(
                item.certificate.lower_scenarios
                for item in correspondence_certificate.cases
            ),
            "upper_scenarios": sum(
                item.certificate.upper_scenarios
                for item in correspondence_certificate.cases
            ),
            "paired_scenarios": sum(
                item.certificate.paired_scenarios
                for item in correspondence_certificate.cases
            ),
            "simulations_used": correspondence_certificate.simulations_used,
            "cases": [
                {
                    "name": item.case_name,
                    "role": item.role.value,
                    "independent": item.independent,
                    "passed": item.certificate.passed,
                    "lower_context_fingerprint": (
                        item.certificate.lower_context_fingerprint
                    ),
                    "upper_context_fingerprint": (
                        item.certificate.upper_context_fingerprint
                    ),
                    "coverage_authorities": [
                        item.certificate.lower_coverage_authority,
                        item.certificate.upper_coverage_authority,
                    ],
                }
                for item in correspondence_certificate.cases
            ],
            "paths": [
                {
                    "scales": list(path.scales),
                    "correspondences": list(path.correspondences),
                    "edgewise_certified": path.edgewise_certified,
                    "end_to_end_certified": path.end_to_end_certified,
                }
                for path in scale_paths
            ],
        },
    }


def _print_human(report: Dict[str, Any]) -> None:
    realized = report["realize"]
    print("宏观目的 → 微观结构：%s" % realized["goal"])
    for candidate in realized["pareto_candidates"]:
        print(
            "  ✓ %s  verification_score=%.4f  metrics=%s"
            % (candidate["model"], candidate["verification_score"], candidate["metrics"])
        )
    for rejected in realized["rejected"]:
        why = rejected["counterexamples"] or rejected["failed_checks"]
        print("  ✗ %s  %s" % (rejected["model"], ", ".join(why)))

    interpreted = report["interpret"]
    print("\n微观结构 → 宏观目的：%s" % interpreted["structure"])
    for candidate in interpreted["ranked_hypotheses"]:
        caveat = "；" + "；".join(candidate["caveats"]) if candidate["caveats"] else ""
        print(
            "  • %s [%s] ranking_score=%.4f%s"
            % (candidate["name"], candidate["level"], candidate["ranking_score"], caveat)
        )
    query = interpreted["discriminating_query"]
    if query:
        print("  ? 最佳区分问题：%s" % query["question"])

    residual = report["residual_quotient"]
    print(
        "\n残差语义商：%s，%d 个微观状态 → %d 个行为类"
        % (
            "已证明最小" if residual["minimal"] else "仅有界结果",
            residual["explored_states"],
            residual["class_count"],
        )
    )
    for level in residual["filtration"]:
        print(
            "  深度 %d：%d 类"
            % (level["context_depth"], level["class_count"])
        )
    print(
        "  反例引导上下文基：%s"
        % (
            residual["context_basis"]
            if residual["context_basis_reproduces_partition"]
            else "未完整"
        )
    )

    composition = report["composition_rules"]
    print(
        "\n候选微观组合规则：%s"
        % (
            ", ".join(composition["selected"])
            if composition["selected"]
            else "无可认证候选"
        )
    )
    for candidate in composition["ranked"]:
        print(
            "  ✓ %s  description_length=%.1f"
            % (candidate["rule"], candidate["description_length"])
        )
    for rejected in composition["rejected"]:
        print(
            "  ✗ %s  %s"
            % (rejected["rule"], ", ".join(rejected["counterexamples"]))
        )

    closure = report["closure"]
    print("\n闭合性检查：%s" % ("通过" if closure["closed"] else "失败"))
    for suggestion in closure["suggested_refinements"]:
        print("  → %s" % suggestion)
    print("  闭环细化后：%s" % ("闭合" if closure["closed_after_refinement"] else "仍未闭合"))

    correspondence = report["correspondence"]
    print(
        "\n跨尺度对应：%s (%s → %s)"
        % (
            "通过" if correspondence["passed"] else "失败",
            correspondence["lower_scale"],
            correspondence["upper_scale"],
        )
    )
    print(
        "  配对 %d 个下层场景到 %d 个上层场景"
        % (
            correspondence["paired_scenarios"],
            correspondence["upper_scenarios"],
        )
    )
    print(
        "  独立留出复核：%s"
        % ("通过" if correspondence["independent_holdout"] else "缺失")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the bidirectional modeling reference demo")
    parser.add_argument("command", nargs="?", default="demo", choices=("demo",))
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    args = parser.parse_args()
    report = build_demo_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_human(report)


if __name__ == "__main__":
    main()
