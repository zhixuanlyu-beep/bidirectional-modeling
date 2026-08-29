"""Adversarial probes that expose underspecified macro goals."""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from .core import Context, Counterexample, ExecutableModel, MacroSpec, ProbeOutcome, ResourceBudget
from .evaluation import SatisfactionEvaluator


class HorizonExtensionProbe:
    """Rejects a short-horizon success that collapses soon afterwards."""

    def __init__(self, extra_steps: int = 3, blocking: bool = True) -> None:
        if extra_steps < 1:
            raise ValueError("extra_steps must be positive")
        self.extra_steps = extra_steps
        self.blocking = blocking

    def probe(
        self,
        model: ExecutableModel,
        spec: MacroSpec,
        context: Context,
        evaluator: SatisfactionEvaluator,
        budget: ResourceBudget,
    ) -> ProbeOutcome:
        extended = replace(spec, name=spec.name + " [extended horizon]", horizon=spec.horizon + self.extra_steps)
        certificate = evaluator.evaluate(model, extended, context, budget)
        if not certificate.complete:
            return ProbeOutcome(
                Counterexample(
                    kind="verification-budget-exhausted",
                    summary="the extended-horizon probe was only partially verified",
                    witness={
                        "requested_horizon": spec.horizon,
                        "tested_horizon": extended.horizon,
                        "model": model.name,
                    },
                    violated=("complete extended-horizon verification",),
                    suggested_refinements=("increase max_simulations",),
                    blocking=True,
                ),
                certificate,
            )
        if certificate.satisfied:
            return ProbeOutcome(None, certificate)
        failed = tuple(check.name for check in certificate.checks if not check.passed)
        return ProbeOutcome(
            Counterexample(
                kind="horizon-specification-gaming",
                summary="the candidate satisfies the requested horizon but fails when the horizon is extended",
                witness={
                    "requested_horizon": spec.horizon,
                    "tested_horizon": extended.horizon,
                    "model": model.name,
                },
                violated=failed,
                suggested_refinements=(
                    "extend the required horizon to %d" % extended.horizon,
                    "add a long-term stability invariant",
                ),
                blocking=self.blocking,
            ),
            certificate,
        )
