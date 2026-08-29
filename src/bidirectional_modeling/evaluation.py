"""Transparent satisfaction checking and certificate construction."""

from __future__ import annotations

from typing import Optional

from .core import (
    CheckResult,
    ConfidenceBreakdown,
    Context,
    ExecutableModel,
    MacroSpec,
    RequirementCategory,
    ResourceBudget,
    SatisfactionCertificate,
)


class SatisfactionEvaluator:
    """Checks M |=_Gamma G and exposes every component of the verdict."""

    def evaluate(
        self,
        model: ExecutableModel,
        spec: MacroSpec,
        context: Context,
        budget: Optional[ResourceBudget] = None,
    ) -> SatisfactionCertificate:
        budget = budget or ResourceBudget()
        all_traces = tuple(model.simulate(context, spec.horizon))
        traces = all_traces[: budget.max_simulations]
        coverage = len(traces) / len(all_traces) if all_traces else 0.0
        checks = []

        if model.metrics.cost > budget.max_cost:
            checks.append(
                CheckResult(
                    name="resource budget",
                    category=RequirementCategory.CONSTRAINT,
                    passed=False,
                    observed=model.metrics.cost,
                    expected="cost <= %s" % budget.max_cost,
                    robustness=0.0,
                    detail="candidate exceeds the caller's computation/resource budget",
                )
            )

        for requirement in spec.requirements:
            try:
                checks.append(requirement.evaluate(model, traces, context))
            except (KeyError, TypeError, ValueError) as error:
                checks.append(
                    CheckResult(
                        name=requirement.name,
                        category=requirement.category,
                        passed=False,
                        observed="unavailable",
                        expected="requirement must be evaluable",
                        robustness=0.0,
                        detail=str(error),
                    )
                )

        satisfied = bool(traces) and all(check.passed for check in checks)
        robustness = min((check.robustness for check in checks), default=1.0) if satisfied else 0.0
        confidence = ConfidenceBreakdown(
            coverage=coverage,
            robustness=robustness,
            assumption_reliability=model.prior_reliability,
        )
        boundaries = list(model.failure_boundaries)
        if coverage < 1.0:
            boundaries.append(
                "certificate covers %d/%d enumerated scenarios because of the simulation budget"
                % (len(traces), len(all_traces))
            )
        return SatisfactionCertificate(
            spec_name=spec.name,
            model_name=model.name,
            satisfied=satisfied,
            checks=tuple(checks),
            verified_scenarios=len(traces),
            confidence=confidence,
            assumptions=tuple(dict.fromkeys(context.assumptions + spec.assumptions + model.assumptions)),
            failure_boundaries=tuple(boundaries),
        )

