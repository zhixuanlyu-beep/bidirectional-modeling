"""Context-relative residual quotients for finite deterministic models.

The analyzer discovers the coarsest reachable-state partition that preserves a
declared observation equivalence and is stable under every model action.  It
does not attach natural-language meaning to a class: a class is identified by
the observations obtainable after all available action contexts.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Deque, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .core import (
    Context,
    EquivalenceSpec,
    FiniteStateModel,
    UndefinedTransition,
)
from .provenance import (
    context_fingerprint,
    equivalence_fingerprint,
    safe_context_fingerprint,
    safe_equivalence_fingerprint,
)
from .structural import (
    FrozenValue,
    fingerprint_value,
    freeze_value,
    isolated_mapping,
    validate_fingerprint,
)


class _Edge(Enum):
    UNDEFINED = "undefined"
    UNKNOWN = "unknown"


def _freeze(value: Any) -> FrozenValue:
    return freeze_value(
        value,
        purpose="residual state values need a deterministic structural identity",
    )


def _state_key(state: Mapping[str, Any]) -> FrozenValue:
    return _freeze(dict(state))


def _normalize_partition(signatures: Iterable[Any]) -> Tuple[int, ...]:
    identifiers: Dict[FrozenValue, int] = {}
    partition = []
    for signature in signatures:
        key = _freeze(signature)
        if key not in identifiers:
            identifiers[key] = len(identifiers)
        partition.append(identifiers[key])
    return tuple(partition)


def _partition_groups(partition: Sequence[int]) -> Tuple[Tuple[int, ...], ...]:
    groups: Dict[int, list[int]] = {}
    for state_index, class_id in enumerate(partition):
        groups.setdefault(class_id, []).append(state_index)
    return tuple(tuple(groups[class_id]) for class_id in sorted(groups))


def _split_representatives(
    before: Sequence[int], after: Sequence[int]
) -> Tuple[Tuple[int, int], ...]:
    """Choose a linear-size witness basis for every strict block split."""

    children: Dict[int, Dict[int, int]] = {}
    for state_index, (old_class, new_class) in enumerate(zip(before, after)):
        children.setdefault(old_class, {}).setdefault(new_class, state_index)
    pairs = []
    for old_class in sorted(children):
        representatives = tuple(children[old_class].values())
        if len(representatives) < 2:
            continue
        anchor = representatives[0]
        pairs.extend((anchor, other) for other in representatives[1:])
    return tuple(pairs)


@dataclass(frozen=True)
class ResidualState:
    """One reachable microstate and a shortest discovered path to it."""

    index: int
    source_initial_state: str
    actions: Tuple[str, ...]
    micro_state: Mapping[str, Any]
    observation: Mapping[str, Any]
    observation_signature: Tuple[Any, ...]


@dataclass(frozen=True)
class DistinguishingContext:
    """A shortest action context that separates two enumerated microstates."""

    left_state: int
    right_state: int
    actions: Tuple[str, ...]
    left_terminal_signature: Tuple[Any, ...]
    right_terminal_signature: Tuple[Any, ...]
    discovered_at_depth: int
    left_defined: bool = True
    right_defined: bool = True

    @property
    def depth(self) -> int:
        return len(self.actions)


@dataclass(frozen=True)
class ResidualFiltrationLevel:
    """The observable partition induced by contexts up to one depth."""

    context_depth: int
    state_classes: Tuple[int, ...]
    new_distinguishing_contexts: Tuple[Tuple[str, ...], ...] = ()

    @property
    def class_count(self) -> int:
        return len(set(self.state_classes))

    @property
    def classes(self) -> Tuple[Tuple[int, ...], ...]:
        return _partition_groups(self.state_classes)


@dataclass(frozen=True)
class ResidualClass:
    """One residual behavior class in the final discovered partition."""

    identifier: int
    members: Tuple[int, ...]
    representative: int

    def __post_init__(self) -> None:
        if not self.members:
            raise ValueError("a residual class must contain at least one state")
        if self.representative not in self.members:
            raise ValueError("the representative must be a class member")


@dataclass(frozen=True)
class ResidualTransition:
    """Observed targets of one action from one residual class."""

    source_class: int
    action: str
    target_classes: Tuple[int, ...]
    complete: bool
    undefined: bool = False

    @property
    def well_defined(self) -> bool:
        return self.complete and (
            (self.undefined and not self.target_classes)
            or (not self.undefined and len(self.target_classes) == 1)
        )

    @property
    def defined(self) -> bool:
        return self.well_defined and not self.undefined


@dataclass(frozen=True)
class ResidualQuotient:
    """A finite quotient candidate, including non-congruence diagnostics."""

    states: Tuple[ResidualState, ...]
    state_classes: Tuple[int, ...]
    classes: Tuple[ResidualClass, ...]
    transitions: Tuple[ResidualTransition, ...]
    actions: Tuple[str, ...]
    initial_state_classes: Tuple[Tuple[str, int], ...]

    @property
    def class_count(self) -> int:
        return len(self.classes)

    def class_for_state(self, state_index: int) -> int:
        if state_index < 0 or state_index >= len(self.state_classes):
            raise IndexError("residual state index out of range")
        return self.state_classes[state_index]

    def next_class(self, source_class: int, action: str) -> int:
        for transition in self.transitions:
            if (
                transition.source_class == source_class
                and transition.action == action
            ):
                if not transition.well_defined:
                    raise ValueError(
                        "the requested quotient transition is not well-defined"
                    )
                if transition.undefined:
                    raise UndefinedTransition(
                        "action %r is undefined for residual class %d"
                        % (action, source_class)
                    )
                return transition.target_classes[0]
        raise KeyError((source_class, action))


def _residual_model_fingerprint(
    model: FiniteStateModel,
    states: Sequence[ResidualState],
    actions: Sequence[str],
    transitions: Mapping[Tuple[int, str], Any],
    initial_indices: Sequence[Tuple[str, int]],
) -> str:
    """Canonicalize the bounded state/observation/transition evidence."""

    state_keys = {
        state.index: _state_key(state.micro_state)
        for state in states
    }
    state_evidence = tuple(
        sorted(
            freeze_value(
                (
                    state_keys[state.index],
                    dict(state.observation),
                ),
                purpose="residual model evidence fingerprint",
            )
            for state in states
        )
    )
    transition_evidence = []
    for (source, action), target in transitions.items():
        if target is _Edge.UNDEFINED:
            outcome: Tuple[Any, ...] = ("undefined",)
        elif target is _Edge.UNKNOWN:
            outcome = ("unknown",)
        else:
            outcome = ("state", state_keys[target])
        transition_evidence.append(
            freeze_value(
                (state_keys[source], action, outcome),
                purpose="residual model evidence fingerprint",
            )
        )
    initial_evidence = tuple(
        sorted(
            freeze_value(
                (name, state_keys[index]),
                purpose="residual model evidence fingerprint",
            )
            for name, index in initial_indices
        )
    )
    return fingerprint_value(
        (
            "residual-observed-model-v1",
            type(model).__module__,
            type(model).__qualname__,
            model.name,
            tuple(sorted(actions)),
            state_evidence,
            tuple(sorted(transition_evidence)),
            initial_evidence,
        ),
        purpose="residual model evidence fingerprint",
    )


def _residual_protocol_fingerprint(
    model_digest: str,
    context_digest: str,
    equivalence_digest: str,
    max_reachability_depth: Optional[int],
    max_states: int,
    max_context_depth: Optional[int],
    max_context_tests: int,
) -> str:
    return fingerprint_value(
        (
            "residual-quotient-analyzer-v1",
            model_digest,
            context_digest,
            equivalence_digest,
            max_reachability_depth,
            max_states,
            max_context_depth,
            max_context_tests,
        ),
        purpose="residual quotient protocol fingerprint",
    )


@dataclass(frozen=True)
class ResidualContextRefinement:
    """One counterexample-guided addition to a finite context basis."""

    iteration: int
    left_state: int
    right_state: int
    context: Tuple[str, ...]
    state_classes: Tuple[int, ...]

    @property
    def class_count(self) -> int:
        return len(set(self.state_classes))


def _residual_claim_fingerprint(
    protocol_digest: str,
    model_name: str,
    equivalence_signature: Tuple[Any, ...],
    quotient: ResidualQuotient,
    filtration: Tuple[ResidualFiltrationLevel, ...],
    distinguishing_contexts: Tuple[DistinguishingContext, ...],
    complete: bool,
    stable: bool,
    congruent: bool,
    exploration_depth: int,
    transition_evaluations: int,
    boundaries: Tuple[str, ...],
    context_basis: Tuple[Tuple[str, ...], ...],
    context_refinements: Tuple[ResidualContextRefinement, ...],
    context_basis_reproduces_partition: bool,
) -> str:
    quotient_signature = (
        "residual-quotient-v1",
        tuple(
            (
                item.index,
                item.source_initial_state,
                item.actions,
                item.micro_state,
                item.observation,
                item.observation_signature,
            )
            for item in quotient.states
        ),
        quotient.state_classes,
        tuple(
            (item.identifier, item.members, item.representative)
            for item in quotient.classes
        ),
        tuple(
            (
                item.source_class,
                item.action,
                item.target_classes,
                item.complete,
                item.undefined,
            )
            for item in quotient.transitions
        ),
        quotient.actions,
        quotient.initial_state_classes,
    )
    filtration_signature = tuple(
        (
            item.context_depth,
            item.state_classes,
            item.new_distinguishing_contexts,
        )
        for item in filtration
    )
    distinction_signature = tuple(
        (
            item.left_state,
            item.right_state,
            item.actions,
            item.left_terminal_signature,
            item.right_terminal_signature,
            item.discovered_at_depth,
            item.left_defined,
            item.right_defined,
        )
        for item in distinguishing_contexts
    )
    refinement_signature = tuple(
        (
            item.iteration,
            item.left_state,
            item.right_state,
            item.context,
            item.state_classes,
        )
        for item in context_refinements
    )
    return fingerprint_value(
        (
            "residual-quotient-claim-v1",
            protocol_digest,
            model_name,
            equivalence_signature,
            quotient_signature,
            filtration_signature,
            distinction_signature,
            complete,
            stable,
            congruent,
            exploration_depth,
            transition_evaluations,
            boundaries,
            context_basis,
            refinement_signature,
            context_basis_reproduces_partition,
        ),
        purpose="residual quotient claim fingerprint",
    )


@dataclass(frozen=True)
class ResidualQuotientReport:
    """Certificate and boundedness information for one quotient discovery."""

    model_name: str
    context_fingerprint: str
    model_fingerprint: str
    equivalence_fingerprint: str
    protocol_fingerprint: str
    claim_fingerprint: str
    max_reachability_depth: Optional[int]
    max_states: int
    max_context_depth: Optional[int]
    max_context_tests: int
    equivalence_signature: Tuple[Any, ...]
    quotient: ResidualQuotient
    filtration: Tuple[ResidualFiltrationLevel, ...]
    distinguishing_contexts: Tuple[DistinguishingContext, ...]
    complete: bool
    stable: bool
    congruent: bool
    exploration_depth: int
    transition_evaluations: int
    boundaries: Tuple[str, ...] = ()
    context_basis: Tuple[Tuple[str, ...], ...] = ()
    context_refinements: Tuple[ResidualContextRefinement, ...] = ()
    context_basis_reproduces_partition: bool = False

    def __post_init__(self) -> None:
        if not self.model_name:
            raise ValueError("residual report model name must be non-empty")
        if self.exploration_depth < 0 or self.transition_evaluations < 0:
            raise ValueError("residual report counts must be non-negative")
        for label, value in (
            ("max_reachability_depth", self.max_reachability_depth),
            ("max_context_depth", self.max_context_depth),
        ):
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError("%s must be a non-negative integer or None" % label)
        for label, value in (
            ("max_states", self.max_states),
            ("max_context_tests", self.max_context_tests),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 1
            ):
                raise ValueError("%s must be a positive integer" % label)
        for label, fingerprint in (
            ("residual context fingerprint", self.context_fingerprint),
            ("residual model fingerprint", self.model_fingerprint),
            ("residual equivalence fingerprint", self.equivalence_fingerprint),
            ("residual protocol fingerprint", self.protocol_fingerprint),
            ("residual claim fingerprint", self.claim_fingerprint),
        ):
            validate_fingerprint(fingerprint, purpose=label)
        expected_equivalence = fingerprint_value(
            ("equivalence-v1", self.equivalence_signature),
            purpose="equivalence deterministic structural fingerprint",
        )
        if expected_equivalence != self.equivalence_fingerprint:
            raise ValueError(
                "residual equivalence signature does not match its fingerprint"
            )
        expected_protocol = _residual_protocol_fingerprint(
            self.model_fingerprint,
            self.context_fingerprint,
            self.equivalence_fingerprint,
            self.max_reachability_depth,
            self.max_states,
            self.max_context_depth,
            self.max_context_tests,
        )
        if expected_protocol != self.protocol_fingerprint:
            raise ValueError(
                "residual report fields do not match its protocol fingerprint"
            )
        expected_claim = _residual_claim_fingerprint(
            self.protocol_fingerprint,
            self.model_name,
            self.equivalence_signature,
            self.quotient,
            self.filtration,
            self.distinguishing_contexts,
            self.complete,
            self.stable,
            self.congruent,
            self.exploration_depth,
            self.transition_evaluations,
            self.boundaries,
            self.context_basis,
            self.context_refinements,
            self.context_basis_reproduces_partition,
        )
        if expected_claim != self.claim_fingerprint:
            raise ValueError(
                "residual report fields do not match its claim fingerprint"
            )

    def verify_integrity(self) -> bool:
        """Return whether protocol and discovered quotient fields remain intact."""

        try:
            expected_equivalence = fingerprint_value(
                ("equivalence-v1", self.equivalence_signature),
                purpose="equivalence deterministic structural fingerprint",
            )
            expected_protocol = _residual_protocol_fingerprint(
                self.model_fingerprint,
                self.context_fingerprint,
                self.equivalence_fingerprint,
                self.max_reachability_depth,
                self.max_states,
                self.max_context_depth,
                self.max_context_tests,
            )
            expected_claim = _residual_claim_fingerprint(
                self.protocol_fingerprint,
                self.model_name,
                self.equivalence_signature,
                self.quotient,
                self.filtration,
                self.distinguishing_contexts,
                self.complete,
                self.stable,
                self.congruent,
                self.exploration_depth,
                self.transition_evaluations,
                self.boundaries,
                self.context_basis,
                self.context_refinements,
                self.context_basis_reproduces_partition,
            )
        except Exception:
            return False
        return (
            expected_equivalence == self.equivalence_fingerprint
            and expected_protocol == self.protocol_fingerprint
            and expected_claim == self.claim_fingerprint
        )

    @property
    def minimal(self) -> bool:
        """Whether minimality is certified on the declared reachable domain."""

        return (
            self.verify_integrity()
            and self.complete
            and self.stable
            and self.congruent
        )

    @property
    def explored_states(self) -> int:
        return len(self.quotient.states)

    def binds_context(self, context: Context) -> bool:
        try:
            return (
                self.verify_integrity()
                and context_fingerprint(context) == self.context_fingerprint
            )
        except Exception:
            return False

    def binds_equivalence(self, equivalence: EquivalenceSpec) -> bool:
        try:
            return (
                self.verify_integrity()
                and equivalence_fingerprint(equivalence)
                == self.equivalence_fingerprint
            )
        except Exception:
            return False


class ResidualQuotientAnalyzer:
    """Discover the coarsest observation-preserving reachable-state quotient."""

    def analyze(
        self,
        model: FiniteStateModel,
        equivalence: EquivalenceSpec,
        context: Context,
        max_reachability_depth: Optional[int] = None,
        max_states: int = 1_000,
        max_context_depth: Optional[int] = None,
        max_context_tests: int = 256,
    ) -> ResidualQuotientReport:
        if max_states < 1:
            raise ValueError("max_states must be positive")
        if max_reachability_depth is not None and max_reachability_depth < 0:
            raise ValueError("max_reachability_depth must be non-negative")
        if max_context_depth is not None and max_context_depth < 0:
            raise ValueError("max_context_depth must be non-negative")
        if max_context_tests < 1:
            raise ValueError("max_context_tests must be positive")

        boundaries = []
        context_digest, context_error = safe_context_fingerprint(context)
        equivalence_digest, equivalence_error = safe_equivalence_fingerprint(
            equivalence
        )
        binding_complete = True
        for label, error in (
            ("context", context_error),
            ("equivalence", equivalence_error),
        ):
            if error is not None:
                binding_complete = False
                boundaries.append(
                    "%s could not be fingerprinted: %s" % (label, error)
                )
        equivalence_signature = equivalence.semantic_signature()

        actions = tuple(
            dict.fromkeys(
                ("noop",)
                + tuple(action for action in model.actions if action != "noop")
            )
        )
        states = []
        state_indices: Dict[FrozenValue, int] = {}
        observation_keys = []
        initial_indices = []
        frontier: Deque[int] = deque()
        transitions: Dict[Tuple[int, str], Any] = {}
        transition_evaluations = 0
        state_limit_hit = False
        reachability_limit_hit = False

        def add_state(
            state: Mapping[str, Any],
            source_initial_state: str,
            path: Tuple[str, ...],
        ) -> Tuple[int, bool]:
            micro_state = isolated_mapping(
                state, purpose="residual microstate"
            )
            key = _state_key(micro_state)
            known = state_indices.get(key)
            if known is not None:
                return known, False
            observation = model.audited_observe(micro_state, context)
            signature = equivalence.signature(observation)
            index = len(states)
            state_indices[key] = index
            states.append(
                ResidualState(
                    index=index,
                    source_initial_state=source_initial_state,
                    actions=path,
                    micro_state=micro_state,
                    observation=observation,
                    observation_signature=signature,
                )
            )
            observation_keys.append(_freeze(signature))
            frontier.append(index)
            return index, True

        for initial_name in model.initial_states:
            initial_key = _state_key(model.states[initial_name])
            if initial_key not in state_indices and len(states) >= max_states:
                state_limit_hit = True
                boundaries.append(
                    "initial-state enumeration reached max_states=%d before %r"
                    % (max_states, initial_name)
                )
                break
            state_index, _ = add_state(
                model.states[initial_name], initial_name, ()
            )
            initial_indices.append((initial_name, state_index))

        if not states:
            boundaries.append(
                "the model has no initial states, so no residual domain was enumerated"
            )
            quotient = ResidualQuotient((), (), (), (), actions, ())
            filtration = (ResidualFiltrationLevel(0, ()),)
            boundary_results = tuple(boundaries)
            model_digest = _residual_model_fingerprint(
                model, states, actions, transitions, initial_indices
            )
            protocol_digest = _residual_protocol_fingerprint(
                model_digest,
                context_digest,
                equivalence_digest,
                max_reachability_depth,
                max_states,
                max_context_depth,
                max_context_tests,
            )
            claim_digest = _residual_claim_fingerprint(
                protocol_digest,
                model.name,
                equivalence_signature,
                quotient,
                filtration,
                (),
                False,
                False,
                False,
                0,
                0,
                boundary_results,
                (),
                (),
                False,
            )
            return ResidualQuotientReport(
                model_name=model.name,
                context_fingerprint=context_digest,
                model_fingerprint=model_digest,
                equivalence_fingerprint=equivalence_digest,
                protocol_fingerprint=protocol_digest,
                claim_fingerprint=claim_digest,
                max_reachability_depth=max_reachability_depth,
                max_states=max_states,
                max_context_depth=max_context_depth,
                max_context_tests=max_context_tests,
                equivalence_signature=equivalence_signature,
                quotient=quotient,
                filtration=filtration,
                distinguishing_contexts=(),
                complete=False,
                stable=False,
                congruent=False,
                exploration_depth=0,
                transition_evaluations=0,
                boundaries=boundary_results,
            )

        while frontier:
            state_index = frontier.popleft()
            state = states[state_index]
            depth = len(state.actions)
            if (
                max_reachability_depth is not None
                and depth >= max_reachability_depth
            ):
                reachability_limit_hit = True
                for action in actions:
                    transitions[(state_index, action)] = _Edge.UNKNOWN
                continue

            for action in actions:
                transition_evaluations += 1
                try:
                    successor = model.audited_step(
                        state.micro_state, action, context
                    )
                    key = _state_key(successor)
                    successor_index = state_indices.get(key)
                    if successor_index is None:
                        if len(states) >= max_states:
                            state_limit_hit = True
                            transitions[(state_index, action)] = _Edge.UNKNOWN
                            continue
                        successor_index, _ = add_state(
                            successor,
                            state.source_initial_state,
                            state.actions + (action,),
                        )
                    transitions[(state_index, action)] = successor_index
                except UndefinedTransition:
                    transitions[(state_index, action)] = _Edge.UNDEFINED
                except Exception as error:
                    transitions[(state_index, action)] = _Edge.UNKNOWN
                    boundaries.append(
                        "transition from state %d under %r could not be certified: %s"
                        % (state_index, action, error)
                    )

        if state_limit_hit:
            boundaries.append(
                "reachable-state enumeration reached max_states=%d" % max_states
            )
        if reachability_limit_hit:
            boundaries.append(
                "reachable-state enumeration stopped at depth %d"
                % max_reachability_depth
            )

        complete = binding_complete and (
            not state_limit_hit
            and not reachability_limit_hit
            and len(transitions) == len(states) * len(actions)
            and all(target is not _Edge.UNKNOWN for target in transitions.values())
        )

        initial_partition = _normalize_partition(observation_keys)
        distinctions = []

        def shortest_context(
            left_state: int, right_state: int
        ) -> Optional[Tuple[str, ...]]:
            if observation_keys[left_state] != observation_keys[right_state]:
                return ()
            queue = deque(((left_state, right_state, ()),))
            visited = {(left_state, right_state)}
            while queue:
                left, right, prefix = queue.popleft()
                for action in actions:
                    left_next = transitions.get(
                        (left, action), _Edge.UNKNOWN
                    )
                    right_next = transitions.get(
                        (right, action), _Edge.UNKNOWN
                    )
                    if (
                        left_next is _Edge.UNKNOWN
                        or right_next is _Edge.UNKNOWN
                    ):
                        continue
                    word = prefix + (action,)
                    if (left_next is _Edge.UNDEFINED) != (
                        right_next is _Edge.UNDEFINED
                    ):
                        return word
                    if left_next is _Edge.UNDEFINED:
                        continue
                    if observation_keys[left_next] != observation_keys[right_next]:
                        return word
                    pair = (left_next, right_next)
                    if pair not in visited:
                        visited.add(pair)
                        queue.append((left_next, right_next, word))
            return None

        def context_result(
            state_index: int, word: Sequence[str]
        ) -> Tuple[bool, Optional[int]]:
            current = state_index
            for action in word:
                target = transitions.get((current, action), _Edge.UNKNOWN)
                if target is _Edge.UNKNOWN:
                    raise ValueError("a distinguishing context crossed an unknown edge")
                if target is _Edge.UNDEFINED:
                    return False, None
                current = target
            return True, current

        def distinctions_for_split(
            before: Sequence[int],
            after: Sequence[int],
            depth: int,
        ) -> Tuple[Tuple[str, ...], ...]:
            words = []
            for left, right in _split_representatives(before, after):
                word = shortest_context(left, right)
                if word is None:
                    continue
                left_defined, left_terminal = context_result(left, word)
                right_defined, right_terminal = context_result(right, word)
                distinctions.append(
                    DistinguishingContext(
                        left_state=left,
                        right_state=right,
                        actions=word,
                        left_terminal_signature=(
                            states[left_terminal].observation_signature
                            if left_terminal is not None
                            else ()
                        ),
                        right_terminal_signature=(
                            states[right_terminal].observation_signature
                            if right_terminal is not None
                            else ()
                        ),
                        discovered_at_depth=depth,
                        left_defined=left_defined,
                        right_defined=right_defined,
                    )
                )
                if word not in words:
                    words.append(word)
            return tuple(words)

        direct_contexts = distinctions_for_split(
            (0,) * len(states), initial_partition, 0
        )
        levels = [
            ResidualFiltrationLevel(0, initial_partition, direct_contexts)
        ]
        current_partition = initial_partition
        current_depth = 0
        stable = False
        while True:
            signatures = []
            for state_index, current_class in enumerate(current_partition):
                targets = []
                for action in actions:
                    target = transitions.get(
                        (state_index, action), _Edge.UNKNOWN
                    )
                    if target is _Edge.UNKNOWN:
                        targets.append(("unknown",))
                    elif target is _Edge.UNDEFINED:
                        targets.append(("undefined",))
                    else:
                        targets.append(("state", current_partition[target]))
                signatures.append((current_class, tuple(targets)))
            refined = _normalize_partition(signatures)
            if refined == current_partition:
                stable = True
                break
            if (
                max_context_depth is not None
                and current_depth >= max_context_depth
            ):
                boundaries.append(
                    "context refinement stopped at depth %d before stabilization"
                    % max_context_depth
                )
                break
            current_depth += 1
            new_contexts = distinctions_for_split(
                current_partition, refined, current_depth
            )
            current_partition = refined
            levels.append(
                ResidualFiltrationLevel(
                    current_depth, current_partition, new_contexts
                )
            )

        groups = _partition_groups(current_partition)
        classes = tuple(
            ResidualClass(class_id, members, members[0])
            for class_id, members in enumerate(groups)
        )
        quotient_transitions = []
        congruent = bool(classes)
        for residual_class in classes:
            for action in actions:
                targets = set()
                transition_complete = True
                undefined = False
                for state_index in residual_class.members:
                    target = transitions.get(
                        (state_index, action), _Edge.UNKNOWN
                    )
                    if target is _Edge.UNKNOWN:
                        transition_complete = False
                    elif target is _Edge.UNDEFINED:
                        undefined = True
                    else:
                        targets.add(current_partition[target])
                quotient_transition = ResidualTransition(
                    residual_class.identifier,
                    action,
                    tuple(sorted(targets)),
                    transition_complete,
                    undefined,
                )
                quotient_transitions.append(quotient_transition)
                congruent = congruent and quotient_transition.well_defined

        quotient = ResidualQuotient(
            states=tuple(states),
            state_classes=current_partition,
            classes=classes,
            transitions=tuple(quotient_transitions),
            actions=actions,
            initial_state_classes=tuple(
                (name, current_partition[index])
                for name, index in initial_indices
            ),
        )

        def context_outcome(
            state_index: int, word: Sequence[str]
        ) -> Tuple[Any, ...]:
            defined, terminal = context_result(state_index, word)
            if not defined:
                return ("undefined",)
            return ("defined", observation_keys[terminal])

        context_basis = [()]
        context_refinements = []

        def partition_for_basis() -> Tuple[int, ...]:
            return _normalize_partition(
                tuple(
                    context_outcome(state_index, word)
                    for word in context_basis
                )
                for state_index in range(len(states))
            )

        basis_partition = partition_for_basis()
        while basis_partition != current_partition:
            if len(context_basis) >= max_context_tests:
                boundaries.append(
                    "distinguishing-context extraction reached "
                    "max_context_tests=%d" % max_context_tests
                )
                break
            counterexample_pair = None
            for left_state in range(len(states)):
                for right_state in range(left_state + 1, len(states)):
                    if (
                        basis_partition[left_state]
                        == basis_partition[right_state]
                        and current_partition[left_state]
                        != current_partition[right_state]
                    ):
                        counterexample_pair = (left_state, right_state)
                        break
                if counterexample_pair is not None:
                    break
            if counterexample_pair is None:
                break
            word = shortest_context(*counterexample_pair)
            if word is None or word in context_basis:
                boundaries.append(
                    "a finite distinguishing-context basis could not reproduce "
                    "the selected residual partition"
                )
                break
            previous_count = len(set(basis_partition))
            context_basis.append(word)
            refined_basis = partition_for_basis()
            if len(set(refined_basis)) <= previous_count:
                boundaries.append(
                    "a counterexample context did not strictly refine its test partition"
                )
                context_basis.pop()
                break
            basis_partition = refined_basis
            context_refinements.append(
                ResidualContextRefinement(
                    iteration=len(context_refinements) + 1,
                    left_state=counterexample_pair[0],
                    right_state=counterexample_pair[1],
                    context=word,
                    state_classes=basis_partition,
                )
            )
        context_basis_reproduces_partition = basis_partition == current_partition

        if complete and not congruent:
            boundaries.append(
                "the selected bounded residual partition is not a transition congruence"
            )
        if not complete:
            boundaries.append(
                "minimality is not certified because the reachable transition "
                "domain is incomplete"
            )

        model_digest = _residual_model_fingerprint(
            model, states, actions, transitions, initial_indices
        )
        protocol_digest = _residual_protocol_fingerprint(
            model_digest,
            context_digest,
            equivalence_digest,
            max_reachability_depth,
            max_states,
            max_context_depth,
            max_context_tests,
        )
        filtration = tuple(levels)
        distinguishing_contexts = tuple(distinctions)
        boundary_results = tuple(boundaries)
        context_basis_result = tuple(context_basis)
        context_refinement_results = tuple(context_refinements)
        exploration_depth = max(len(state.actions) for state in states)
        claim_digest = _residual_claim_fingerprint(
            protocol_digest,
            model.name,
            equivalence_signature,
            quotient,
            filtration,
            distinguishing_contexts,
            complete,
            stable,
            congruent,
            exploration_depth,
            transition_evaluations,
            boundary_results,
            context_basis_result,
            context_refinement_results,
            context_basis_reproduces_partition,
        )

        return ResidualQuotientReport(
            model_name=model.name,
            context_fingerprint=context_digest,
            model_fingerprint=model_digest,
            equivalence_fingerprint=equivalence_digest,
            protocol_fingerprint=protocol_digest,
            claim_fingerprint=claim_digest,
            max_reachability_depth=max_reachability_depth,
            max_states=max_states,
            max_context_depth=max_context_depth,
            max_context_tests=max_context_tests,
            equivalence_signature=equivalence_signature,
            quotient=quotient,
            filtration=filtration,
            distinguishing_contexts=distinguishing_contexts,
            complete=complete,
            stable=stable,
            congruent=congruent,
            exploration_depth=exploration_depth,
            transition_evaluations=transition_evaluations,
            boundaries=boundary_results,
            context_basis=context_basis_result,
            context_refinements=context_refinement_results,
            context_basis_reproduces_partition=(
                context_basis_reproduces_partition
            ),
        )
