"""Exact response bitset index; the scan backend remains the reference oracle."""


class WorldMask:
    def __init__(self, bits):
        self.bits = bits

    def __bool__(self):
        return bool(self.bits)

    def __contains__(self, index):
        return type(index) is int and index >= 0 and bool(self.bits & (1 << index))

    def __iter__(self):
        bits = self.bits
        while bits:
            low = bits & -bits
            yield low.bit_length()-1
            bits ^= low


from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class ResponseIndex:
    protocol: object
    responses: object
    constraints: object
    all_worlds: int

    def __init__(self, protocol, budget):
        # Publish only after this constructor finishes; a partial index is never used.
        budget.consume('index_operations')
        responses = {}
        constraints = {}
        all_worlds = (1 << len(protocol.worlds))-1
        for i,world in enumerate(protocol.worlds):
            for experiment,response in zip(protocol.experiments,world):
                budget.consume('index_entries')
                key = experiment.name,response
                responses[key] = responses.get(key,0) | (1 << i)
        for constraint in protocol.constraints:
            bits = 0
            for i in constraint.worlds:
                budget.consume('index_entries')
                bits |= 1 << i
            constraints[constraint.name] = bits
        object.__setattr__(self, 'protocol', protocol)
        object.__setattr__(self, 'responses', MappingProxyType(responses))
        object.__setattr__(self, 'constraints', MappingProxyType(constraints))
        object.__setattr__(self, 'all_worlds', all_worlds)

    def allows(self, observation, budget):
        budget.consume('response_checks')
        return (observation.experiment,observation.response) in self.responses

    def filter(self, commitments, evidence, budget):
        bits = self.all_worlds
        for name in commitments:
            budget.consume('index_operations')
            bits &= self.constraints[name]
        for observation in evidence:
            budget.consume('index_operations')
            bits &= self.responses.get((observation.experiment,observation.response),0)
        return WorldMask(bits)
