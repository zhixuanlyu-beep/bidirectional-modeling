"""Typed, bounded existential queries over an explicit finite search problem.

A receipt is replayable evidence of this backend's result, not an independently
checkable SAT proof. Future solvers must preserve query scope and UNKNOWN.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, Tuple, Union

from .search import (ExperimentHypothesisSearch, SearchBudgetExceeded,
                     SearchObservation, SearchWork, SearchWorkBudget, _name)
from .structural import fingerprint_value


class QueryStatus(str, Enum):
    FOUND = 'found'
    ABSENT = 'absent'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class ConstraintQuery:
    commitments: Tuple[str, ...] = ()
    evidence: Tuple[SearchObservation, ...] = ()

    def __post_init__(self):
        object.__setattr__(self,'commitments',tuple(self.commitments))
        object.__setattr__(self,'evidence',tuple(self.evidence))
        for name in self.commitments:
            _name(name)


@dataclass(frozen=True)
class MacroAlternativeQuery:
    answer: str
    evidence: Tuple[SearchObservation, ...] = ()

    def __post_init__(self):
        _name(self.answer)
        object.__setattr__(self,'evidence',tuple(self.evidence))


@dataclass(frozen=True)
class LowerSubstituteQuery:
    candidate: str
    lower_names: Tuple[str, ...]

    def __post_init__(self):
        _name(self.candidate)
        object.__setattr__(self,'lower_names',tuple(self.lower_names))
        for name in self.lower_names:
            _name(name)
        if len(set(self.lower_names)) != len(self.lower_names):
            raise ValueError('duplicate lower-class candidate')


SearchQuery = Union[ConstraintQuery, MacroAlternativeQuery, LowerSubstituteQuery]


def query_fingerprint(problem, query):
    if isinstance(query,ConstraintQuery):
        signature=('constraints',query.commitments,
                   tuple((o.experiment,o.response,o.source) for o in query.evidence))
    elif isinstance(query,MacroAlternativeQuery):
        signature=('macro-alternative',query.answer,
                   tuple((o.experiment,o.response,o.source) for o in query.evidence))
    elif isinstance(query,LowerSubstituteQuery):
        signature=('lower-substitute',query.candidate,query.lower_names)
    else:
        raise TypeError('unsupported search query')
    return fingerprint_value(('search-query-v1',problem.fingerprint,signature))


@dataclass(frozen=True)
class QueryResult:
    status: QueryStatus
    query_fingerprint: str
    scope: str
    reason: str
    work: SearchWork
    witness_world: Optional[int] = None
    witness_candidate: Optional[str] = None
    # Only a macro-alternative query checks the compatible candidate catalogue.
    # None means unestablished, not empty. ABSENT alone cannot establish an answer.
    compatible_catalogue_nonempty: Optional[bool] = None


class SearchQueryBackend(Protocol):
    def execute(self, problem: ExperimentHypothesisSearch, query: SearchQuery, *,
                budget: Optional[SearchWorkBudget] = None) -> QueryResult:
        ...


def _query_scope(problem,query):
    if isinstance(query,ConstraintQuery):
        known={c.name for c in problem.protocol.constraints}
        if any(c not in known for c in query.commitments):
            raise ValueError('unknown commitment')
        return 'declared_response_universe'
    if isinstance(query,MacroAlternativeQuery):
        return 'declared_candidate_catalogue'
    if isinstance(query,LowerSubstituteQuery):
        known={h.name for h in problem.hypotheses}
        if query.candidate not in known or any(n not in known for n in query.lower_names):
            raise ValueError('unknown candidate')
        return 'declared_lower_catalogue'
    raise TypeError('unsupported search query')


def _world_matcher(problem, commitments, evidence, budget):
    """Prepare one-row checks; no response-universe traversal."""
    indices = {e.name: i for i, e in enumerate(problem.protocol.experiments)}
    constraints = {c.name: frozenset(c.worlds) for c in problem.protocol.constraints
                   if c.name in commitments}
    checks = tuple((indices[o.experiment], o.response) for o in evidence)

    def matches(world):
        for name in commitments:
            budget.consume('constraint_checks')
            if world not in constraints[name]:
                return False
        row = problem.protocol.worlds[world]
        for index, response in checks:
            budget.consume('response_checks')
            if row[index] != response:
                return False
        return True
    return matches


class FiniteSearchQueryBackend:
    """Use the problem's scan/indexed exact filter with shared work accounting."""

    def execute(self, problem, query, *, budget=None):
        return self._execute(problem, query, budget=budget)

    def _execute(self, problem, query, *, budget=None, force_scan=False):
        # Verification reads the original declarations without rebuilding the
        # validated problem or trusting a cached response index.
        budget=budget if budget is not None else SearchWorkBudget()
        digest=query_fingerprint(problem,query)
        nonempty=None
        scope=_query_scope(problem,query)

        def result(status,reason,world=None,candidate=None):
            return QueryResult(status,digest,scope,reason,budget.work,world,candidate,nonempty)

        try:
            budget.consume('query_checks')
            if isinstance(query,ConstraintQuery):
                evidence=problem._evidence(query.evidence,budget,use_index=not force_scan)
                if problem.backend == 'indexed' and not force_scan:
                    possible = problem._worlds(query.commitments, evidence, budget=budget)
                    world = next(iter(possible), None)
                    if world is not None:
                        budget.consume('candidate_checks')
                        return result(QueryStatus.FOUND, 'satisfying_response', world)
                else:
                    matches = _world_matcher(problem, query.commitments, evidence, budget)
                    for world in range(len(problem.protocol.worlds)):
                        budget.consume('candidate_checks')
                        if matches(world):
                            return result(QueryStatus.FOUND, 'satisfying_response', world)
                return result(QueryStatus.ABSENT,'finite_domain_exhausted')

            if isinstance(query,MacroAlternativeQuery):
                evidence=problem._evidence(query.evidence,budget,use_index=not force_scan)
                matches = _world_matcher(problem, (), evidence, budget)
                unknown=False
                for h in problem.hypotheses:
                    budget.consume('candidate_checks')
                    if h.world is None:
                        unknown=True
                        continue
                    if matches(h.world):
                        nonempty=True
                        if h.macro_answer != query.answer:
                            return result(QueryStatus.FOUND,'alternative_candidate',h.world,h.name)
                if unknown:
                    return result(QueryStatus.UNKNOWN,'unknown_prediction')
                nonempty=nonempty is True
                return result(QueryStatus.ABSENT,
                              'no_alternative' if nonempty else 'empty_version_space')

            if not query.lower_names:
                return result(QueryStatus.UNKNOWN,'empty_reference_class')
            by_name={h.name:h for h in problem.hypotheses}
            target=by_name[query.candidate]
            if target.world is None:
                return result(QueryStatus.UNKNOWN,'unknown_target_prediction')
            unknown=False
            for name in query.lower_names:
                budget.consume('candidate_checks')
                lower=by_name[name]
                if lower.world is None:
                    unknown=True
                elif lower.world == target.world:
                    return result(QueryStatus.FOUND,'equivalent_lower_candidate',lower.world,lower.name)
            return result(QueryStatus.UNKNOWN,'unknown_prediction') if unknown else result(
                QueryStatus.ABSENT,'finite_lower_class_exhausted')
        except SearchBudgetExceeded as error:
            return result(QueryStatus.UNKNOWN,error.reason)


@dataclass(frozen=True)
class QueryVerification:
    status: str  # valid / invalid / undecided
    reason: str
    work: SearchWork


def verify_query_result(problem, query, receipt, *, budget=None):
    """Recheck receipts using the scan oracle; never trust an external ABSENT flag.

    FOUND witnesses are checked directly, allowing a different valid witness.
    ABSENT is replayed exhaustively. UNKNOWN is not a positive proof claim.
    """
    budget=budget if budget is not None else SearchWorkBudget()
    def verdict(status,reason):
        return QueryVerification(status,reason,budget.work)
    expected_scope=_query_scope(problem,query)
    if (not isinstance(receipt, QueryResult)
            or not all(isinstance(value, str) for value in
                       (receipt.query_fingerprint, receipt.scope, receipt.reason))
            or not isinstance(receipt.work, SearchWork)
            or (receipt.witness_world is not None and type(receipt.witness_world) is not int)
            or (receipt.witness_candidate is not None and not isinstance(receipt.witness_candidate, str))
            or (receipt.compatible_catalogue_nonempty is not None
                and type(receipt.compatible_catalogue_nonempty) is not bool)):
        return verdict('invalid', 'malformed_receipt')
    if receipt.query_fingerprint != query_fingerprint(problem,query):
        return verdict('invalid','binding_mismatch')
    if not isinstance(receipt.status,QueryStatus):
        return verdict('invalid','invalid_status')
    if receipt.status is QueryStatus.UNKNOWN:
        return verdict('undecided','no_decisive_claim')
    if receipt.scope != expected_scope:
        return verdict('invalid','scope_mismatch')
    try:
        budget.consume('query_checks')
        if receipt.status is QueryStatus.FOUND:
            world=receipt.witness_world
            if type(world) is not int or world < 0 or world >= len(problem.protocol.worlds):
                return verdict('invalid','invalid_witness')
            known_experiments = {e.name for e in problem.protocol.experiments}
            if isinstance(query,ConstraintQuery):
                if receipt.witness_candidate is not None or receipt.compatible_catalogue_nonempty is not None:
                    return verdict('invalid','invalid_metadata')
                evidence=query.evidence
                if any(o.experiment not in known_experiments for o in evidence):
                    return verdict('invalid', 'witness_violates_query')
                if not _world_matcher(problem, query.commitments, evidence, budget)(world):
                    return verdict('invalid','witness_violates_query')
            else:
                candidates={h.name:h for h in problem.hypotheses}
                h=candidates.get(receipt.witness_candidate)
                budget.consume('candidate_checks')
                if h is None or h.world != world:
                    return verdict('invalid','invalid_witness')
                if isinstance(query,MacroAlternativeQuery):
                    evidence=query.evidence
                    if any(o.experiment not in known_experiments for o in evidence):
                        return verdict('invalid', 'witness_violates_query')
                    if (receipt.compatible_catalogue_nonempty is not True or h.macro_answer == query.answer
                            or not _world_matcher(problem, (), evidence, budget)(world)):
                        return verdict('invalid','witness_violates_query')
                else:
                    if (receipt.compatible_catalogue_nonempty is not None
                            or h.name not in query.lower_names
                            or candidates[query.candidate].world != world):
                        return verdict('invalid','witness_violates_query')
            return verdict('valid','witness_checked')
        if receipt.witness_world is not None or receipt.witness_candidate is not None:
            return verdict('invalid','absent_with_witness')
        replay=FiniteSearchQueryBackend()._execute(problem,query,budget=budget,force_scan=True)
        if replay.status is QueryStatus.UNKNOWN:
            return verdict('undecided',replay.reason)
        if (replay.status is not QueryStatus.ABSENT
                or replay.compatible_catalogue_nonempty is not receipt.compatible_catalogue_nonempty):
            return verdict('invalid','replay_disagrees')
        return verdict('valid','absence_replayed')
    except SearchBudgetExceeded as error:
        return verdict('undecided',error.reason)

