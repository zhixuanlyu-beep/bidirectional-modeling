"""Data-only replay for finite, context-independent state tables.

This deliberately small language has no expressions, imports or Python hooks.
Every state/action pair declares a successor or an explicit undefined result.
"""
import json
from dataclasses import asdict, dataclass

from .core import Context, Intervention, ModelMetrics, ScenarioKey, Trace, UndefinedTransition
from .search import DescriptionLength, SearchBudgetExceeded, SearchWorkBudget, _name, _natural
from .search_adapter import ExecutableSearchAdapter, ModelSearchCandidate, ModelSearchCase
from .search_session import SearchSession
from .structural import fingerprint_value, ordered_tuple
from .versions import (FINITE_MODEL_SCHEMA_VERSION, REPLAY_SCHEMA_VERSION,
                       REPLAY_IDENTITY_DOMAINS, SESSION_SCHEMA_VERSION)


def _json_value(value):
    return json.loads(json.dumps(value, allow_nan=False))


@dataclass(frozen=True)
class FiniteModelDeclaration:
    name: str
    states: tuple
    initial_states: tuple
    actions: tuple
    transitions: tuple
    readouts: tuple
    resources: tuple = (0, 0, 0)
    schema_version: int = FINITE_MODEL_SCHEMA_VERSION

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != FINITE_MODEL_SCHEMA_VERSION:
            raise ValueError('unsupported finite model schema')
        _name(self.name)
        for field in ('states', 'initial_states', 'actions', 'resources'):
            object.__setattr__(self, field, ordered_tuple(getattr(self, field)))
        transitions = tuple(ordered_tuple(row) for row in ordered_tuple(self.transitions))
        readouts = tuple((state, tuple(ordered_tuple(pair) for pair in ordered_tuple(values)))
                         for state, values in ordered_tuple(self.readouts))
        object.__setattr__(self, 'transitions', transitions)
        object.__setattr__(self, 'readouts', readouts)
        for values in (self.states, self.initial_states, self.actions):
            if not values or len(set(values)) != len(values):
                raise ValueError('state and action domains must be nonempty and unique')
            for value in values:
                _name(value)
        if not set(self.initial_states) <= set(self.states) or 'noop' not in self.actions:
            raise ValueError('initial states must be declared; actions must include noop')
        pairs = set()
        for row in transitions:
            if len(row) != 3:
                raise ValueError('transition requires state, action and successor')
            state, action, successor = row
            if (state not in self.states or action not in self.actions
                    or (successor is not None and successor not in self.states)
                    or (state, action) in pairs):
                raise ValueError('invalid or duplicate transition')
            pairs.add((state, action))
        if len(pairs) != len(self.states) * len(self.actions):
            raise ValueError('every state/action pair requires an explicit result')
        if len(readouts) != len(self.states) or {s for s, _ in readouts} != set(self.states):
            raise ValueError('readouts must cover each state exactly once')
        for _, values in readouts:
            if not values or len({key for key, _ in values}) != len(values):
                raise ValueError('readout fields must be nonempty and unique')
            for key, value in values:
                _name(key)
                if value is not None and type(value) not in (str, int, bool):
                    raise TypeError('v1 readouts support only null, strings, integers and booleans')
        if len(self.resources) != 3:
            raise ValueError('resources require cost, complexity and risk')
        for value in self.resources:
            _natural(value)

    def to_dict(self):
        return _json_value(asdict(self))

    @property
    def fingerprint(self):
        return fingerprint_value(('finite-model-table-v1', self.to_dict()))


@dataclass(frozen=True)
class DeclaredFiniteModel:
    declaration: FiniteModelDeclaration

    @property
    def name(self):
        return self.declaration.name

    @property
    def metrics(self):
        return ModelMetrics(*self.declaration.resources)

    assumptions = ('context-independent finite table',)
    failure_boundaries = ('only the declared states, actions and horizons',)
    capabilities = ('finite-table-v1',)

    def search_signature(self):
        return self.declaration.to_dict()

    def simulate(self, context, horizon):
        _natural(horizon)
        interventions = context.interventions
        if context.include_baseline and not any(i.name == 'baseline' for i in interventions):
            interventions = (Intervention('baseline'),) + interventions
        if not interventions:
            interventions = (Intervention('baseline'),)
        table = {(state, action): successor for state, action, successor in self.declaration.transitions}
        outputs = {state: dict(values) for state, values in self.declaration.readouts}
        for initial in self.declaration.initial_states:
            for intervention in interventions:
                state = initial
                snapshots = [dict(outputs[state])]
                for step in range(horizon):
                    state = table.get((state, intervention.action_at(step)))
                    if state is None:
                        raise UndefinedTransition('undefined finite table transition')
                    snapshots.append(dict(outputs[state]))
                yield Trace(self.name, initial, intervention.name, tuple(snapshots))


def _case_record(case):
    # v1 has no environmental expressions or provenance callbacks. A caller-owned
    # manifest is mandatory: the evaluator does not trust third-party enumeration.
    context = case.context
    if context.environment or context.history or context.assumptions:
        raise ValueError('v1 replay cases require empty environment, history and assumptions')
    if type(context.include_baseline) is not bool or not context.scenario_manifest:
        raise ValueError('v1 replay cases require a boolean baseline and explicit scenario manifest')
    for value in (context.scale, context.observer):
        _name(value)
    for intervention in context.interventions:
        _name(intervention.name)
        if type(intervention.repeat_last) is not bool:
            raise ValueError('repeat_last must be a boolean')
        for action in intervention.actions:
            _name(action)
    return _json_value(asdict(case))


def _restore_case(record):
    c = record['context']
    context = Context(**dict(c, interventions=tuple(Intervention(**i) for i in c['interventions']),
                            scenario_manifest=tuple(ScenarioKey(**s) for s in c['scenario_manifest'])))
    case = ModelSearchCase(record['experiment'], context, ScenarioKey(**record['scenario']),
                           record['field'], record['horizon'])
    _case_record(case)
    return case


def create_replay_bundle(protocol, candidates, cases, *, target, world_answers, evidence=(),
                         max_simulations=10000, max_operations=1000000, backend='scan'):
    """Prepare predictions and archive a session plus its complete data sources."""
    from . import __version__
    candidates, cases = ordered_tuple(candidates), ordered_tuple(cases)
    records = []
    for candidate in candidates:
        if type(candidate.model) is not DeclaredFiniteModel:
            raise TypeError('replay bundles accept only DeclaredFiniteModel candidates')
        records.append(dict(model=candidate.model.declaration.to_dict(),
                            description=asdict(candidate.description),
                            commitments=candidate.commitments, materials=candidate.materials))
    case_records = [_case_record(case) for case in cases]
    _natural(max_operations)
    prepared = ExecutableSearchAdapter().prepare(protocol, candidates, cases, target=target,
        world_answers=world_answers, max_simulations=max_simulations, backend=backend)
    session = SearchSession.from_prepared(prepared, evidence, budget=SearchWorkBudget(max_operations))
    report = session.run(budget=SearchWorkBudget(max_operations))
    payload = _json_value(dict(schema_version=REPLAY_SCHEMA_VERSION, producer_version=__version__,
        identity_domains=REPLAY_IDENTITY_DOMAINS, session_schema=SESSION_SCHEMA_VERSION,
        protocol=asdict(protocol), candidates=records, cases=case_records,
        target=target, world_answers=prepared.search.world_answers, backend=backend,
        budgets=dict(max_simulations=max_simulations, max_operations=max_operations),
        preparation=dict(simulations_used=prepared.simulations_used, diagnostics=prepared.diagnostics),
        session=json.loads(session.to_json()), search_work=asdict(report.work)))
    return json.dumps(dict(payload=payload, checksum=fingerprint_value(payload)),
                      ensure_ascii=False, sort_keys=True, indent=2)


@dataclass(frozen=True)
class ReplayResult:
    status: str
    reason: str
    session: object = None
    report: object = None
    simulations_used: int = 0


def replay_bundle(text, *, max_bytes=5000000, max_simulations=10000,
                  max_operations=1000000, max_horizon=1000):
    """Rebuild predictions and revalidate evidence; never restore executable code.

    Package versions are advisory. Unknown format/identity versions require
    explicit regeneration. Each validation/search phase receives the recorded
    operation cap; prediction collection shares the recorded simulation cap.
    Reviewer-owned ceilings reject excessive recorded budgets or horizons as
    undecided before rebuilding models. Explicit overrides do not change bindings.
    """
    from .search import ResponseConstraint, SearchExperiment, SearchProtocol
    for limit in (max_bytes, max_simulations, max_operations, max_horizon):
        _natural(limit)
    if len(text.encode('utf-8')) > max_bytes:
        raise ValueError('replay bundle exceeds input limit')
    document = json.loads(text)
    p = document['payload']
    if document['checksum'] != fingerprint_value(p):
        raise ValueError('replay checksum mismatch')
    if (type(p['schema_version']) is not int or p['schema_version'] != REPLAY_SCHEMA_VERSION
            or type(p['session_schema']) is not int or p['session_schema'] != SESSION_SCHEMA_VERSION
            or p['identity_domains'] != REPLAY_IDENTITY_DOMAINS):
        raise ValueError('unsupported replay versions; regenerate from original declarations')
    budgets = p['budgets']
    _natural(budgets['max_simulations'])
    _natural(budgets['max_operations'])
    if (budgets['max_simulations'] > max_simulations
            or budgets['max_operations'] > max_operations):
        return ReplayResult('undecided', 'review_resource_limit')
    for case in p['cases']:
        _natural(case['horizon'])
        if case['horizon'] > max_horizon:
            return ReplayResult('undecided', 'review_horizon_limit')
    raw = p['protocol']
    protocol = SearchProtocol(raw['scope'], raw['coding'],
        tuple(SearchExperiment(**e) for e in raw['experiments']), raw['worlds'],
        tuple(ResponseConstraint(**c) for c in raw['constraints']))
    candidates = tuple(ModelSearchCandidate(DeclaredFiniteModel(FiniteModelDeclaration(**c['model'])),
        DescriptionLength(**c['description']), c['commitments'], c['materials']) for c in p['candidates'])
    cases = tuple(_restore_case(c) for c in p['cases'])
    try:
        archived = SearchSession.from_json(json.dumps(p['session']),
            budget=SearchWorkBudget(budgets['max_operations']))
    except SearchBudgetExceeded as error:
        return ReplayResult('undecided', error.reason)
    prepared = ExecutableSearchAdapter().prepare(protocol, candidates, cases, target=p['target'],
        world_answers=p['world_answers'], backend=p['backend'], max_simulations=budgets['max_simulations'])
    if (prepared.search.fingerprint != archived.search.fingerprint
            or prepared.batch_bindings != archived.model_bindings
            or archived.prepared_problem_fingerprint != prepared.search.fingerprint
            or _json_value(dict(simulations_used=prepared.simulations_used,
                                diagnostics=prepared.diagnostics)) != p['preparation']):
        return ReplayResult('invalid', 'prediction_or_declaration_binding_mismatch',
                            simulations_used=prepared.simulations_used)
    try:
        session = SearchSession.from_prepared(prepared, archived.evidence,
            budget=SearchWorkBudget(budgets['max_operations']))
    except SearchBudgetExceeded as error:
        return ReplayResult('undecided', error.reason, simulations_used=prepared.simulations_used)
    session.certificates = archived.certificates
    report = session.run(budget=SearchWorkBudget(budgets['max_operations']))
    pending = bool(prepared.diagnostics or report.undecided or report.stop_reason in (
        'work_budget_exhausted', 'cancelled'))
    return ReplayResult('undecided' if pending else 'verified', report.stop_reason,
                        session, report, prepared.simulations_used)
