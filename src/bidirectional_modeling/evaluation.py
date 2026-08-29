"""Transparent satisfaction checking and certificate construction."""

from __future__ import annotations

from collections.abc import Sequence as SequenceABC
from itertools import islice
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
        generated = model.simulate(context, spec.horizon)
        known_total = None
        scenario_counter = getattr(model, "scenario_count", None)
        if callable(scenario_counter):
            known_total = int(scenario_counter(context))
            if known_total < 0:
                raise ValueError("scenario_count must be non-negative")
        elif isinstance(generated, SequenceABC):
            known_total = len(generated)

        traces = tuple(islice(iter(generated), budget.max_simulations))
        if known_total is not None:
            complete = len(traces) == known_total
            coverage = len(traces) / known_total if known_total else 1.0
        else:
            # Without a declared scenario count, consuming fewer than the limit
            # proves exhaustion. Reaching the limit is conservatively partial;
            # no extra trace is pulled beyond the caller's budget just to peek.
            complete = len(traces) < budget.max_simulations
            coverage = 1.0 if complete else 0.0
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

        requirements_passed = bool(traces) and all(check.passed for check in checks)
        satisfied = complete and requirements_passed
        robustness = (
            min((check.robustness for check in checks), default=1.0)
            if requirements_passed
            else 0.0
        )
        confidence = ConfidenceBreakdown(
            coverage=coverage,
            robustness=robustness,
            assumption_reliability=model.prior_reliability,
        )
        boundaries = list(model.failure_boundaries)
        if not complete:
            total_label = str(known_total) if known_total is not None else "an unknown total"
            boundaries.append(
                "partial verification covers %d/%s scenarios because the simulation budget was exhausted"
                % (len(traces), total_label)
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
            complete=complete,
            requirements_passed=requirements_passed,
        )
