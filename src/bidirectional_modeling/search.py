"""Finite experiment-relative hypothesis search and replayable evidence bases.

The caller owns the experiment protocol, finite response universe, constraint
semantics and candidate catalogue. This is an exact, discrete reference backend,
not an open-world theorem prover or a statistical rejection procedure.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from itertools import combinations
from fractions import Fraction
from functools import cached_property
from typing import Callable, Optional, Tuple

from .structural import fingerprint_value


def _name(value: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError("semantic identifiers and response labels must be nonempty strings")


def _natural(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("costs and budgets must be nonnegative integers")


@dataclass(frozen=True)
class SearchWork:
    """Cumulative semantic operations; excludes setup, hashing, sorting and I/O."""
    index_entries: int = 0
    index_operations: int = 0
    world_queries: int = 0
    response_checks: int = 0
    constraint_checks: int = 0
    candidate_checks: int = 0
    certificate_checks: int = 0
    partition_checks: int = 0
    pair_checks: int = 0
    subset_checks: int = 0
    query_checks: int = 0

    @property
    def total(self) -> int:
        return sum(getattr(self, f.name) for f in fields(self))


class SearchBudgetExceeded(RuntimeError):
    """An interrupted operation is undecided, never a falsification."""
    def __init__(self, reason: str, work: SearchWork) -> None:
        super().__init__(reason)
        self.reason = reason
        self.work = work


class SearchWorkBudget:
    """Share one cumulative, cooperative budget across search/validation calls.

    Cancellation is checked before each semantic operation. This is not a wall
    clock, allocation or constructor limit. Once interrupted, a budget stays
    stopped; create a new budget to resume by replaying the operation.
    """
    def __init__(self, max_operations: int = 1_000_000,
                 cancelled: Optional[Callable[[], bool]] = None) -> None:
        _natural(max_operations)
        if cancelled is not None and not callable(cancelled):
            raise TypeError("cancelled must be a callable")
        self.max_operations = max_operations
        self.cancelled = cancelled
        self._counts = {f.name: 0 for f in fields(SearchWork)}
        self._reason = None
        self._total = 0

    @property
    def work(self) -> SearchWork:
        return SearchWork(**self._counts)

    def consume(self, category: str) -> None:
        if category not in self._counts:
            raise ValueError("unknown work category")
        if self._reason is None:
            if self.cancelled is not None and self.cancelled():
                self._reason = "cancelled"
            elif self._total >= self.max_operations:
                self._reason = "work_budget_exhausted"
        if self._reason is not None:
            raise SearchBudgetExceeded(self._reason, self.work)
        self._counts[category] += 1
        self._total += 1


@dataclass(frozen=True)
class MacroValidationReport:
    sufficiency: str
    minimality: str
    stop_reason: str
    subsets_checked: int
    work: SearchWork

    @property
    def valid(self) -> bool:
        return self.sufficiency == "valid" and self.minimality in ("valid", "not_claimed")


@dataclass(frozen=True)
class DescriptionLength:
    """All components use the protocol's shared coding convention; none are free."""
    framework: int = 0
    concepts: int = 0
    relations: int = 0
    parameters: int = 0
    observation_map: int = 0
    assumptions: int = 0

    def __post_init__(self) -> None:
        for field in fields(self):
            _natural(getattr(self, field.name))

    @property
    def total(self) -> int:
        return sum(getattr(self, field.name) for field in fields(self))


@dataclass(frozen=True)
class SearchExperiment:
    name: str
    semantics: str
    cost: int = 1

    def __post_init__(self) -> None:
        _name(self.name)
        _name(self.semantics)
        _natural(self.cost)
        if not self.cost:
            raise ValueError("experiment cost must be positive")


@dataclass(frozen=True)
class ResponseConstraint:
    """Extensional semantics: indices of all protocol worlds satisfying a rule."""
    name: str
    worlds: Tuple[int, ...]

    def __post_init__(self) -> None:
        _name(self.name)
        object.__setattr__(self, "worlds", tuple(self.worlds))
        for index in self.worlds:
            _natural(index)
        if len(set(self.worlds)) != len(self.worlds):
            raise ValueError("duplicate constraint world")


@dataclass(frozen=True)
class SearchProtocol:
    scope: str
    coding: str
    experiments: Tuple[SearchExperiment, ...]
    worlds: Tuple[Tuple[str, ...], ...]
    constraints: Tuple[ResponseConstraint, ...] = ()

    def __post_init__(self) -> None:
        _name(self.scope)
        _name(self.coding)
        object.__setattr__(self, "experiments", tuple(self.experiments))
        object.__setattr__(self, "worlds", tuple(tuple(w) for w in self.worlds))
        object.__setattr__(self, "constraints", tuple(self.constraints))
        if not self.experiments or not self.worlds:
            raise ValueError("protocol needs a nonempty experiment domain and response universe")
        for items in (self.experiments, self.constraints):
            if len({item.name for item in items}) != len(items):
                raise ValueError("duplicate semantic identifier")
        if len(set(self.worlds)) != len(self.worlds):
            raise ValueError("duplicate response world")
        for world in self.worlds:
            if len(world) != len(self.experiments):
                raise ValueError("world must specify every allowed experiment")
            for response in world:
                _name(response)
        for constraint in self.constraints:
            if any(i >= len(self.worlds) for i in constraint.worlds):
                raise ValueError("constraint references an unknown world")

    @cached_property
    def fingerprint(self) -> str:
        return fingerprint_value((
            "search-protocol-v1", self.scope, self.coding,
            tuple((e.name, e.semantics, e.cost) for e in self.experiments),
            self.worlds, tuple((c.name, c.worlds) for c in self.constraints),
        ))


@dataclass(frozen=True)
class SearchObservation:
    experiment: str
    response: str
    source: str

    def __post_init__(self) -> None:
        for value in (self.experiment, self.response, self.source):
            _name(value)


@dataclass(frozen=True)
class SearchHypothesis:
    name: str
    world: Optional[int]
    macro_answer: str
    description: DescriptionLength
    commitments: Tuple[str, ...] = ()
    materials: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _name(self.name)
        _name(self.macro_answer)
        if self.world is not None:
            _natural(self.world)
        for field in ("commitments", "materials"):
            values = tuple(getattr(self, field))
            for value in values:
                _name(value)
            object.__setattr__(self, field, values)


@dataclass(frozen=True)
class ConflictCertificate:
    protocol_fingerprint: str
    commitments: Tuple[str, ...]
    evidence: Tuple[SearchObservation, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "commitments", tuple(self.commitments))
        object.__setattr__(self, "evidence", tuple(self.evidence))


@dataclass(frozen=True)
class MacroEvidenceCertificate:
    problem_fingerprint: str
    full_evidence: Tuple[SearchObservation, ...]
    retained_evidence: Tuple[SearchObservation, ...]
    answers: Tuple[str, ...]
    determined: bool
    minimum_cardinality: bool
    subsets_checked: int


@dataclass(frozen=True)
class HypothesisSearchReport:
    compatible: Tuple[str, ...]
    pruned: Tuple[str, ...]
    rejected: Tuple[str, ...]
    undecided: Tuple[str, ...]
    quotient: Tuple[Tuple[str, ...], ...]
    answers: Tuple[str, ...]
    determined: bool
    conflicts: Tuple[ConflictCertificate, ...]
    replay_checks: int
    surviving_quotient: Tuple[Tuple[str, ...], ...] = ()
    observed_quotient: Tuple[Tuple[str, ...], ...] = ()
    partition_complete: bool = False
    experiment_domain: Tuple[str, ...] = ()
    observed_experiments: Tuple[str, ...] = ()
    undecided_reasons: Tuple[Tuple[str, str], ...] = ()
    stop_reason: str = "completed"
    work: SearchWork = SearchWork()

    @property
    def full_quotient(self) -> Tuple[Tuple[str, ...], ...]:
        """Explicit name for the backward-compatible full-catalogue quotient."""
        return self.quotient


class ExperimentHypothesisSearch:
    """Search an explicit catalogue, keeping every structural representative.

    ``world=None`` means unavailable/unknown prediction, not a contradiction.
    Reconstructed candidates are supplied by the domain adapter; parentage and
    shared materials never imply inheritance of logical commitments.
    """

    __slots__ = ("_protocol", "_hypotheses", "_target", "_backend", "_world_answers", "_response_index", "_fingerprint")

    @property
    def protocol(self):
        return self._protocol

    @property
    def hypotheses(self):
        return self._hypotheses

    @property
    def target(self):
        return self._target

    @property
    def backend(self):
        return self._backend

    @property
    def world_answers(self):
        """Authoritative target mapping, or None for a legacy labelled catalogue."""
        return self._world_answers

    def with_hypotheses(self, hypotheses):
        updated = ExperimentHypothesisSearch(self.protocol, hypotheses, self.target,
                    backend=self.backend, world_answers=self.world_answers)
        # The index depends only on this exact immutable protocol, never on candidates.
        updated._response_index = self._response_index
        return updated

    def __init__(self, protocol: SearchProtocol,
                 hypotheses: Tuple[SearchHypothesis, ...], target: str, *, backend="scan",
                 world_answers=None) -> None:
        _name(target)
        if backend not in ("scan", "indexed"):
            raise ValueError("backend must be scan or indexed")
        self._backend = backend
        self._response_index = None
        self._fingerprint = None
        self._protocol = protocol
        self._hypotheses = tuple(hypotheses)
        self._target = target
        self._world_answers = None if world_answers is None else tuple(world_answers)
        if self.world_answers is not None:
            if len(self.world_answers) != len(protocol.worlds):
                raise ValueError("target mapping must cover the response universe")
            for answer in self.world_answers:
                _name(answer)
        if len({h.name for h in self.hypotheses}) != len(self.hypotheses):
            raise ValueError("duplicate hypothesis name")
        constraints = {c.name: set(c.worlds) for c in protocol.constraints}
        for h in self.hypotheses:
            if not isinstance(h.description, DescriptionLength):
                raise TypeError("hypothesis needs a complete DescriptionLength")
            if any(c not in constraints for c in h.commitments):
                raise ValueError("unknown commitment")
            if h.world is not None:
                if h.world >= len(protocol.worlds):
                    raise ValueError("hypothesis references an unknown world")
                if self.world_answers is not None and h.macro_answer != self.world_answers[h.world]:
                    raise ValueError("macro answer violates the authoritative target mapping")
                if any(h.world not in constraints[c] for c in h.commitments):
                    raise ValueError("prediction violates a declared commitment")

    @property
    def fingerprint(self) -> str:
        if self._fingerprint is not None:
            return self._fingerprint
        legacy = (
            "search-problem-v1", self.protocol.fingerprint, self.target,
            tuple((h.name, h.world, h.macro_answer,
                   tuple(getattr(h.description, f.name) for f in fields(h.description)),
                   h.commitments, h.materials) for h in self.hypotheses),
        )
        self._fingerprint = fingerprint_value(legacy if self.world_answers is None else
                                 ("search-problem-v2", legacy, self.world_answers))
        return self._fingerprint

    def query(self, request, *, budget=None):
        """Run a typed existential query with explicit FOUND/ABSENT/UNKNOWN."""
        from .search_queries import FiniteSearchQueryBackend
        return FiniteSearchQueryBackend().execute(self, request, budget=budget)

    def _index(self, budget):
        from .search_index import ResponseIndex
        if self._response_index is None or self._response_index.protocol is not self.protocol:
            self._response_index = ResponseIndex(self.protocol, budget)
        return self._response_index

    def _evidence(self, evidence, budget):
        evidence = tuple(evidence)
        names = {e.name: i for i, e in enumerate(self.protocol.experiments)}
        for observation in evidence:
            if observation.experiment not in names:
                raise ValueError("evidence lies outside the allowed experiment domain")
            if self.backend == "indexed":
                if not self._index(budget).allows(observation,budget):
                    raise ValueError("observed response lies outside the declared universe")
                continue
            index = names[observation.experiment]
            for world in self.protocol.worlds:
                budget.consume("response_checks")
                if world[index] == observation.response:
                    break
            else:
                raise ValueError("observed response lies outside the declared universe")
        return evidence

    def _worlds(self, commitments=(), evidence=(), *, budget):
        budget.consume("world_queries")
        if self.backend == "indexed":
            return self._index(budget).filter(commitments,evidence,budget)
        possible = set(range(len(self.protocol.worlds)))
        constraints = {c.name: set(c.worlds) for c in self.protocol.constraints}
        for name in commitments:
            retained = set()
            for i in sorted(possible):
                budget.consume("constraint_checks")
                if i in constraints[name]:
                    retained.add(i)
            possible = retained
        indices = {e.name: i for i, e in enumerate(self.protocol.experiments)}
        for observation in evidence:
            retained = set()
            for i in sorted(possible):
                budget.consume("response_checks")
                if self.protocol.worlds[i][indices[observation.experiment]] == observation.response:
                    retained.add(i)
            possible = retained
        return possible

    def learn_conflict(self, commitments, evidence, *, budget=None) -> Optional[ConflictCertificate]:
        """Extract an inclusion-minimal joint core, not blame for each member.

        Proof is exhaustive intersection in the caller-declared response universe.
        Data inconsistent on their own cannot certify a model conflict.
        """
        budget = budget if budget is not None else SearchWorkBudget()
        budget.consume("certificate_checks")
        evidence = self._evidence(evidence, budget)
        core = tuple(dict.fromkeys(commitments))
        known = {c.name for c in self.protocol.constraints}
        if any(c not in known for c in core):
            raise ValueError("unknown commitment")
        if (not core or not self._worlds(evidence=evidence, budget=budget)
                or not self._worlds(core, budget=budget) or self._worlds(core, evidence, budget=budget)):
            return None
        for name in tuple(core):
            trial = tuple(c for c in core if c != name)
            if not self._worlds(trial, evidence, budget=budget):
                core = trial
        support = evidence
        for observation in evidence:
            trial = tuple(o for o in support if o != observation)
            if not self._worlds(core, trial, budget=budget):
                support = trial
        return ConflictCertificate(self.protocol.fingerprint, core, support)

    def validates_conflict(self, certificate: ConflictCertificate, evidence, *, budget=None) -> bool:
        """Recheck binding, live evidence dependencies, and the actual contradiction."""
        budget = budget if budget is not None else SearchWorkBudget()
        budget.consume("certificate_checks")
        evidence = self._evidence(evidence, budget)
        if certificate.protocol_fingerprint != self.protocol.fingerprint:
            return False
        if not set(certificate.evidence).issubset(evidence):
            return False
        if not certificate.commitments or any(
            c not in {rule.name for rule in self.protocol.constraints}
            for c in certificate.commitments
        ):
            return False
        return bool(self._worlds(evidence=certificate.evidence, budget=budget)
                    and self._worlds(certificate.commitments, budget=budget)
                    and not self._worlds(certificate.commitments, certificate.evidence, budget=budget))

    def partition(self, experiments=None, *, budget=None) -> Tuple[Tuple[str, ...], ...]:
        """Full E gives theoretical equivalence; a subset gives provisional groups.

        Unknown predictions are separate unresolved representatives. These groups
        are an evaluation view and must never replace the structural catalogue.
        """
        budget = budget if budget is not None else SearchWorkBudget()
        names = tuple(e.name for e in self.protocol.experiments)
        selected = names if experiments is None else tuple(experiments)
        if any(name not in names for name in selected):
            raise ValueError("unknown experiment")
        indices = tuple(names.index(name) for name in selected)
        groups = {}
        for h in sorted(self.hypotheses, key=lambda h: (h.description.total, h.name)):
            budget.consume("candidate_checks")
            values = []
            if h.world is not None:
                for i in indices:
                    budget.consume("partition_checks")
                    values.append(self.protocol.worlds[h.world][i])
            signature = (("unknown", h.name) if h.world is None else ("known", tuple(values)))
            groups.setdefault(signature, []).append(h.name)
        return tuple(tuple(group) for group in groups.values())

    def macro_identifiable(self, *, budget=None) -> bool:
        budget = budget if budget is not None else SearchWorkBudget()
        by_name = {h.name: h for h in self.hypotheses}
        if not by_name:
            return False
        for h in self.hypotheses:
            budget.consume("candidate_checks")
            if h.world is None:
                return False
        for group in self.partition(budget=budget):
            answers = set()
            for name in group:
                budget.consume("candidate_checks")
                answers.add(by_name[name].macro_answer)
            if len(answers) != 1:
                return False
        return True

    def irreducible_against(self, hypothesis: str, lower_names, *, budget=None) -> Optional[bool]:
        """Whether no representative in the declared finite lower class substitutes it.

        The adapter defines the allowed lower-order language, not variable count.
        None means an empty reference class or unknown predictions. True is only
        relative to this explicit class, never all imaginable lower-order models.
        """
        budget = budget if budget is not None else SearchWorkBudget()
        by_name = {h.name: h for h in self.hypotheses}
        lower_names = tuple(lower_names)
        if hypothesis not in by_name or any(n not in by_name for n in lower_names):
            raise ValueError("unknown hypothesis")
        candidate = by_name[hypothesis]
        if candidate.world is None or not lower_names:
            return None
        unknown = False
        for name in lower_names:
            budget.consume("candidate_checks")
            h = by_name[name]
            if h.world == candidate.world:
                return False
            unknown = unknown or h.world is None
        return None if unknown else True

    def search(self, evidence=(), certificates=(), *, max_replays=None,
               budget=None, learn_conflicts=True) -> HypothesisSearchReport:
        budget = budget if budget is not None else SearchWorkBudget()
        if max_replays is not None:
            _natural(max_replays)
        evidence = tuple(evidence)
        compatible, pruned, rejected = [], [], []
        reasons = {}
        active = []
        replay_checks = 0
        full, observed = (), ()
        partition_complete = False
        stopped = None
        inconsistent = False
        domain = tuple(e.name for e in self.protocol.experiments)
        measured = tuple(name for name in domain if any(o.experiment == name for o in evidence))
        try:
            evidence = self._evidence(evidence, budget)
            full = self.partition(budget=budget)
            observed = self.partition(measured, budget=budget)
            partition_complete = True
            for c in certificates:
                if self.validates_conflict(c, evidence, budget=budget):
                    active.append(c)
            possible = self._worlds(evidence=evidence, budget=budget)
            inconsistent = not possible
            for h in sorted(self.hypotheses, key=lambda h: (h.description.total, h.name)):
                budget.consume("candidate_checks")
                inherited = False
                for c in active:
                    budget.consume("certificate_checks")
                    if set(c.commitments).issubset(h.commitments):
                        inherited = True
                        break
                if inherited:
                    pruned.append(h.name)
                elif h.world is None:
                    reasons[h.name] = "unknown_prediction"
                elif max_replays is not None and replay_checks >= max_replays:
                    reasons[h.name] = "replay_budget_exhausted"
                else:
                    replay_checks += 1
                    if h.world in possible:
                        compatible.append(h.name)
                    else:
                        rejected.append(h.name)
                        # If learning is interrupted the completed rejection remains valid.
                        if learn_conflicts:
                            c = self.learn_conflict(h.commitments, evidence, budget=budget)
                            if c is not None and c not in active:
                                active.append(c)
        except SearchBudgetExceeded as error:
            stopped = error.reason
        classified = set(compatible + pruned + rejected)
        ordered = sorted(self.hypotheses, key=lambda h: (h.description.total, h.name))
        undecided = tuple(h.name for h in ordered if h.name not in classified)
        for name in undecided:
            reasons.setdefault(name, stopped or "unknown_prediction")
        survivors = set(compatible) | set(undecided)
        def surviving(groups):
            return tuple(tuple(n for n in group if n in survivors)
                         for group in groups if any(n in survivors for n in group))
        answers = tuple(sorted({h.macro_answer for h in self.hypotheses if h.name in survivors}))
        determined = bool(compatible) and not undecided and len(answers) == 1
        stop_reason = stopped or (
            "replay_budget_exhausted" if "replay_budget_exhausted" in reasons.values() else
            "inconsistent_evidence" if inconsistent else
            "unknown_predictions" if undecided else
            "determined" if determined else
            "no_compatible_candidate" if not compatible else "macro_ambiguous"
        )
        return HypothesisSearchReport(
            tuple(compatible), tuple(pruned), tuple(rejected), undecided,
            full, answers, determined, tuple(active), replay_checks,
            surviving(full), surviving(observed), partition_complete, domain, measured,
            tuple((n, reasons[n]) for n in undecided), stop_reason, budget.work,
        )

    def _answers(self, evidence, budget):
        worlds = self._worlds(evidence=evidence, budget=budget)
        answers = set()
        for h in self.hypotheses:
            budget.consume("candidate_checks")
            if h.world in worlds:
                answers.add(h.macro_answer)
        return tuple(sorted(answers))

    def compress_evidence(self, evidence, *, max_subsets=10000, budget=None) -> MacroEvidenceCertificate:
        """Exact cardinality search with a hard subset-count budget.

        Every subset is evaluated against the ORIGINAL catalogue, with no free
        pruning cache. Budget exhaustion returns the full valid evidence and
        minimum_cardinality=False, never a false optimality claim.
        """
        _natural(max_subsets)
        budget = budget if budget is not None else SearchWorkBudget()
        evidence = self._evidence(evidence, budget)
        if any(h.world is None for h in self.hypotheses):
            raise ValueError("unknown predictions prevent an exact evidence certificate")
        answers = self._answers(evidence, budget)
        if not answers:
            raise ValueError("empty version space cannot certify a macro answer")
        checked = 0
        for size in range(len(evidence) + 1):
            for subset in combinations(evidence, size):
                if checked >= max_subsets:
                    return MacroEvidenceCertificate(self.fingerprint, evidence, evidence,
                                                    answers, len(answers) == 1, False, checked)
                budget.consume("subset_checks")
                checked += 1
                if self._answers(subset, budget) == answers:
                    return MacroEvidenceCertificate(self.fingerprint, evidence, subset,
                                                    answers, len(answers) == 1, True, checked)
        raise AssertionError("full evidence must preserve its own answers")

    def verify_macro(self, certificate: MacroEvidenceCertificate, evidence, *,
                     check_minimality=True, max_subsets=10000, budget=None) -> MacroValidationReport:
        """Verify sufficiency first, then optionally verify a minimality claim.

        Exhaustion preserves a proved sufficiency result. Neither skipping nor
        exhausting the minimality check verifies a claimed optimum.
        """
        _natural(max_subsets)
        budget = budget if budget is not None else SearchWorkBudget()
        sufficient, minimality, checked = "undecided", "not_checked", 0
        try:
            budget.consume("certificate_checks")
            evidence = self._evidence(evidence, budget)
            if (certificate.problem_fingerprint != self.fingerprint
                    or certificate.full_evidence != evidence
                    or not set(certificate.retained_evidence).issubset(evidence)
                    or any(h.world is None for h in self.hypotheses)):
                return MacroValidationReport("invalid", minimality, "binding_mismatch", checked, budget.work)
            answers = self._answers(evidence, budget)
            if (not answers or certificate.answers != answers
                    or self._answers(certificate.retained_evidence, budget) != answers
                    or certificate.determined != (len(answers) == 1)):
                return MacroValidationReport("invalid", minimality, "insufficient_evidence", checked, budget.work)
            sufficient = "valid"
            if not certificate.minimum_cardinality:
                return MacroValidationReport(sufficient, "not_claimed", "completed", checked, budget.work)
            if not check_minimality:
                return MacroValidationReport(sufficient, minimality, "sufficiency_only", checked, budget.work)
            minimality = "undecided"
            for size in range(len(certificate.retained_evidence)):
                for subset in combinations(evidence, size):
                    if checked >= max_subsets:
                        return MacroValidationReport(sufficient, minimality, "subset_budget_exhausted", checked, budget.work)
                    budget.consume("subset_checks")
                    checked += 1
                    if self._answers(subset, budget) == answers:
                        return MacroValidationReport(sufficient, "invalid", "smaller_basis_found", checked, budget.work)
            return MacroValidationReport(sufficient, "valid", "completed", checked, budget.work)
        except SearchBudgetExceeded as error:
            return MacroValidationReport(sufficient, minimality, error.reason, checked, budget.work)

    def validates_macro(self, certificate: MacroEvidenceCertificate, evidence, *,
                        max_subsets=10000, budget=None) -> bool:
        """Bounded boolean compatibility wrapper; False also includes undecided.

        Use verify_macro to distinguish invalidity from resource exhaustion.
        """
        return self.verify_macro(certificate, evidence, max_subsets=max_subsets, budget=budget).valid

    def next_experiment(self, evidence=(), *, budget=None) -> Optional[SearchExperiment]:
        """Greedy unequal-answer pair coverage per cost, not information gain.

        None means no known separating experiment; it is not a success status.
        """
        budget = budget if budget is not None else SearchWorkBudget()
        evidence = self._evidence(evidence, budget)
        possible = self._worlds(evidence=evidence, budget=budget)
        unique = {}
        for h in self.hypotheses:
            budget.consume("candidate_checks")
            if h.world in possible:
                unique[h.world, h.macro_answer] = h
        survivors = tuple(unique.values())
        measured = {o.experiment for o in evidence}
        ranked = []
        for i, experiment in enumerate(self.protocol.experiments):
            if experiment.name in measured:
                continue
            covered = 0
            for a, b in combinations(survivors, 2):
                budget.consume("pair_checks")
                covered += (a.macro_answer != b.macro_answer
                            and self.protocol.worlds[a.world][i] != self.protocol.worlds[b.world][i])
            if covered:
                ranked.append((Fraction(covered, experiment.cost), -experiment.cost, -i, experiment))
        return max(ranked, key=lambda item: item[:3])[3] if ranked else None
