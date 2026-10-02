"""Transparent satisfaction checking and certificate construction."""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Optional, Tuple

from .core import (
    CheckResult,
    VerificationMeasures,
    Context,
    ExecutableModel,
    FiniteStateModel,
    MacroSpec,
    RequirementCategory,
    ResourceBudget,
    SatisfactionCertificate,
    Trace,
    _nonnegative_number,
)
from .provenance import (
    safe_context_fingerprint,
    safe_macro_spec_fingerprint,
    safe_observed_model_fingerprint,
    satisfaction_protocol_fingerprint,
    trace_batch_protocol_fingerprint,
)
from .structural import isolated_copy, isolated_mapping, validate_fingerprint


@dataclass(frozen=True)
class TraceDiagnostic:
    """Stable collection reason plus presentation-only detail."""
    code: str
    detail: str


@dataclass(frozen=True)
class TraceBatch:
    """A budget-bounded set of traces with independently established coverage."""

    traces: Tuple[Trace, ...]
    complete: bool
    coverage: float
    horizon: int
    simulation_limit: int
    model_fingerprint: str
    context_fingerprint: str
    protocol_fingerprint: str
    diagnostics: Tuple[TraceDiagnostic, ...] = ()
    coverage_authority: str = "none"

    def __post_init__(self) -> None:
        if type(self.complete) is not bool:
            raise TypeError('trace completeness must be an explicit boolean')
        if type(self.traces) is not tuple or any(
            not isinstance(item, Trace) for item in self.traces
        ):
            raise TypeError("trace batch evidence must be a tuple of Trace instances")
        if not isinstance(self.horizon, int) or isinstance(self.horizon, bool):
            raise TypeError("trace batch horizon must be an integer")
        if self.horizon < 1:
            raise ValueError("trace batch horizon must be at least one")
        if (
            not isinstance(self.simulation_limit, int)
            or isinstance(self.simulation_limit, bool)
        ):
            raise TypeError("trace batch simulation limit must be an integer")
        if self.simulation_limit < 0:
            raise ValueError("trace batch simulation limit must be non-negative")
        if len(self.traces) > self.simulation_limit:
            raise ValueError("trace batch exceeds its declared simulation limit")
        if not 0.0 <= self.coverage <= 1.0:
            raise ValueError("trace batch coverage must be in [0, 1]")
        if self.complete and self.coverage != 1.0:
            raise ValueError("a complete trace batch must have full coverage")
        if not self.coverage_authority:
            raise ValueError("trace batch coverage authority must be non-empty")
        for label, fingerprint in (
            ("trace model evidence fingerprint", self.model_fingerprint),
            ("trace context fingerprint", self.context_fingerprint),
            ("trace protocol fingerprint", self.protocol_fingerprint),
        ):
            validate_fingerprint(fingerprint, purpose=label)
        expected_protocol = trace_batch_protocol_fingerprint(
            self.model_fingerprint,
            self.context_fingerprint,
            self.horizon,
            self.simulation_limit,
            self.coverage_authority,
            self.complete,
            self.coverage,
            tuple(d.code for d in self.diagnostics),
            self.traces,
        )
        if expected_protocol != self.protocol_fingerprint:
            raise ValueError(
                "trace batch fields do not match its protocol fingerprint"
            )

    @property
    def boundaries(self) -> Tuple[str, ...]:
        return tuple(d.detail for d in self.diagnostics)

    @property
    def simulations_used(self) -> int:
        # Failed or discarded work cannot be counted from surviving evidence.
        if any(d.code in {"simulation_start_failed", "simulation_failed",
                          "invalid_trace", "trace_isolation_failed"}
               for d in self.diagnostics):
            return self.simulation_limit
        return len(self.traces)

    def binds(
        self,
        model: ExecutableModel,
        context: Context,
        horizon: int,
    ) -> bool:
        """Whether this exact batch belongs to the requested evaluation."""

        return not _trace_batch_binding_errors(self, model, context, horizon)


def _checked_evaluator_result(result, expected_type, max_simulations):
    """Validate callback output before trusting its execution accounting."""
    if not isinstance(result, expected_type):
        raise TypeError('evaluator returned an invalid result type')
    used = result.simulations_used
    if type(used) is not int or used < 0 or used > max_simulations:
        raise ValueError('evaluator consumption exceeds its execution allowance')
    return result


def _checked_trace_batch(batch, model, context, horizon, max_simulations):
    batch = _checked_evaluator_result(batch, TraceBatch, max_simulations)
    if not batch.binds(model, context, horizon):
        raise ValueError('collected trace batch does not bind the requested evaluation')
    return batch


def _checked_satisfaction_result(certificate, model, spec, context, budget, batch=None):
    certificate = _checked_evaluator_result(certificate, SatisfactionCertificate,
                                            budget.max_simulations)
    # An unresolved native result can carry unavailable-provenance diagnostics.
    # Only a complete verdict may accept or refute the current declaration.
    if certificate.complete:
        if (certificate.model_name != model.name
                or not certificate.binds_context(context)
                or certificate.max_cost != budget.max_cost):
            raise ValueError('satisfaction certificate does not bind the requested model, context or cost limit')
        if spec is not None and (certificate.horizon != spec.horizon
                                or not certificate.binds_specification(spec)):
            raise ValueError('satisfaction certificate does not bind the requested specification')
        if batch is not None and not certificate.binds_trace_batch(batch):
            raise ValueError('satisfaction certificate does not bind the evaluated trace batch')
    return certificate


def _trace_batch_binding_errors(
    batch: TraceBatch,
    model: ExecutableModel,
    context: Context,
    horizon: int,
) -> Tuple[str, ...]:
    errors = []
    model_digest, model_error = safe_observed_model_fingerprint(
        model, batch.traces, horizon
    )
    context_digest, context_error = safe_context_fingerprint(context)
    if model_error is not None:
        errors.append("model evidence could not be fingerprinted: %s" % model_error)
    if context_error is not None:
        errors.append("context could not be fingerprinted: %s" % context_error)
    if batch.horizon != horizon:
        errors.append(
            "trace batch horizon %d does not match requested horizon %d"
            % (batch.horizon, horizon)
        )
    if batch.model_fingerprint != model_digest:
        errors.append("trace batch model evidence does not match this evaluation")
    if batch.context_fingerprint != context_digest:
        errors.append("trace batch context does not match this evaluation")
    expected_protocol = trace_batch_protocol_fingerprint(
        batch.model_fingerprint,
        batch.context_fingerprint,
        batch.horizon,
        batch.simulation_limit,
        batch.coverage_authority,
        batch.complete,
        batch.coverage,
        tuple(d.code for d in batch.diagnostics),
        batch.traces,
    )
    if batch.protocol_fingerprint != expected_protocol:
        errors.append("trace batch metadata changed after collection")
    return tuple(dict.fromkeys(errors))


class SatisfactionEvaluator:
    """Checks M |=_Gamma G and exposes every component of the verdict."""

    def _bound_trace_batch(
        self,
        model: ExecutableModel,
        context: Context,
        horizon: int,
        simulation_limit: int,
        traces: Tuple[Trace, ...],
        complete: bool,
        coverage: float,
        boundaries: list[TraceDiagnostic],
        coverage_authority: str,
    ) -> TraceBatch:
        """Attach deterministic evidence and protocol identities to a batch."""

        model_digest, model_error = safe_observed_model_fingerprint(
            model, traces, horizon
        )
        context_digest, context_error = safe_context_fingerprint(context)
        if model_error is not None:
            complete = False
            boundaries.append(
                TraceDiagnostic('model_binding_failed', "model evidence could not be fingerprinted: %s" % model_error)
            )
        if context_error is not None:
            complete = False
            boundaries.append(
                TraceDiagnostic('context_binding_failed', "context could not be fingerprinted: %s" % context_error)
            )
        final_boundaries = tuple(dict.fromkeys(boundaries))
        protocol_digest = trace_batch_protocol_fingerprint(
            model_digest,
            context_digest,
            horizon,
            simulation_limit,
            coverage_authority,
            complete,
            coverage,
            tuple(d.code for d in final_boundaries),
            traces,
        )
        return TraceBatch(
            traces=traces,
            complete=complete,
            coverage=coverage,
            horizon=horizon,
            simulation_limit=simulation_limit,
            model_fingerprint=model_digest,
            context_fingerprint=context_digest,
            protocol_fingerprint=protocol_digest,
            diagnostics=final_boundaries,
            coverage_authority=coverage_authority,
        )

    def unstarted_batch(
        self,
        model: ExecutableModel,
        context: Context,
        horizon: int,
        boundary: str,
    ) -> TraceBatch:
        """Build a bound, incomplete batch when shared budget prevents a run."""

        return self._bound_trace_batch(
            model,
            context,
            horizon,
            0,
            (),
            False,
            0.0,
            [TraceDiagnostic("not_started", boundary)],
            "none",
        )

    def _certify_trace_batch(
        self,
        model: ExecutableModel,
        context: Context,
        horizon: int,
        traces: Tuple[Trace, ...],
        source_exhausted: bool,
        source_failed: bool,
        boundaries: list[TraceDiagnostic],
        simulation_limit: int,
    ) -> TraceBatch:
        """Validate trace integrity against a caller- or framework-owned domain."""

        if context.scenario_manifest:
            expected = context.scenario_manifest
            authority = "context-manifest"
        elif type(model) is FiniteStateModel:
            # Call the framework method unbound so an instance cannot replace
            # the manifest derivation while also replacing simulate().
            expected = FiniteStateModel.scenario_manifest(model, context)
            authority = "framework-finite-state"
        else:
            expected = None
            authority = "none"

        try:
            expected_model_name = str(getattr(model, "name"))
        except Exception:
            expected_model_name = None

        valid_keys = []
        certified_traces = []
        integrity_ok = True
        for index, trace in enumerate(traces):
            if not isinstance(trace, Trace):
                integrity_ok = False
                boundaries.append(TraceDiagnostic('invalid_trace', "scenario %d is not a Trace instance" % index))
                continue
            try:
                trace = Trace(
                    model_name=trace.model_name,
                    initial_state=trace.initial_state,
                    intervention=trace.intervention,
                    snapshots=tuple(
                        isolated_mapping(
                            snapshot,
                            purpose="scenario %d snapshot" % index,
                        )
                        for snapshot in trace.snapshots
                    ),
                )
            except Exception as error:
                integrity_ok = False
                boundaries.append(
                    TraceDiagnostic('trace_isolation_failed', "scenario %d evidence could not be isolated: %s"
                    % (index, error))
                )
                continue
            certified_traces.append(trace)
            if len(trace.snapshots) != horizon + 1:
                integrity_ok = False
                boundaries.append(
                    TraceDiagnostic('invalid_horizon', "scenario %s/%s has %d snapshots; expected %d for horizon %d"
                    % (
                        trace.initial_state,
                        trace.intervention,
                        len(trace.snapshots),
                        horizon + 1,
                        horizon,
                    ))
                )
            if expected_model_name is not None and trace.model_name != expected_model_name:
                integrity_ok = False
                boundaries.append(
                    TraceDiagnostic('model_name_mismatch', "scenario %s/%s names model %r instead of %r"
                    % (
                        trace.initial_state,
                        trace.intervention,
                        trace.model_name,
                        expected_model_name,
                    ))
                )
            valid_keys.append(trace.scenario_key)

        traces = tuple(certified_traces)

        unique_keys = set(valid_keys)
        duplicate_count = len(valid_keys) - len(unique_keys)
        if duplicate_count:
            integrity_ok = False
            boundaries.append(
                TraceDiagnostic('duplicate_scenarios', "trace enumeration contains %d duplicate scenario identities"
                % duplicate_count)
            )

        if expected is None:
            boundaries.append(
                TraceDiagnostic('missing_manifest', "no caller-owned scenario manifest was supplied for a third-party model; "
                "candidate enumeration cannot prove domain coverage")
            )
            return self._bound_trace_batch(
                model,
                context,
                horizon,
                simulation_limit,
                traces,
                False,
                0.0,
                boundaries,
                authority,
            )

        expected_keys = set(expected)
        missing = expected_keys - unique_keys
        unexpected = unique_keys - expected_keys
        if missing:
            boundaries.append(
                TraceDiagnostic('missing_scenarios', "missing required scenarios: %s"
                % ", ".join(
                    "%s/%s" % (item.initial_state, item.intervention)
                    for item in sorted(missing)
                ))
            )
        if unexpected:
            boundaries.append(
                TraceDiagnostic('unexpected_scenarios', "unexpected scenarios outside the manifest: %s"
                % ", ".join(
                    "%s/%s" % (item.initial_state, item.intervention)
                    for item in sorted(unexpected)
                ))
            )
        coverage = (
            len(expected_keys.intersection(unique_keys)) / len(expected_keys)
            if expected_keys
            else 1.0
        )
        # An authoritative manifest proves completion as soon as every required
        # identity is observed. Iterator exhaustion is not needed at the exact
        # budget boundary, but an actual source failure remains disqualifying.
        complete = (
            integrity_ok
            and not source_failed
            and not missing
            and not unexpected
            and (source_exhausted or len(unique_keys) == len(expected_keys))
        )
        return self._bound_trace_batch(
            model,
            context,
            horizon,
            simulation_limit,
            traces,
            complete,
            coverage,
            boundaries,
            authority,
        )

    def collect(
        self,
        model: ExecutableModel,
        context: Context,
        horizon: int,
        budget: Optional[ResourceBudget] = None,
    ) -> TraceBatch:
        """Collect traces and prove coverage against an authoritative manifest.

        A caller-owned ``Context.scenario_manifest`` is required for arbitrary
        third-party models. The framework can derive the manifest for its exact
        built-in ``FiniteStateModel`` because it owns that simulator's scenario
        enumeration. Candidate-provided ``scenario_count`` remains diagnostic.
        """

        budget = budget or ResourceBudget()
        boundaries = []
        declared_total = None
        scenario_counter = getattr(model, "scenario_count", None)
        if callable(scenario_counter):
            try:
                declared_total = int(
                    scenario_counter(
                        isolated_copy(
                            context,
                            purpose="scenario-count input context",
                        )
                    )
                )
                if declared_total < 0:
                    boundaries.append(
                        TraceDiagnostic('negative_scenario_count', "candidate declared a negative scenario count; the declaration was ignored")
                    )
                    declared_total = None
            except Exception as error:
                boundaries.append(
                    TraceDiagnostic('scenario_count_failed', "candidate scenario-count declaration failed and was ignored: %s"
                    % error)
                )

        try:
            generated = model.simulate(
                isolated_copy(context, purpose="simulation input context"),
                horizon,
            )
            if isinstance(generated, AbstractSet):
                raise TypeError('simulation cannot return an unordered set')
        except Exception as error:
            boundaries.append(
                TraceDiagnostic('simulation_start_failed', "simulation failed before producing a scenario: %s" % error)
            )
            return self._certify_trace_batch(
                model,
                context,
                horizon,
                (),
                False,
                True,
                boundaries,
                budget.max_simulations,
            )

        # Exact containers expose whether their own enumeration was truncated;
        # domain completeness is still decided by _certify_trace_batch against
        # the independent manifest.
        if type(generated) in (tuple, list):
            known_total = len(generated)
            traces = tuple(generated[: budget.max_simulations])
            source_exhausted = len(traces) == known_total
            if declared_total is not None and declared_total != known_total:
                boundaries.append(
                    TraceDiagnostic('scenario_count_mismatch', "candidate declared %d scenarios but enumerated %d; the declaration was ignored"
                    % (declared_total, known_total))
                )
            if not source_exhausted:
                boundaries.append(
                    TraceDiagnostic('container_limit_reached', "partial verification covers %d/%d scenarios because the simulation budget was exhausted"
                    % (len(traces), known_total))
                )
            return self._certify_trace_batch(
                model,
                context,
                horizon,
                traces,
                source_exhausted,
                False,
                boundaries,
                budget.max_simulations,
            )

        traces = []
        exhausted = False
        source_failed = False
        try:
            iterator = iter(generated)
            for _ in range(budget.max_simulations):
                try:
                    traces.append(next(iterator))
                except StopIteration:
                    exhausted = True
                    break
        except Exception as error:
            source_failed = True
            boundaries.append(
                TraceDiagnostic('simulation_failed', "simulation failed after %d scenarios: %s" % (len(traces), error))
            )
            if declared_total is not None and len(traces) > declared_total:
                boundaries.append(
                    TraceDiagnostic('scenario_count_mismatch', "candidate declared %d scenarios but produced more; the declaration was ignored"
                    % declared_total)
                )
        enumerated_count = len(traces)
        if declared_total is not None:
            mismatch = (exhausted and declared_total != enumerated_count) or (
                not exhausted and enumerated_count > declared_total
            )
            if mismatch:
                actual = str(enumerated_count) if exhausted else "more than %d" % declared_total
                boundaries.append(
                    TraceDiagnostic('scenario_count_mismatch', "candidate declared %d scenarios but enumerated %s; the declaration was ignored"
                    % (declared_total, actual))
                )
        if not exhausted and not source_failed:
            declared_hint = (
                " (candidate declared %d, but this is not trusted)" % declared_total
                if declared_total is not None
                else ""
            )
            boundaries.append(
                TraceDiagnostic('iterator_limit_reached', "partial verification covers %d scenarios of an unproven total because the simulation budget was exhausted%s"
                % (len(traces), declared_hint))
            )
        return self._certify_trace_batch(
            model,
            context,
            horizon,
            tuple(traces),
            exhausted,
            source_failed,
            boundaries,
            budget.max_simulations,
        )

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
        boundaries = list(batch.boundaries)
        spec_digest, spec_error = safe_macro_spec_fingerprint(spec)
        model_digest, model_error = safe_observed_model_fingerprint(
            model, batch.traces, spec.horizon
        )
        context_digest, context_error = safe_context_fingerprint(context)
        if spec_error is not None:
            boundaries.append(
                "macro specification could not be fingerprinted: %s" % spec_error
            )
        initial_binding_errors = _trace_batch_binding_errors(
            batch, model, context, spec.horizon
        )
        boundaries.extend(
            "trace batch binding failed: %s" % item
            for item in initial_binding_errors
        )
        provenance_complete = (
            spec_error is None
            and model_error is None
            and context_error is None
            and not initial_binding_errors
        )

        try:
            model_cost = model.metrics.cost
            _nonnegative_number(model_cost, label='candidate cost')
        except Exception as error:
            model_cost = "unavailable"
            checks.append(
                CheckResult(
                    name="resource budget",
                    category=RequirementCategory.CONSTRAINT,
                    passed=False,
                    observed=model_cost,
                    expected="a readable non-negative candidate cost",

                    detail=str(error),
                    evaluation_error=str(error),
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

                        detail="candidate exceeds the caller's computation/resource budget",
                    )
                )

        for requirement in spec.requirements:
            try:
                check = requirement.evaluate(
                    model,
                    isolated_copy(
                        batch.traces,
                        purpose="requirement trace evidence",
                    ),
                    isolated_copy(
                        context,
                        purpose="requirement input context",
                    ),
                )
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

                        detail=str(error),
                        evaluation_error=str(error),
                    )
                )

        final_spec_digest, final_spec_error = safe_macro_spec_fingerprint(spec)
        if final_spec_error is not None:
            provenance_complete = False
            boundaries.append(
                "macro specification could not be fingerprinted after evaluation: %s"
                % final_spec_error
            )
        elif final_spec_digest != spec_digest:
            provenance_complete = False
            boundaries.append(
                "macro specification changed while its requirements were evaluated"
            )
        final_binding_errors = _trace_batch_binding_errors(
            batch, model, context, spec.horizon
        )
        if final_binding_errors:
            provenance_complete = False
            boundaries.extend(
                "trace batch binding failed after evaluation: %s" % item
                for item in final_binding_errors
                if item not in initial_binding_errors
            )

        complete = (batch.complete and provenance_complete
                    and not any(check.evaluation_error is not None for check in checks))
        verification = VerificationMeasures(
            coverage=batch.coverage if provenance_complete else 0.0,
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
        protocol_digest = satisfaction_protocol_fingerprint(
            spec_digest,
            model_digest,
            context_digest,
            batch.protocol_fingerprint,
            budget.max_cost,
        )
        return SatisfactionCertificate(
            spec_name=spec.name,
            model_name=model_name,
            checks=tuple(checks),
            verified_scenarios=len(batch.traces),
            charged_simulations=batch.simulations_used,
            verification=verification,
            assumptions=tuple(
                dict.fromkeys(
                    context.assumptions + spec.assumptions + model_assumptions
                )
            ),
            failure_boundaries=tuple(
                dict.fromkeys(model_boundaries + tuple(boundaries))
            ),
            horizon=spec.horizon,
            spec_fingerprint=spec_digest,
            model_fingerprint=model_digest,
            context_fingerprint=context_digest,
            trace_batch_fingerprint=batch.protocol_fingerprint,
            protocol_fingerprint=protocol_digest,
            max_cost=budget.max_cost,
            complete=complete,
            coverage_authority=batch.coverage_authority,
        )

    def failure_certificate(
        self,
        model: ExecutableModel,
        spec: MacroSpec,
        context: Context,
        detail: str,
        budget: Optional[ResourceBudget] = None,
    ) -> SatisfactionCertificate:
        """Build a fail-closed certificate for an isolated candidate error."""

        budget = budget or ResourceBudget()
        try:
            model_name = str(getattr(model, "name"))
        except Exception:
            model_name = type(model).__name__
        spec_digest, spec_error = safe_macro_spec_fingerprint(spec)
        model_digest, model_error = safe_observed_model_fingerprint(
            model, (), spec.horizon
        )
        context_digest, context_error = safe_context_fingerprint(context)
        diagnostics = [TraceDiagnostic('candidate_verification_failed',
            "candidate verification failed before completion: %s" % detail)]
        for code, label, error in (
            ('spec_binding_failed', "macro specification", spec_error),
            ('model_binding_failed', "model evidence", model_error),
            ('context_binding_failed', "context", context_error),
        ):
            if error is not None:
                diagnostics.append(TraceDiagnostic(code, "%s could not be fingerprinted: %s" % (label, error)))
        boundaries = tuple(d.detail for d in diagnostics)
        trace_protocol_digest = trace_batch_protocol_fingerprint(
            model_digest, context_digest, spec.horizon, budget.max_simulations,
            "none", False, 0.0, tuple(d.code for d in diagnostics), ())
        protocol_digest = satisfaction_protocol_fingerprint(
            spec_digest,
            model_digest,
            context_digest,
            trace_protocol_digest,
            budget.max_cost,
        )
        check = CheckResult(
            name="candidate verification",
            category=RequirementCategory.CONSTRAINT,
            passed=False,
            observed="error",
            expected="candidate verification must complete",

            detail=detail,
            evaluation_error=detail,
        )
        return SatisfactionCertificate(
            spec_name=spec.name,
            model_name=model_name,
            checks=(check,),
            verified_scenarios=0,
            verification=VerificationMeasures(0.0),
            assumptions=tuple(dict.fromkeys(context.assumptions + spec.assumptions)),
            failure_boundaries=tuple(boundaries),
            horizon=spec.horizon,
            spec_fingerprint=spec_digest,
            model_fingerprint=model_digest,
            context_fingerprint=context_digest,
            trace_batch_fingerprint=trace_protocol_digest,
            protocol_fingerprint=protocol_digest,
            max_cost=budget.max_cost,
            complete=False,
            coverage_authority="none",
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
