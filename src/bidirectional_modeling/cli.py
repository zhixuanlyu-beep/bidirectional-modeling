"""Executable demonstration of the full modeling loop."""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict

from .engine import BidirectionalModelingEngine
from .examples import (
    organization_interpretation_scenario,
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

    closure = report["closure"]
    print("\n闭合性检查：%s" % ("通过" if closure["closed"] else "失败"))
    for suggestion in closure["suggested_refinements"]:
        print("  → %s" % suggestion)
    print("  闭环细化后：%s" % ("闭合" if closure["closed_after_refinement"] else "仍未闭合"))


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
