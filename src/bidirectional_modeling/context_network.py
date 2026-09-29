"""Finite local contexts and explicit, non-transitive context changes.

Response translations run from target labels back to source labels. They are
finite declarations to check, not permission to reuse a source certificate.
"""
from dataclasses import asdict, dataclass
from enum import Enum
from types import MappingProxyType

from .search import SearchBudgetExceeded, SearchProtocol, SearchWorkBudget, _name
from .structural import fingerprint_value


class ContextChange(str, Enum):
    RESTRICTION = 'restriction'
    REFINEMENT = 'refinement'
    EXTENSION = 'extension'
    RECONSTRUCTION = 'reconstruction'


@dataclass(frozen=True)
class ModelingContext:
    name: str
    protocol: SearchProtocol
    observation_scope: str
    resolution: str
    objects: tuple
    language: str
    target: str

    def __post_init__(self):
        for value in (self.name, self.observation_scope, self.resolution, self.language, self.target):
            _name(value)
        if not isinstance(self.protocol, SearchProtocol):
            raise TypeError('context requires a search protocol')
        object.__setattr__(self, 'objects', tuple(self.objects))
        for value in self.objects:
            _name(value)
        if len(set(self.objects)) != len(self.objects):
            raise ValueError('duplicate object identifier')

    @property
    def fingerprint(self):
        return fingerprint_value(('modeling-context-v1', asdict(self)))


@dataclass(frozen=True)
class ContextTransition:
    source: ModelingContext
    target: ModelingContext
    kind: ContextChange
    # (source experiment, target experiment)
    experiments: tuple = ()
    # (source experiment, target response, source response)
    responses: tuple = ()
    # (source commitment, target commitment)
    commitments: tuple = ()

    def __post_init__(self):
        if not isinstance(self.kind, ContextChange):
            raise TypeError('use ContextChange')
        for field, size in (('experiments', 2), ('responses', 3), ('commitments', 2)):
            rows = tuple(tuple(row) for row in getattr(self, field))
            if any(len(row) != size for row in rows):
                raise ValueError('malformed context translation')
            for row in rows:
                for value in row:
                    _name(value)
            keys = tuple(row[:-1] for row in rows)
            if len(set(keys)) != len(keys):
                raise ValueError('ambiguous context translation')
            object.__setattr__(self, field, rows)
        old = {e.name for e in self.source.protocol.experiments}
        new = {e.name for e in self.target.protocol.experiments}
        if any(a not in old or b not in new for a, b in self.experiments):
            raise ValueError('unknown mapped experiment')
        if len({b for _, b in self.experiments}) != len(self.experiments):
            raise ValueError('experiment translation must be injective')
        if any(a not in dict(self.experiments) for a, _, _ in self.responses):
            raise ValueError('response translation needs a mapped experiment')
        old_constraints = {c.name for c in self.source.protocol.constraints}
        new_constraints = {c.name for c in self.target.protocol.constraints}
        if any(a not in old_constraints or b not in new_constraints for a, b in self.commitments):
            raise ValueError('unknown mapped commitment')

    @property
    def fingerprint(self):
        return fingerprint_value(('context-transition-v1', self.source.fingerprint,
            self.target.fingerprint, self.kind.value, self.experiments, self.responses, self.commitments))

    def translate_response(self, experiment, value):
        translations = {(a, b): c for a, b, c in self.responses}
        # Identity is explicit too: missing translations never guess semantics.
        if (experiment, value) not in translations:
            raise ValueError('unmapped target response')
        return translations[experiment, value]



@dataclass(frozen=True)
class ContextTransitionReport:
    transition_fingerprint: str
    status: str
    reason: str
    # Counts concern declared response worlds, not structural candidates.
    split_source_worlds: int = 0
    unrepresented_source_worlds: int = 0
    unmatched_target_worlds: int = 0
    # Properties actually checked; kind is the caller's proposed classification.
    checked_properties: tuple = ()


def _prepare_context_transition(transition, *, budget=None):
    budget = budget if budget is not None else SearchWorkBudget()
    relation = []
    def result(status, reason, *counts, properties=()):
        return ContextTransitionReport(transition.fingerprint, status, reason,
                                       *counts, checked_properties=properties), tuple(relation)
    source_names = {e.name for e in transition.source.protocol.experiments}
    target_names = {e.name for e in transition.target.protocol.experiments}
    mapped_source = {a for a, _ in transition.experiments}
    mapped_target = {b for _, b in transition.experiments}
    if transition.kind in (ContextChange.REFINEMENT, ContextChange.EXTENSION) and mapped_source != source_names:
        return result('invalid', 'source_experiments_not_covered')
    if transition.kind is ContextChange.RESTRICTION and mapped_target != target_names:
        return result('invalid', 'target_experiments_not_covered')
    if not transition.experiments:
        return result('undecided', 'no_shared_experiment_mapping')
    counts = [0] * len(transition.source.protocol.worlds)
    unmatched = 0
    try:
        old_names = {e.name: i for i, e in enumerate(transition.source.protocol.experiments)}
        new_names = {e.name: i for i, e in enumerate(transition.target.protocol.experiments)}
        columns = tuple((old_names[a], new_names[b], a) for a, b in transition.experiments)
        translations = {(a, b): c for a, b, c in transition.responses}
        source_index = {}
        for i, row in enumerate(transition.source.protocol.worlds):
            budget.consume('index_entries')
            key = tuple(row[old] for old, _, _ in columns)
            source_index.setdefault(key, []).append(i)
        for row in transition.target.protocol.worlds:
            budget.consume('index_operations')
            translated = []
            for _, new, name in columns:
                budget.consume('response_checks')
                if (name, row[new]) not in translations:
                    raise ValueError('unmapped target response')
                translated.append(translations[name, row[new]])
            matches = tuple(source_index.get(tuple(translated), ()))
            unmatched += not bool(matches)
            for i in matches:
                budget.consume('response_checks')
                counts[i] += 1
            relation.append(matches)
        if transition.kind is not ContextChange.RECONSTRUCTION and unmatched:
            return result('invalid', 'target_behavior_not_represented')
        if transition.kind is ContextChange.RESTRICTION and any(n == 0 for n in counts):
            return result('invalid', 'restriction_loses_source_behavior')
        # Without complete source coordinates, a match need not be a refinement.
        split = sum(n > 1 for n in counts) if mapped_source == source_names else 0
        properties = ['mapped_responses_checked']
        if mapped_source == source_names:
            properties.append('source_experiments_covered')
        if mapped_target == target_names:
            properties.append('target_experiments_covered')
        if not unmatched:
            properties.append('target_worlds_represented')
        if all(counts):
            properties.append('source_worlds_represented')
        return result('valid', 'finite_response_relation_checked', split,
                      sum(n == 0 for n in counts), unmatched, properties=tuple(properties))
    except SearchBudgetExceeded as error:
        return result('undecided', error.reason)
    except ValueError as error:
        return result('invalid', str(error))


def validate_context_transition(transition, *, budget=None):
    return _prepare_context_transition(transition, budget=budget)[0]


class ContextNetwork:
    """Local contexts and checked direct changes; no global universe is assumed."""
    def __init__(self):
        self._contexts = {}
        self._transitions = {}

    @property
    def contexts(self):
        return MappingProxyType(dict(self._contexts))

    @property
    def transitions(self):
        return MappingProxyType(dict(self._transitions))

    def add_context(self, context):
        old = self._contexts.get(context.name)
        if old is not None and old.fingerprint != context.fingerprint:
            raise ValueError('changed context needs a new versioned name')
        self._contexts[context.name] = context

    def add_transition(self, transition, *, budget=None):
        report = validate_context_transition(transition, budget=budget)
        if report.status != 'valid':
            return report
        # Validate both names before publishing either node.
        for context in (transition.source, transition.target):
            old = self._contexts.get(context.name)
            if old is not None and old.fingerprint != context.fingerprint:
                raise ValueError('changed context needs a new versioned name')
        if transition.source.name == transition.target.name and transition.source != transition.target:
            raise ValueError('changed context needs a new versioned name')
        self.add_context(transition.source)
        self.add_context(transition.target)
        self._transitions[transition.fingerprint] = (transition, report)
        return report
