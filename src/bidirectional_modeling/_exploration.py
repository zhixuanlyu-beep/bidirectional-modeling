"""Shared bounded, audited reachability substrate; no closure/quotient policy."""
from collections import deque
from dataclasses import dataclass
from enum import Enum

from .core import UndefinedTransition
from .structural import freeze_value, isolated_mapping


class Edge(Enum):
    UNDEFINED = 'undefined'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class ExploredState:
    index: int
    source_initial_state: str
    actions: tuple
    micro_state: object
    observation: object = None
    observation_signature: object = None


@dataclass(frozen=True)
class Exploration:
    states: tuple
    initial_indices: tuple
    actions: tuple
    transitions: dict
    errors: tuple
    state_limit_hit: bool
    depth_limit_hit: bool
    transition_evaluations: int

    @property
    def complete(self):
        return (bool(self.states) and not self.errors and not self.state_limit_hit
                and not self.depth_limit_hit
                and len(self.transitions) == len(self.states)*len(self.actions)
                and all(edge is not Edge.UNKNOWN for edge in self.transitions.values()))


def explore_reachable(model, context, *, max_depth, max_states,
                      observe=None, raise_initial_errors=False):
    """BFS over isolated canonical states, including noop and explicit partiality.

    observe, when supplied, returns (observation, signature) before a new state
    is published. Initial error policy is explicit for legacy analyzer behavior.
    Analysis-specific fingerprints, equivalence and counterexamples stay outside.
    """
    if type(max_states) is not int or max_states < 1:
        raise ValueError('max_states must be positive')
    if max_depth is not None and (type(max_depth) is not int or max_depth < 0):
        raise ValueError('max_depth must be nonnegative or None')
    states, indices, initial, errors = [], {}, [], []
    frontier, transitions = deque(), {}
    actions = tuple(dict.fromkeys(('noop',) + tuple(model.actions)))
    state_limit = depth_limit = False
    evaluations = 0

    def key(state):
        return freeze_value(dict(state), purpose='reachable state deterministic structural identity')

    def add(state, origin, path):
        nonlocal state_limit
        digest = key(state)
        if digest in indices:
            return indices[digest]
        if len(states) >= max_states:
            state_limit = True
            return None
        isolated = isolated_mapping(state, purpose='reachable state')
        observation, signature = (None, None) if observe is None else observe(isolated)
        index = len(states)
        states.append(ExploredState(index, origin, path, isolated, observation, signature))
        indices[digest] = index
        frontier.append(index)
        return index

    for name in model.initial_states:
        try:
            index = add(model.states[name], name, ())
            if index is None:
                break
            initial.append((name, index))
        except Exception as error:
            if raise_initial_errors:
                raise
            errors.append(('initial-state identity', error, {'initial_state': name}))

    while frontier:
        index = frontier.popleft()
        state = states[index]
        if max_depth is not None and len(state.actions) >= max_depth:
            depth_limit = True
            for action in actions:
                transitions[index, action] = Edge.UNKNOWN
            continue
        for action in actions:
            evaluations += 1
            try:
                successor = model.audited_step(state.micro_state, action, context)
                target = add(successor, state.source_initial_state, state.actions+(action,))
                transitions[index, action] = Edge.UNKNOWN if target is None else target
            except UndefinedTransition:
                transitions[index, action] = Edge.UNDEFINED
            except Exception as error:
                transitions[index, action] = Edge.UNKNOWN
                errors.append(('reachable transition', error, {'source_state': index, 'action': action}))
    return Exploration(tuple(states), tuple(initial), actions, transitions, tuple(errors),
                       state_limit, depth_limit, evaluations)
