"""Adversarial probes that expose underspecified macro goals."""

from __future__ import annotations

from dataclasses import replace

from .core import VerificationIssue, Context, Counterexample, ExecutableModel, MacroSpec, ProbeOutcome, ResourceBudget
from .evaluation import SatisfactionEvaluator, _checked_satisfaction_result
from .provenance import macro_spec_fingerprint


class HorizonExtensionProbe:
    """Tests a separate extended specification; advisory unless explicitly required."""

    def __init__(self, extra_steps: int = 3, blocking: bool = False) -> None:
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
        certificate = _checked_satisfaction_result(certificate, model, extended, context, budget)
        if not certificate.complete:
            return ProbeOutcome(None, certificate, (
                VerificationIssue("extended-horizon", "extended-horizon verification incomplete",
                                  {"requested_horizon": spec.horizon, "tested_horizon": extended.horizon}),
            ))
        if certificate.satisfied:
            return ProbeOutcome(None, certificate)
        failed = tuple(check.name for check in certificate.checks if not check.passed)
        return ProbeOutcome(
            Counterexample(
                kind="extended-specification-failure",
                summary="the candidate fails the separately tested extended specification",
                witness={
                    "requested_horizon": spec.horizon,
                    "tested_horizon": extended.horizon,
                    "model": model.name,
                    "original_spec_fingerprint": macro_spec_fingerprint(spec),
                    "tested_spec_fingerprint": certificate.spec_fingerprint,
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
