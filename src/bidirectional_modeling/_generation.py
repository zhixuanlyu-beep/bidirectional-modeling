"""Bounded candidate enumeration shared by realization and interpretation."""
from collections.abc import Set as AbstractSet
from .core import VerificationIssue


class CandidateStream:
    """Never pull beyond the limit; retain errors without discarding the prefix."""
    def __init__(self, factory, limit):
        self.factory = factory
        self.limit = limit
        self.inspected = 0
        self.diagnostics = []
        self._iterator = None
        self._count = None
        self._exhausted = False
        self._stopped = False

    @property
    def complete(self):
        return self._exhausted or (self._count is not None and self.inspected == self._count)

    def __iter__(self):
        return self

    def __next__(self):
        if self._stopped or self.complete or self.inspected >= self.limit:
            raise StopIteration
        if self._iterator is None:
            try:
                items = self.factory()
                if isinstance(items, AbstractSet):
                    raise TypeError('candidate generation cannot return an unordered set')
                # Only concrete built-in catalogues establish an exact size.
                if type(items) in (tuple, list):
                    items = tuple(items)
                    self._count = len(items)
                self._iterator = iter(items)
            except Exception as error:
                self._fail(error)
        try:
            item = next(self._iterator)
        except StopIteration:
            self._exhausted = True
            raise
        except Exception as error:
            self._fail(error)
        self.inspected += 1
        return item

    def _fail(self, error):
        self.diagnostics.append(VerificationIssue(
            'candidate-generation', '%s: %s' % (type(error).__name__, error)))
        self._stopped = True
        raise StopIteration from None
