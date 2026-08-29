"""Transparent satisfaction checking and certificate construction."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

from .core import (
    CheckResult,
    ConfidenceBreakdown,
    Context,
    ExecutableModel,
    MacroSpec,
    RequirementCategory,
    ResourceBudget,
    SatisfactionCertificate,
    Trace,
)


@dataclass(frozen=True)
class TraceBatch:
    """A budget-bounded set of traces with independently established coverage."""

    traces: Tuple[Trace, ...]
    complete: bool
    coverage: float
    boundaries: Tuple[str, ...] = ()

    @property
    def simulations_used(self) -> int:
        return len(self.traces)


class SatisfactionEvaluator:
    """Checks M |=_Gamma G and exposes every component of the verdict."""

    def collect(
        self,
        model: ExecutableModel,
        context: Context,
        horizon: int,
        budget: Optional[ResourceBudget] = None,
    ) -> TraceBatch:
        """Collect traces without trusting a candidate's claimed scenario count.

        Completeness is established only by a concrete in-memory list/tuple or
        by observing iterator exhaustion within the simulation budget. A
        candidate-provided ``scenario_count`` is retained solely as a diagnostic
        hint and can never upgrade an incomplete batch to a complete one.
        """

        budget = budget or ResourceBudget()
        boundaries = []
        declared_total = None
        scenario_counter = getattr(model, "scenario_count", None)
        if callable(scenario_counter):
            try:
                declared_total = int(scenario_counter(context))
                if declared_total < 0:
                    boundaries.append(
                        "candidate declared a negative scenario count; the declaration was ignored"
                    )
                    declared_total = None
            except Exception as error:
                boundaries.append(
                    "candidate scenario-count declaration failed and was ignored: %s"
                    % error
                )

        try:
            generated = model.simulate(context, horizon)
        except Exception as error:
            boundaries.append(
                "simulation failed before producing a scenario: %s" % error
            )
            return TraceBatch((), False, 0.0, tuple(boundaries))

        # Only exact built-in containers are treated as trusted finite sources.
        # Arbitrary Sequence implementations can lie through __len__ just as a
        # candidate can lie through scenario_count().
        if type(generated) in (tuple, list):
            known_total = len(generated)
            traces = tuple(generated[: budget.max_simulations])
            complete = len(traces) == known_total
            coverage = len(traces) / known_total if known_total else 1.0
            if declared_total is not None and declared_total != known_total:
                boundaries.append(
                    "candidate declared %d scenarios but enumerated %d; the declaration was ignored"
                    % (declared_total, known_total)
                )
            if not complete:
                boundaries.append(
                    "partial verification covers %d/%d scenarios because the simulation budget was exhausted"
                    % (len(traces), known_total)
                )
            return TraceBatch(traces, complete, coverage, tuple(boundaries))

        traces = []
        exhausted = False
        try:
            iterator = iter(generated)
            for _ in range(budget.max_simulations):
                try:
                    traces.append(next(iterator))
                except StopIteration:
                    exhausted = True
                    break
        except Exception as error:
            boundaries.append(
                "simulation failed after %d scenarios: %s" % (len(traces), error)
            )
            if declared_total is not None and len(traces) > declared_total:
                boundaries.append(
                    "candidate declared %d scenarios but produced more; the declaration was ignored"
                    % declared_total
                )
            return TraceBatch(tuple(traces), False, 0.0, tuple(boundaries))

        complete = exhausted
        coverage = 1.0 if complete else 0.0
        if declared_total is not None:
            mismatch = (complete and declared_total != len(traces)) or (
                not complete and len(traces) > declared_total
            )
            if mismatch:
                actual = str(len(traces)) if complete else "more than %d" % declared_total
                boundaries.append(
                    "candidate declared %d scenarios but enumerated %s; the declaration was ignored"
                    % (declared_total, actual)
                )
        if not complete:
            declared_hint = (
                " (candidate declared %d, but this is not trusted)" % declared_total
                if declared_total is not None
                else ""
            )
            boundaries.append(
                "partial verification covers %d scenarios of an unproven total because the simulation budget was exhausted%s"
                % (len(traces), declared_hint)
            )
        return TraceBatch(tuple(traces), complete, coverage, tuple(boundaries))

    def evaluate_batch(
        self,
        model: ExecutableModel,
        spec: MacroSpec,
        context: Context,
        batch: TraceBatch,
        budget: Optional[ResourceBudget] = None,
    ) -> SatisfactionCertificate:
        """Evaluate a specification against an already collected trace batch."""

        budget = budget or ResourceBudget()
        checks = []

        try:
            model_cost = model.metrics.cost
            if math.isnan(float(model_cost)) or model_cost < 0:
                raise ValueError("candidate cost must be a non-negative number")
        except Exception as error:
            model_cost = "unavailable"
            checks.append(
                CheckResult(
                    name="resource budget",
                    category=RequirementCategory.CONSTRAINT,
                    passed=False,
                    observed=model_cost,
                    expected="a readable non-negative candidate cost",
                    robustness=0.0,
                    detail=str(error),
                )
            )
        else:
            if model_cost > budget.max_cost:
                checks.append(
                    CheckResult(
                        name="resource budget",
                        category=RequirementCategory.CONSTRAINT,
                        passed=False,
                        observed=model_cost,
                        expected="cost <= %s" % budget.max_cost,
                        robustness=0.0,
                        detail="candidate exceeds the caller's computation/resource budget",
                    )
                )

        for requirement in spec.requirements:
            try:
                check = requirement.evaluate(model, batch.traces, context)
                if not isinstance(check, CheckResult):
                    raise TypeError("requirement did not return a CheckResult")
                checks.append(check)
            except Exception as error:
                category = getattr(
                    requirement,
                    "category",
                    RequirementCategory.CONSTRAINT,
                )
                if not isinstance(category, RequirementCategory):
                    category = RequirementCategory.CONSTRAINT
                checks.append(
                    CheckResult(
                        name=str(
                            getattr(
                                requirement,
                                "name",
                                type(requirement).__name__,
                            )
                        ),
                        category=category,
                        passed=False,
                        observed="unavailable",
                        expected="requirement must be evaluable",
                        robustness=0.0,
                        detail=str(error),
                    )
                )

        requirements_passed = bool(batch.traces) and all(
            check.passed for check in checks
        )
        satisfied = batch.complete and requirements_passed
        robustness = (
            min((check.robustness for check in checks), default=1.0)
            if requirements_passed
            else 0.0
        )
        boundaries = list(batch.boundaries)
        try:
            reliability = float(model.prior_reliability)
            if not 0.0 <= reliability <= 1.0:
                raise ValueError("prior reliability must be in [0, 1]")
        except Exception as error:
            reliability = 0.0
            boundaries.append(
                "candidate prior reliability was invalid and was replaced with zero: %s"
                % error
            )
        confidence = ConfidenceBreakdown(
            coverage=batch.coverage,
            robustness=robustness,
            assumption_reliability=reliability,
        )
        try:
            model_boundaries = tuple(
                str(item) for item in getattr(model, "failure_boundaries", ())
            )
        except Exception as error:
            model_boundaries = ()
            boundaries.append(
                "candidate failure-boundary metadata was unavailable: %s" % error
            )
        try:
            model_assumptions = tuple(
                str(item) for item in getattr(model, "assumptions", ())
            )
        except Exception as error:
            model_assumptions = ()
            boundaries.append(
                "candidate assumption metadata was unavailable: %s" % error
            )
        try:
            model_name = str(getattr(model, "name"))
        except Exception:
            model_name = type(model).__name__
        return SatisfactionCertificate(
            spec_name=spec.name,
            model_name=model_name,
            satisfied=satisfied,
            checks=tuple(checks),
            verified_scenarios=batch.simulations_used,
            confidence=confidence,
            assumptions=tuple(
                dict.fromkeys(
                    context.assumptions + spec.assumptions + model_assumptions
                )
            ),
            failure_boundaries=tuple(
                dict.fromkeys(model_boundaries + tuple(boundaries))
            ),
            complete=batch.complete,
            requirements_passed=requirements_passed,
        )

    def failure_certificate(
        self,
        model: ExecutableModel,
        spec: MacroSpec,
        context: Context,
        detail: str,
    ) -> SatisfactionCertificate:
        """Build a fail-closed certificate for an isolated candidate error."""

        try:
            model_name = str(getattr(model, "name"))
        except Exception:
            model_name = type(model).__name__
        check = CheckResult(
            name="candidate verification",
            category=RequirementCategory.CONSTRAINT,
            passed=False,
            observed="error",
            expected="candidate verification must complete",
            robustness=0.0,
            detail=detail,
        )
        return SatisfactionCertificate(
            spec_name=spec.name,
            model_name=model_name,
            satisfied=False,
            checks=(check,),
            verified_scenarios=0,
            confidence=ConfidenceBreakdown(0.0, 0.0, 0.0),
            assumptions=tuple(dict.fromkeys(context.assumptions + spec.assumptions)),
            failure_boundaries=(
                "candidate verification failed before completion: %s" % detail,
            ),
            complete=False,
            requirements_passed=False,
        )

    def evaluate(
        self,
        model: ExecutableModel,
        spec: MacroSpec,
        context: Context,
        budget: Optional[ResourceBudget] = None,
    ) -> SatisfactionCertificate:
        budget = budget or ResourceBudget()
        batch = self.collect(model, context, spec.horizon, budget)
        return self.evaluate_batch(model, spec, context, batch, budget)
