"""Dynamical closure checks with separate witnesses and diagnostics."""

from __future__ import annotations

from itertools import combinations
from typing import Any, Dict, Optional

from .core import (
    ClosureReport,
    Context,
    Counterexample,
    VerificationIssue,
    FiniteStateModel,
    MacroSpec,
    UndefinedTransition,
)
from ._exploration import explore_reachable
from .structural import freeze_value


class ClosureAnalyzer:
    """Find transition or action-support differences inside a macro class."""

    def analyze(
        self,
        model: FiniteStateModel,
        spec: MacroSpec,
        context: Context,
        max_depth: Optional[int] = None,
        max_states: int = 1_000,
    ) -> ClosureReport:
        if type(max_states) is not int or max_states < 1:
            raise ValueError("max_states must be positive")
        depth_limit = spec.horizon if max_depth is None else max_depth
        if type(depth_limit) is not int or depth_limit < 0:
            raise ValueError("max_depth must be non-negative")

        analysis_errors = []
        error_keys = set()

        def record_error(phase: str, error: Exception, **witness: Any) -> None:
            key = (
                phase,
                type(error).__module__,
                type(error).__qualname__,
                str(error),
                freeze_value(witness, purpose='closure diagnostic witness'),
            )
            if key in error_keys:
                return
            error_keys.add(key)
            analysis_errors.append(VerificationIssue(
                phase, "%s: %s" % (type(error).__name__, error),
                {"model": model.name, **witness},
            ))

        exploration = explore_reachable(model, context, max_depth=depth_limit, max_states=max_states)
        complete = exploration.complete
        for phase, error, witness in exploration.errors:
            record_error(phase, error, **witness)
        reachable = [
            (state.source_initial_state if not state.actions else
             '%s --%s--> reachable:%d' % (state.source_initial_state, '/'.join(state.actions), state.index),
             state.micro_state)
            for state in exploration.states
        ]
        actions = exploration.actions

        violations = []
        checked = 0
        state_items = []
        for state_name, state in reachable:
            try:
                observation = model.audited_observe(state, context)
                # Validate the declared equivalence interface before pairwise use.
                freeze_value(spec.equivalence.signature(observation))
            except Exception as error:
                complete = False
                record_error(
                    "reachable-state readout",
                    error,
                    state=state_name,
                )
                continue
            state_items.append((state_name, state, observation))

        for (
            left_name,
            left,
            left_observed,
        ), (
            right_name,
            right,
            right_observed,
        ) in combinations(tuple(state_items), 2):
            if not spec.equivalence.equivalent(left_observed, right_observed):
                continue
            for action in actions:
                checked += 1
                try:
                    left_next = model.audited_step(left, action, context)
                    left_next_observed = model.audited_observe(
                        left_next, context
                    )
                    freeze_value(spec.equivalence.signature(left_next_observed))
                    left_defined = True
                except UndefinedTransition:
                    left_next_observed = None
                    left_defined = False
                except Exception as error:
                    complete = False
                    record_error(
                        "paired transition",
                        error,
                        state=left_name,
                        action=action,
                    )
                    continue
                try:
                    right_next = model.audited_step(right, action, context)
                    right_next_observed = model.audited_observe(
                        right_next, context
                    )
                    freeze_value(spec.equivalence.signature(right_next_observed))
                    right_defined = True
                except UndefinedTransition:
                    right_next_observed = None
                    right_defined = False
                except Exception as error:
                    complete = False
                    record_error(
                        "paired transition",
                        error,
                        state=right_name,
                        action=action,
                    )
                    continue
                if not left_defined and not right_defined:
                    continue
                support_mismatch = left_defined != right_defined
                if (
                    not support_mismatch
                    and spec.equivalence.equivalent(
                        left_next_observed, right_next_observed
                    )
                ):
                    continue
                differing = tuple(
                    sorted(
                        key
                        for key in set(left).intersection(right)
                        if freeze_value(left[key]) != freeze_value(right[key])
                    )
                )
                violations.append(
                    {
                        "left_name": left_name,
                        "right_name": right_name,
                        "left": dict(left),
                        "right": dict(right),
                        "action": action,
                        "left_defined": left_defined,
                        "right_defined": right_defined,
                        "left_next": (
                            dict(left_next_observed) if left_defined else None
                        ),
                        "right_next": (
                            dict(right_next_observed) if right_defined else None
                        ),
                        "differing": differing,
                        "kind": (
                            "dynamical-support-non-closure"
                            if support_mismatch
                            else "dynamical-non-closure"
                        ),
                        "summary": (
                            "two macro-equivalent states have different action support"
                            if support_mismatch
                            else "two macro-equivalent states evolve into different macro classes"
                        ),
                    }
                )

        separating_counts: Dict[str, int] = {}
        for violation in violations:
            for feature in violation["differing"]:
                separating_counts[feature] = separating_counts.get(feature, 0) + 1
        ranked_features = tuple(
            feature
            for feature, _ in sorted(
                separating_counts.items(), key=lambda item: (-item[1], item[0])
            )
        )

        counterexamples = []
        for violation in violations:
            local_ranked = tuple(
                feature
                for feature in ranked_features
                if feature in violation["differing"]
            )
            counterexamples.append(
                Counterexample(
                    kind=violation["kind"],
                    summary=violation["summary"],
                    witness={
                        "left_state": violation["left_name"],
                        "right_state": violation["right_name"],
                        "left_micro": violation["left"],
                        "right_micro": violation["right"],
                        "action": violation["action"],
                        "left_defined": violation["left_defined"],
                        "right_defined": violation["right_defined"],
                        "left_next_macro": violation["left_next"],
                        "right_next_macro": violation["right_next"],
                    },
                    violated=("dynamical closure of %s" % spec.name,),
                    suggested_refinements=tuple(
                        "consider promoting separating micro feature %r to a macro observable" % feature
                        for feature in local_ranked
                    ),
                )
            )
        return ClosureReport(
            closed=not counterexamples and complete,
            checked_pairs=checked,
            counterexamples=tuple(counterexamples),
            suggested_features=ranked_features,
            complete=complete,
            explored_states=len(reachable),
            diagnostics=tuple(analysis_errors),
        )
