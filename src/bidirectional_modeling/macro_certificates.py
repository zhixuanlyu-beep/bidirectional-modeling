"""Compact sufficient evidence with explicit unequal-answer exclusion witnesses.

Generation uses greedy weighted cover, not an optimality claim. Verification
needs retained evidence and the bound catalogue, not the entire history.
"""
from .structural import ordered_tuple
from dataclasses import dataclass
from fractions import Fraction

from .search import SearchBudgetExceeded, SearchWorkBudget, SearchObservation, _name, _natural


@dataclass(frozen=True)
class MacroSufficiencyCertificate:
    problem_fingerprint: str
    answer: str
    evidence: tuple
    witness_candidate: str
    # One (candidate name, retained observation) per unequal-answer candidate.
    exclusions: tuple

    def __post_init__(self):
        _name(self.answer)
        _name(self.witness_candidate)
        object.__setattr__(self, 'evidence', ordered_tuple(self.evidence))
        object.__setattr__(self, 'exclusions', tuple(ordered_tuple(x) for x in ordered_tuple(self.exclusions)))


@dataclass(frozen=True)
class MacroSufficiencyResult:
    status: str
    reason: str
    certificate: object = None
    retained_cost: object = None
    # Neither a greedy cover nor a sufficient certificate proves optimality.
    minimality: str = 'not_claimed'


def certify_macro_sufficiency(problem, evidence, *, read_costs=None, budget=None):
    """Compress determined answers against the original catalogue.

    read_costs maps observations to positive integers. It measures evidence-read
    cost and deliberately defaults to one, not experiment acquisition cost.
    """
    budget = budget if budget is not None else SearchWorkBudget()
    def pending(reason):
        return MacroSufficiencyResult('undecided', reason)
    try:
        evidence = tuple(dict.fromkeys(problem._evidence(evidence, budget)))
        if any(h.world is None for h in problem.hypotheses):
            return pending('unknown_prediction')
        answers = problem._answers(evidence, budget)
        if len(answers) != 1:
            return pending('empty_version_space' if not answers else 'multiple_answers')
        answer = answers[0]
        costs = {} if read_costs is None else dict(read_costs)
        if any(o not in evidence for o in costs):
            raise ValueError('cost supplied for unknown observation')
        for value in costs.values():
            _natural(value)
            if not value:
                raise ValueError('evidence read costs must be positive')
        indices = {e.name: i for i, e in enumerate(problem.protocol.experiments)}
        def disagrees(h, o):
            budget.consume('response_checks')
            return problem.protocol.worlds[h.world][indices[o.experiment]] != o.response
        witness = next(h.name for h in problem.hypotheses if h.macro_answer == answer
                       and not any(disagrees(h, o) for o in evidence))
        uncovered = {h.name: h for h in problem.hypotheses if h.macro_answer != answer}
        retained, exclusions = [], []
        while uncovered:
            choices = []
            for i, observation in enumerate(evidence):
                covered = tuple(n for n, h in uncovered.items() if disagrees(h, observation))
                if covered:
                    choices.append((Fraction(len(covered), costs.get(observation, 1)), -i, observation, covered))
            _, _, observation, covered = max(choices, key=lambda x: x[:2])
            retained.append(observation)
            for name in covered:
                exclusions.append((name, observation))
                del uncovered[name]
        certificate = MacroSufficiencyCertificate(problem.fingerprint, answer,
            tuple(retained), witness, tuple(exclusions))
        return MacroSufficiencyResult('verified', 'sufficient_greedy_cover', certificate,
            sum(costs.get(o, 1) for o in retained))
    except SearchBudgetExceeded as error:
        return pending(error.reason)


def _well_formed_certificate(certificate):
    return (isinstance(certificate, MacroSufficiencyCertificate)
            and all(isinstance(value, str) and value.strip() for value in
                    (certificate.problem_fingerprint, certificate.answer, certificate.witness_candidate))
            and isinstance(certificate.evidence, tuple)
            and all(isinstance(o, SearchObservation) for o in certificate.evidence)
            and isinstance(certificate.exclusions, tuple)
            and all(isinstance(row, tuple) and len(row) == 2
                    and isinstance(row[0], str) and isinstance(row[1], SearchObservation)
                    for row in certificate.exclusions))


def verify_macro_sufficiency(problem, certificate, evidence, *, budget=None):
    """Validate witnesses; evidence may consist only of the retained live records.

    Hash binding is not a proof of evidence authenticity. Callers own the data
    provenance and must check it before supplying the retained records here.
    """
    budget = budget if budget is not None else SearchWorkBudget()
    if not _well_formed_certificate(certificate):
        return 'invalid'
    if certificate.problem_fingerprint != problem.fingerprint:
        return 'invalid'
    try:
        budget.consume('certificate_checks')
        evidence = problem._evidence(evidence, budget)
        if not set(certificate.evidence).issubset(evidence):
            return 'invalid'
        if any(h.world is None for h in problem.hypotheses):
            return 'undecided'
        by_name = {h.name: h for h in problem.hypotheses}
        witness = by_name.get(certificate.witness_candidate)
        if witness is None or witness.macro_answer != certificate.answer:
            return 'invalid'
        indices = {e.name: i for i, e in enumerate(problem.protocol.experiments)}
        def disagrees(h, o):
            budget.consume('response_checks')
            return problem.protocol.worlds[h.world][indices[o.experiment]] != o.response
        if any(disagrees(witness, o) for o in certificate.evidence):
            return 'invalid'
        excluded = dict(certificate.exclusions)
        expected = {h.name for h in problem.hypotheses if h.macro_answer != certificate.answer}
        if set(excluded) != expected or len(excluded) != len(certificate.exclusions):
            return 'invalid'
        for name, observation in excluded.items():
            if observation not in certificate.evidence or not disagrees(by_name[name], observation):
                return 'invalid'
        return 'valid'
    except SearchBudgetExceeded:
        return 'undecided'
