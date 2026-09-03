"""Dynamical closure checks and a versioned concept memory."""

from __future__ import annotations

from dataclasses import replace
from itertools import combinations
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from .core import (
    ClosureReport,
    Concept,
    Context,
    Counterexample,
    FiniteStateModel,
    MacroSpec,
    UndefinedTransition,
)
from .structural import freeze_value, isolated_mapping


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
        if max_states < 1:
            raise ValueError("max_states must be positive")
        depth_limit = spec.horizon if max_depth is None else max_depth
        if depth_limit < 0:
            raise ValueError("max_depth must be non-negative")

        def state_key(state: Mapping[str, Any]) -> Tuple[Any, ...]:
            return freeze_value(
                dict(state),
                purpose="closure state deterministic structural identity",
            )

        analysis_errors = []
        error_keys = set()

        def record_error(phase: str, error: Exception, **witness: Any) -> None:
            key = (
                phase,
                type(error).__module__,
                type(error).__qualname__,
                str(error),
                tuple(sorted((name, repr(value)) for name, value in witness.items())),
            )
            if key in error_keys:
                return
            error_keys.add(key)
            analysis_errors.append(
                Counterexample(
                    kind="closure-analysis-error",
                    summary="closure analysis could not certify %s" % phase,
                    witness={
                        "model": model.name,
                        "phase": phase,
                        "error": "%s: %s" % (type(error).__name__, error),
                        **witness,
                    },
                    violated=("complete dynamical closure analysis",),
                    suggested_refinements=(
                        "use deterministic structural state values and pure model callbacks",
                    ),
                )
            )

        complete = True
        initial = []
        seen = set()
        for name in model.initial_states:
            if len(initial) >= max_states:
                complete = False
                break
            try:
                state = isolated_mapping(
                    model.states[name], purpose="closure initial state"
                )
                key = state_key(state)
            except Exception as error:
                complete = False
                record_error("initial-state identity", error, initial_state=name)
                continue
            initial.append((name, state))
            seen.add(key)

        reachable = list(initial)
        frontier = list(reachable)
        actions = tuple(
            dict.fromkeys(
                ("noop",)
                + tuple(action for action in model.actions if action != "noop")
            )
        )
        for depth in range(1, depth_limit + 1):
            next_frontier = []
            for source_name, state in frontier:
                for action in actions:
                    try:
                        next_state = model.audited_step(state, action, context)
                    except UndefinedTransition:
                        continue
                    except Exception as error:
                        complete = False
                        record_error(
                            "reachable transition",
                            error,
                            source_state=source_name,
                            action=action,
                        )
                        continue
                    try:
                        key = state_key(next_state)
                    except Exception as error:
                        complete = False
                        record_error(
                            "successor identity",
                            error,
                            source_state=source_name,
                            action=action,
                        )
                        continue
                    if key in seen:
                        continue
                    if len(reachable) >= max_states:
                        complete = False
                        break
                    seen.add(key)
                    named = ("%s --%s--> reachable:%d:%d" % (source_name, action, depth, len(reachable)), next_state)
                    reachable.append(named)
                    next_frontier.append(named)
                if not complete:
                    break
            frontier = next_frontier
            if not complete or not frontier:
                break

        # Reaching the caller's depth bound with an unexpanded frontier proves
        # only bounded non-refutation, never exhaustion of the reachable state
        # space.  Keep the report incomplete until the frontier is empty.
        if complete and frontier:
            complete = False

        violations = []
        checked = 0
        state_items = []
        for state_name, state in reachable:
            try:
                observation = model.audited_observe(state, context)
                # Validate the declared equivalence interface before pairwise use.
                spec.equivalence.signature(observation)
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
                        if left[key] != right[key]
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

        counterexamples = list(analysis_errors)
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
        )


class ConceptLibrary:
    """Small in-memory concept store; persistence can be supplied by an adapter."""

    def __init__(self, concepts: Iterable[Concept] = ()) -> None:
        self._concepts = {concept.name: concept for concept in concepts}

    def add(self, concept: Concept) -> None:
        if concept.name in self._concepts:
            raise ValueError("concept %r already exists" % concept.name)
        self._concepts[concept.name] = concept

    def get(self, name: str) -> Concept:
        return self._concepts[name]

    def all(self) -> Tuple[Concept, ...]:
        return tuple(sorted(self._concepts.values(), key=lambda item: item.name))

    def record_judgment(
        self,
        name: str,
        example: str,
        accepted: bool,
        boundary: Optional[str] = None,
    ) -> Concept:
        concept = self.get(name)
        positives = concept.positive_examples
        negatives = concept.negative_examples
        if accepted and example not in positives:
            positives += (example,)
        if accepted and example in negatives:
            negatives = tuple(item for item in negatives if item != example)
        if not accepted and example not in negatives:
            negatives += (example,)
        if not accepted and example in positives:
            positives = tuple(item for item in positives if item != example)
        boundaries = concept.boundaries
        if boundary and boundary not in boundaries:
            boundaries += (boundary,)
        if (
            positives == concept.positive_examples
            and negatives == concept.negative_examples
            and boundaries == concept.boundaries
        ):
            return concept
        updated = replace(
            concept,
            positive_examples=positives,
            negative_examples=negatives,
            boundaries=boundaries,
            version=concept.version + 1,
        )
        self._concepts[name] = updated
        return updated

    def refine_from_counterexample(self, name: str, counterexample: Counterexample) -> Concept:
        concept = self.get(name)
        boundary = counterexample.summary
        example = repr(dict(counterexample.witness))
        positives = tuple(item for item in concept.positive_examples if item != example)
        negatives = concept.negative_examples
        if example not in negatives:
            negatives += (example,)
        boundaries = concept.boundaries
        if boundary not in boundaries:
            boundaries += (boundary,)
        definitions = concept.candidate_definitions
        for suggestion in counterexample.suggested_refinements:
            if suggestion not in definitions:
                definitions += (suggestion,)
        changed = (
            positives != concept.positive_examples
            or negatives != concept.negative_examples
            or boundaries != concept.boundaries
            or definitions != concept.candidate_definitions
        )
        if not changed:
            return concept
        updated = replace(
            concept,
            positive_examples=positives,
            negative_examples=negatives,
            boundaries=boundaries,
            candidate_definitions=definitions,
            version=concept.version + 1,
        )
        self._concepts[name] = updated
        return updated
