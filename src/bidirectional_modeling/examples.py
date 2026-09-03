"""Domain examples using the same core interfaces."""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, Tuple

from .correspondence import (
    Correspondence,
    CorrespondenceCaseRole,
    CorrespondenceValidationCase,
    Scale,
)
from .composition import (
    CompositionExperiment,
    CompositionRule,
    CompositionTest,
)
from .core import (
    Aggregation,
    Context,
    EquivalenceSpec,
    Evidence,
    Experiment,
    FieldRequirement,
    FiniteStateModel,
    MacroSpec,
    ModelMetricRequirement,
    ModelMetrics,
    PurposeHypothesis,
    PurposeLevel,
    RequirementCategory,
    ScenarioKey,
)


def software_scenario() -> Tuple[MacroSpec, Context, Tuple[FiniteStateModel, ...]]:
    """Several worker designs, including a short-horizon specification gamer."""

    spec = MacroSpec(
        name="reliable work completion",
        observables=("completed", "data_loss", "latency"),
        objectives=(
            FieldRequirement(
                "complete queued work",
                "completed",
                "ge",
                2,
                aggregation=Aggregation.FINAL,
            ),
        ),
        equivalence=EquivalenceSpec(("completed", "data_loss")),
        invariants=(
            FieldRequirement(
                "never lose data",
                "data_loss",
                "eq",
                0,
                category=RequirementCategory.INVARIANT,
                aggregation=Aggregation.EACH,
            ),
        ),
        constraints=(
            ModelMetricRequirement("affordable", "cost", "le", 5.0),
        ),
        horizon=2,
        assumptions=("the queue contains at least two valid jobs",),
        ambiguous_terms={
            "reliable": (
                "no loss during the requested horizon",
                "no loss under a longer operational horizon",
            )
        },
    )
    context = Context(environment={"queue_size": 3}, scale="request-processing")

    def readout(state, _context):
        return {
            "completed": state["completed"],
            "data_loss": state["data_loss"],
            "latency": state["latency"],
        }

    def safe_transition(state, _action, context):
        next_state = dict(state)
        queue_size = context.environment["queue_size"]
        next_state["completed"] = min(queue_size, state["completed"] + 1)
        next_state["latency"] = max(0, queue_size - next_state["completed"])
        next_state["tick"] += 1
        return next_state

    def parallel_transition(state, _action, context):
        next_state = dict(state)
        queue_size = context.environment["queue_size"]
        next_state["completed"] = min(queue_size, state["completed"] + 2)
        next_state["latency"] = max(0, queue_size - next_state["completed"])
        next_state["tick"] += 1
        return next_state

    def fragile_transition(state, _action, context):
        next_state = safe_transition(state, _action, context)
        if next_state["tick"] > 2:
            next_state["data_loss"] = 1
        return next_state

    def drop_transition(state, _action, context):
        next_state = dict(state)
        next_state["completed"] = context.environment["queue_size"]
        next_state["data_loss"] = context.environment["queue_size"]
        next_state["latency"] = 0
        next_state["tick"] += 1
        return next_state

    base_state = {"completed": 0, "data_loss": 0, "latency": 3, "tick": 0}
    models = (
        FiniteStateModel(
            "sequential-safe-worker",
            {"queue-ready": base_state},
            ("queue-ready",),
            ("noop",),
            safe_transition,
            readout,
            ModelMetrics(cost=2.0, complexity=1.0, risk=0.8),
            assumptions=("jobs are independently processable",),
            capabilities=("work completion", "lossless processing"),
        ),
        FiniteStateModel(
            "parallel-audited-worker",
            {"queue-ready": base_state},
            ("queue-ready",),
            ("noop",),
            parallel_transition,
            readout,
            ModelMetrics(cost=4.0, complexity=2.5, risk=0.2),
            assumptions=("two workers may execute concurrently",),
            capabilities=("work completion", "lossless processing", "fast completion"),
        ),
        FiniteStateModel(
            "short-horizon-worker",
            {"queue-ready": base_state},
            ("queue-ready",),
            ("noop",),
            fragile_transition,
            readout,
            ModelMetrics(cost=1.0, complexity=0.8, risk=2.0),
            failure_boundaries=("loses state after two ticks",),
            capabilities=("short-term work completion",),
        ),
        FiniteStateModel(
            "drop-and-report-worker",
            {"queue-ready": base_state},
            ("queue-ready",),
            ("noop",),
            drop_transition,
            readout,
            ModelMetrics(cost=0.5, complexity=0.5, risk=5.0),
            capabilities=("reported completion",),
        ),
    )
    return spec, context, models


def science_closure_scenario() -> Tuple[MacroSpec, Context, FiniteStateModel]:
    """Position alone is not dynamically closed because velocity is hidden."""

    spec = MacroSpec(
        name="position-only dynamics",
        observables=("position",),
        objectives=(),
        equivalence=EquivalenceSpec(("position",)),
        horizon=1,
    )
    context = Context(scale="one-dimensional-particle")

    def transition(state, action, _context):
        acceleration = 1 if action == "push-right" else 0
        velocity = state["velocity"] + acceleration
        return {"position": state["position"] + velocity, "velocity": velocity}

    def readout(state, _context):
        return {"position": state["position"]}

    model = FiniteStateModel(
        "particle-with-hidden-velocity",
        {
            "moving-right": {"position": 0, "velocity": 1},
            "moving-left": {"position": 0, "velocity": -1},
            "resting": {"position": 0, "velocity": 0},
        },
        ("moving-right", "moving-left", "resting"),
        ("noop", "push-right"),
        transition,
        readout,
        ModelMetrics(cost=1.0, complexity=2.0, risk=0.0),
        capabilities=("position evolution",),
    )
    return spec, context, model


def residual_quotient_scenario() -> Tuple[
    EquivalenceSpec, Context, FiniteStateModel
]:
    """Opaque microstates whose future probe behavior induces four classes."""

    equivalence = EquivalenceSpec(("signal",))
    context = Context(scale="opaque-probe")

    def transition(state, action, _context):
        if action != "probe" or state["mode"] == "terminal":
            return dict(state)
        signal = -1 if state["mode"] == "left" else 1
        return {"mode": "terminal", "copy": 0, "signal": signal}

    model = FiniteStateModel(
        "opaque-probe-system",
        {
            "left-a": {"mode": "left", "copy": 0, "signal": 0},
            "left-b": {"mode": "left", "copy": 1, "signal": 0},
            "right": {"mode": "right", "copy": 0, "signal": 0},
        },
        ("left-a", "left-b", "right"),
        ("probe",),
        transition,
        lambda state, _context: {"signal": state["signal"]},
        ModelMetrics(cost=1.0, complexity=1.0, risk=0.0),
        capabilities=("behavioral quotient discovery",),
    )
    return equivalence, context, model


def partial_residual_scenario() -> Tuple[
    EquivalenceSpec, Context, FiniteStateModel
]:
    """Equal observations separated only by a locally supported action."""

    equivalence = EquivalenceSpec(("signal",))
    context = Context(scale="partial-operation")

    def transition(state, action, _context):
        if action == "consume":
            return {"enabled": False, "signal": 0}
        return dict(state)

    model = FiniteStateModel(
        "partial-consumer",
        {
            "enabled": {"enabled": True, "signal": 0},
            "disabled": {"enabled": False, "signal": 0},
        },
        ("enabled", "disabled"),
        ("consume",),
        transition,
        lambda state, _context: {"signal": state["signal"]},
        ModelMetrics(cost=1.0, complexity=1.0, risk=0.0),
        capabilities=("partial action support",),
        applicable=lambda state, action, _context: (
            action != "consume" or state["enabled"]
        ),
    )
    return equivalence, context, model


def composition_rule_scenario() -> Tuple[
    Tuple[CompositionExperiment, ...], Tuple[CompositionRule, ...]
]:
    """Competing parity rules with sparse, language-free observations."""

    experiment = CompositionExperiment(
        name="finite parity composition",
        states={"zero": {"parity": 0, "phase": 0}},
        initial_states=("zero",),
        actions=("add-zero", "add-one"),
        readout=lambda state, _context: {"parity": state["parity"]},
        equivalence=EquivalenceSpec(("parity",)),
        tests=(
            CompositionTest("empty", "zero", (), True, {"parity": 0}),
            CompositionTest(
                "one bit", "zero", ("add-one",), True, {"parity": 1}
            ),
            CompositionTest(
                "zero bit", "zero", ("add-zero",), True, {"parity": 0}
            ),
            CompositionTest(
                "two ones",
                "zero",
                ("add-one", "add-one"),
                True,
                {"parity": 0},
            ),
            CompositionTest(
                "zero then one",
                "zero",
                ("add-zero", "add-one"),
                True,
                {"parity": 1},
            ),
        ),
        context=Context(scale="finite-parity-composition"),
    )

    def parity_transition(state, action, _context):
        result = dict(state)
        if action == "add-one":
            result["parity"] = 1 - result["parity"]
        return result

    def delayed_failure_transition(state, action, _context):
        result = dict(state)
        if action in ("add-zero", "add-one"):
            if action == "add-one" and result["phase"] < 2:
                result["parity"] = 1 - result["parity"]
            result["phase"] = min(2, result["phase"] + 1)
        return result

    def constant_transition(state, _action, _context):
        result = dict(state)
        result["parity"] = 0
        return result

    rules = (
        CompositionRule("parity", parity_transition, description_length=8.0),
        CompositionRule(
            "delayed failure",
            delayed_failure_transition,
            description_length=8.0,
        ),
        CompositionRule(
            "constant zero", constant_transition, description_length=2.0
        ),
        CompositionRule(
            "forbid one",
            parity_transition,
            description_length=3.0,
            applicable=lambda _state, action, _context: action != "add-one",
        ),
    )
    return (experiment,), rules


def organization_interpretation_scenario():
    """One approval structure is compatible with several macro purposes."""

    context = Context(
        environment={"risk_level": "low"},
        scale="organization-process",
    )

    def transition(state, action, _context):
        next_state = dict(state)
        if action == "noop":
            next_state["fraud_blocked"] = 1
            next_state["audit_log"] = 1
            next_state["autonomy"] = 0
            next_state["cycle_time"] = 2
        return next_state

    def readout(state, _context):
        return dict(state)

    model = FiniteStateModel(
        "central-approval-process",
        {
            "request": {
                "fraud_blocked": 0,
                "audit_log": 0,
                "autonomy": 1,
                "cycle_time": 0,
            }
        },
        ("request",),
        ("noop",),
        transition,
        readout,
        ModelMetrics(cost=2.0, complexity=2.0, risk=1.0),
        capabilities=("fraud prevention", "auditability", "central control"),
    )

    def purpose_spec(name: str, field_name: str, operator: str, expected: int) -> MacroSpec:
        return MacroSpec(
            name=name,
            observables=(field_name,),
            objectives=(
                FieldRequirement(name, field_name, operator, expected),
            ),
            equivalence=EquivalenceSpec((field_name,)),
            horizon=1,
        )

    hypotheses = (
        PurposeHypothesis(
            "prevent fraud",
            PurposeLevel.FUNCTION,
            purpose_spec("prevent fraud", "fraud_blocked", "eq", 1),
            prior=0.6,
            explanation="approval serves as a risk-control function",
            predictions={"delegate-low-risk": 0.85, "remove-audit-log": 0.15},
        ),
        PurposeHypothesis(
            "provide auditability",
            PurposeLevel.FUNCTION,
            purpose_spec("provide auditability", "audit_log", "eq", 1),
            prior=0.5,
            explanation="approval exists to leave a reviewable trace",
            predictions={"delegate-low-risk": 0.75, "remove-audit-log": 0.05},
        ),
        PurposeHypothesis(
            "preserve central control",
            PurposeLevel.INTENTION,
            purpose_spec("preserve central control", "autonomy", "eq", 0),
            prior=0.4,
            explanation="a designer may intend to retain decision authority",
            predictions={"delegate-low-risk": 0.10, "remove-audit-log": 0.70},
        ),
    )
    experiments = (
        Experiment(
            "delegate-low-risk",
            "Would the process owner accept delegation for low-risk requests?",
            cost=0.1,
        ),
        Experiment(
            "remove-audit-log",
            "Would the process remain acceptable if approval stayed central but produced no audit log?",
            cost=0.2,
        ),
    )
    evidence = (
        Evidence(
            "fraud reviews were introduced after a loss incident",
            "prevent fraud",
            0.8,
            kind="selection-history",
            source="policy history",
        ),
    )
    return context, model, hypotheses, experiments, evidence


def scale_correspondence_scenario():
    """Two micro partitions coarse-grain to one aggregate dynamic state."""

    lower_context = Context(scale="component-state")
    upper_context = Context(scale="aggregate-state")

    def lower_transition(state, _action, _context):
        return {"left": state["left"] + 1, "right": state["right"]}

    def upper_transition(state, _action, _context):
        return {"total": state["total"] + 1}

    lower_model = FiniteStateModel(
        "two-component-dynamics",
        {
            "partition-a": {"left": 1, "right": 1},
            "partition-b": {"left": 0, "right": 2},
        },
        ("partition-a", "partition-b"),
        (),
        lower_transition,
        lambda state, _context: dict(state),
        ModelMetrics(cost=2.0, complexity=2.0, risk=0.0),
    )
    upper_model = FiniteStateModel(
        "aggregate-dynamics",
        {"aggregate": {"total": 2}},
        ("aggregate",),
        (),
        upper_transition,
        lambda state, _context: dict(state),
        ModelMetrics(cost=1.0, complexity=1.0, risk=0.0),
    )

    lower_scale = Scale(
        "micro",
        ("left", "right"),
        EquivalenceSpec(("left", "right")),
    )
    upper_scale = Scale(
        "macro",
        ("total",),
        EquivalenceSpec(("total",)),
    )
    correspondence = Correspondence(
        "sum-components",
        lower_scale,
        upper_scale,
        lambda snapshot, _context: {
            "total": snapshot["left"] + snapshot["right"]
        },
        scenario_projection=lambda key: ScenarioKey("aggregate", key.intervention),
        assumptions=("only the component sum is task-relevant",),
    )
    return (
        correspondence,
        lower_model,
        upper_model,
        lower_context,
        upper_context,
    )


def scale_correspondence_suite():
    """Calibration plus a separately declared holdout partition."""

    (
        correspondence,
        lower_model,
        upper_model,
        lower_context,
        upper_context,
    ) = scale_correspondence_scenario()
    lower_context = replace(
        lower_context,
        environment={"validation_split": "calibration"},
    )
    upper_context = replace(
        upper_context,
        environment={"validation_split": "calibration"},
    )

    holdout_lower = replace(
        lower_model,
        name="holdout-two-component-dynamics",
        states={
            "unseen-partition-a": {"left": 2, "right": 3},
            "unseen-partition-b": {"left": 4, "right": 1},
        },
        initial_states=("unseen-partition-a", "unseen-partition-b"),
    )
    holdout_upper = replace(
        upper_model,
        name="holdout-aggregate-dynamics",
        states={"aggregate": {"total": 5}},
    )
    holdout_lower_context = replace(
        lower_context,
        environment={"validation_split": "holdout"},
    )
    holdout_upper_context = replace(
        upper_context,
        environment={"validation_split": "holdout"},
    )
    cases = (
        CorrespondenceValidationCase(
            "known partitions",
            lower_model,
            upper_model,
            lower_context,
            upper_context,
            horizon=2,
        ),
        CorrespondenceValidationCase(
            "unseen partitions",
            holdout_lower,
            holdout_upper,
            holdout_lower_context,
            holdout_upper_context,
            horizon=2,
            role=CorrespondenceCaseRole.HOLDOUT,
            independent=True,
        ),
    )
    return correspondence, cases


def all_scenarios() -> Dict[str, object]:
    return {
        "software": software_scenario(),
        "science": science_closure_scenario(),
        "organization": organization_interpretation_scenario(),
        "partial_residual": partial_residual_scenario(),
        "composition_rules": composition_rule_scenario(),
        "residual_quotient": residual_quotient_scenario(),
        "correspondence": scale_correspondence_scenario(),
        "correspondence_suite": scale_correspondence_suite(),
    }
