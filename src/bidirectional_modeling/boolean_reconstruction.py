"""A bounded executable Boolean language for auditable structural reconstruction.

The language contains canonical commutative ASTs, not arbitrary Python programs.
Coverage and irreducibility claims are relative to its declared node bound.
"""
import json
from dataclasses import asdict, dataclass
from itertools import product

from .core import FiniteStateModel, ModelMetrics
from .search import DescriptionLength, SearchBudgetExceeded, SearchWorkBudget, _name, _natural
from .structural import fingerprint_value


_ARITY = {'false': 0, 'true': 0, 'var': 1, 'not': 1, 'and': 2, 'or': 2, 'xor': 2}


def _normalize(tree):
    tree = tuple(tree)
    if not tree or tree[0] not in _ARITY or len(tree) != 1 + _ARITY[tree[0]]:
        raise ValueError('invalid Boolean AST')
    if tree[0] == 'var':
        _name(tree[1])
        return tree
    children = tuple(_normalize(t) for t in tree[1:])
    if len(children) == 2:
        children = tuple(sorted(children))
    return (tree[0],) + children


def _nodes(tree):
    return 1 if tree[0] == 'var' else 1 + sum(_nodes(t) for t in tree[1:])


def _variables(tree):
    if tree[0] == 'var':
        return {tree[1]}
    return set().union(*(_variables(t) for t in tree[1:]))


def _evaluate(tree, inputs):
    op = tree[0]
    if op == 'var':
        value = inputs[tree[1]]
        if type(value) is not bool:
            raise ValueError('Boolean inputs must be bool values')
        return value
    if op in ('false', 'true'):
        return op == 'true'
    values = tuple(_evaluate(t, inputs) for t in tree[1:])
    if op == 'not':
        return not values[0]
    if op == 'and':
        return values[0] and values[1]
    if op == 'or':
        return values[0] or values[1]
    return values[0] != values[1]


@dataclass(frozen=True)
class BooleanExpression:
    tree: tuple

    def __post_init__(self):
        object.__setattr__(self, 'tree', _normalize(self.tree))

    @property
    def nodes(self):
        return _nodes(self.tree)

    def evaluate(self, inputs):
        return _evaluate(self.tree, inputs)

    @property
    def fingerprint(self):
        return fingerprint_value(('boolean-expression-v1', self.tree))

    def to_model(self, name, *, field='y'):
        """A stateless executable model; inputs come from Context.environment."""
        _name(field)
        # Capture primitive tuples, compatible with the existing callable encoder.
        tree = self.tree
        def transition(state, action, context):
            return state
        def readout(state, context):
            # Self-contained interpreter: the callable identity includes its
            # bytecode and primitive tree closure, with no opaque helper ID.
            pending, values = [(tree, False)], []
            while pending:
                node, ready = pending.pop()
                op = node[0]
                if op == 'var':
                    value = context.environment[node[1]]
                    if type(value) is not bool:
                        raise ValueError('Boolean inputs must be bool values')
                    values.append(value)
                elif op in ('false', 'true'):
                    values.append(op == 'true')
                elif not ready:
                    pending.append((node, True))
                    pending.extend((child, False) for child in reversed(node[1:]))
                elif op == 'not':
                    values.append(not values.pop())
                else:
                    right, left = values.pop(), values.pop()
                    values.append((left and right) if op == 'and' else
                                  (left or right) if op == 'or' else left != right)
            return {field: str(int(values[0]))}
        return FiniteStateModel(name, {'s': {}}, ('s',), ('noop',), transition, readout,
                                ModelMetrics(1, self.nodes, 0))

    def replace(self, path, replacement):
        """Replace a subtree; path indexes children in the canonical AST."""
        path = tuple(path)
        def rewrite(tree, rest):
            if not rest:
                return replacement.tree
            index = rest[0]
            if type(index) is not int or index < 0 or tree[0] == 'var' or index >= len(tree)-1:
                raise ValueError('invalid rewrite path')
            children = list(tree[1:])
            children[index] = rewrite(children[index], rest[1:])
            return (tree[0],) + tuple(children)
        return BooleanExpression(rewrite(self.tree, path))


@dataclass(frozen=True)
class BooleanLanguage:
    variables: tuple
    operations: tuple = ('not', 'and', 'or', 'xor')
    max_nodes: int = 3
    constants: bool = True

    def __post_init__(self):
        object.__setattr__(self, 'variables', tuple(self.variables))
        object.__setattr__(self, 'operations', tuple(self.operations))
        if not self.variables or len(set(self.variables)) != len(self.variables):
            raise ValueError('distinct variables required')
        for name in self.variables:
            _name(name)
        if len(set(self.operations)) != len(self.operations) or any(
                op not in ('not', 'and', 'or', 'xor') for op in self.operations):
            raise ValueError('invalid Boolean operation set')
        _natural(self.max_nodes)
        if self.max_nodes == 0 or type(self.constants) is not bool:
            raise ValueError('positive node bound and explicit constants flag required')

    @property
    def fingerprint(self):
        return fingerprint_value(('bounded-boolean-language-v1', asdict(self)))

    def accepts(self, expression):
        def allowed(tree):
            op = tree[0]
            if op == 'var':
                return tree[1] in self.variables
            if op in ('false', 'true'):
                return self.constants
            return op in self.operations and all(allowed(t) for t in tree[1:])
        return expression.nodes <= self.max_nodes and allowed(expression.tree)

    def description(self, expression):
        if not self.accepts(expression):
            raise ValueError('expression outside declared language')
        # A UTF-8 JSON language header, then gamma(node_count), then fixed-width
        # prefix tokens. Arity is in the header; a variable uses one token.
        header = json.dumps(asdict(self), sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        alphabet = len(self.variables) + len(self.operations) + (2 if self.constants else 0)
        width = max(1, (alphabet-1).bit_length())
        length = (2*expression.nodes.bit_length()-1) + expression.nodes*width
        return DescriptionLength(framework=len(header.encode('utf-8'))*8, relations=length)


@dataclass(frozen=True)
class BooleanCatalogue:
    language_fingerprint: str
    expressions: tuple
    complete: bool
    reason: str


def enumerate_boolean_language(language, *, budget=None):
    budget = budget if budget is not None else SearchWorkBudget()
    levels, expressions = {}, []
    def publish(tree, current):
        budget.consume('candidate_checks')
        expression = BooleanExpression(tree)
        if expression.tree not in current:
            current[expression.tree] = expression
            expressions.append(expression)
    try:
        for size in range(1, language.max_nodes+1):
            budget.consume('query_checks')
            current = {}
            if size == 1:
                for name in language.variables:
                    publish(('var', name), current)
                if language.constants:
                    for op in ('false', 'true'):
                        publish((op,), current)
            for op in language.operations:
                if op == 'not':
                    for child in levels.get(size-1, ()):
                        publish((op, child.tree), current)
                else:
                    for left_size in range(1, size-1):
                        for left, right in product(levels.get(left_size, ()), levels.get(size-1-left_size, ())):
                            # Commutative canonical syntax is part of the language.
                            if left.tree <= right.tree:
                                publish((op, left.tree, right.tree), current)
            levels[size] = tuple(current.values())
        return BooleanCatalogue(language.fingerprint, tuple(expressions), True, 'bounded_language_exhausted')
    except SearchBudgetExceeded as error:
        return BooleanCatalogue(language.fingerprint, tuple(expressions), False, error.reason)


@dataclass(frozen=True)
class BooleanSubstituteReport:
    language_fingerprint: str
    target_fingerprint: str
    experiment_fingerprint: str
    status: str  # found / absent / unknown
    witness: object = None
    reason: str = ''


def find_boolean_substitute(target, language, inputs, *, budget=None):
    """Search the entire bounded lower language, over precisely these inputs."""
    budget = budget if budget is not None else SearchWorkBudget()
    inputs = tuple(dict(row) for row in inputs)
    input_fingerprint = fingerprint_value(inputs)
    def result(status, reason, witness=None):
        return BooleanSubstituteReport(language.fingerprint, target.fingerprint,
                                       input_fingerprint, status, witness, reason)
    if not inputs:
        return result('unknown', 'empty_experiment_domain')
    if any(set(row) != set(language.variables) for row in inputs) or not _variables(target.tree) <= set(language.variables):
        raise ValueError('inputs and target must use declared variables')
    try:
        expected = []
        for row in inputs:
            budget.consume('response_checks')
            expected.append(target.evaluate(row))
        catalogue = enumerate_boolean_language(language, budget=budget)
        for expression in catalogue.expressions:
            values = []
            for row in inputs:
                budget.consume('response_checks')
                values.append(expression.evaluate(row))
            if values == expected:
                return result('found', 'equivalent_bounded_expression', expression)
        return result('absent' if catalogue.complete else 'unknown', catalogue.reason)
    except SearchBudgetExceeded as error:
        return result('unknown', error.reason)


def reconstruct_boolean(parent, replacement, path, language, rule, *, name, protocol,
                        cases, target, world_answers, max_simulations=10000):
    """Rewrite the AST, run the model adapter, and validate rewritten commitments.

    parent is (BooleanExpression, SearchHypothesis). No response or macro answer
    is accepted from the rewrite caller: both come from replay and target map.
    """
    from .search_adapter import ExecutableSearchAdapter, ModelSearchCandidate
    expression, hypothesis = parent
    rewritten = expression.replace(path, replacement)
    description = language.description(rewritten)
    declaration = rule.apply(hypothesis, name=name, world=None, macro_answer='unresolved',
                             description=description, materials=tuple(sorted(_variables(rewritten.tree))))
    candidate = ModelSearchCandidate(rewritten.to_model(name), description,
                                     declaration.commitments, declaration.materials)
    prepared = ExecutableSearchAdapter().prepare(protocol, (candidate,), cases,
        target=target, world_answers=world_answers, max_simulations=max_simulations)
    return rewritten, candidate, prepared


def verify_boolean_substitute(target, language, inputs, receipt, *, budget=None):
    """Recompute bounded coverage rather than trusting an external ABSENT flag."""
    inputs = tuple(dict(row) for row in inputs)
    if (receipt.language_fingerprint != language.fingerprint or
            receipt.target_fingerprint != target.fingerprint or
            receipt.experiment_fingerprint != fingerprint_value(inputs)):
        return 'invalid'
    if receipt.status == 'unknown':
        return 'undecided'
    replay = find_boolean_substitute(target, language, inputs, budget=budget)
    if replay.status == 'unknown':
        return 'undecided'
    return 'valid' if replay == receipt else 'invalid'
