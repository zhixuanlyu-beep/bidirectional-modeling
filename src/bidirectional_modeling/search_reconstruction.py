"""Explicit commitment rewrites, independent of model parentage and complexity."""
from dataclasses import dataclass

from .search import SearchHypothesis, _name


@dataclass(frozen=True)
class ReconstructionRule:
    name: str
    withdraw: tuple = ()
    add: tuple = ()

    def __post_init__(self):
        _name(self.name)
        for field in ('withdraw','add'):
            values = tuple(getattr(self,field))
            for value in values:
                _name(value)
            if len(values) != len(set(values)):
                raise ValueError('duplicate commitment in rewrite')
            object.__setattr__(self,field,values)
        if set(self.withdraw) & set(self.add):
            raise ValueError('cannot withdraw and add the same commitment')

    def apply(self, parent, *, name, world, macro_answer, description, materials=None):
        """New prediction and full description cost must be supplied explicitly.

        Preserving declared commitments does not prove arbitrary structural or
        observational equivalence. The search constructor validates the output.
        """
        if not set(self.withdraw).issubset(parent.commitments):
            raise ValueError('cannot withdraw an uncommitted constraint')
        commitments = tuple(dict.fromkeys(
            tuple(c for c in parent.commitments if c not in self.withdraw)+self.add))
        return SearchHypothesis(name,world,macro_answer,description,commitments,
                                parent.materials if materials is None else tuple(materials))
