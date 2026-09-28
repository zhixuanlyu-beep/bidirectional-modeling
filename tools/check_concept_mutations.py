"""Check that distinguishing experiments reject ten explicit semantic mistakes.

Each mutation runs in a fresh temporary source copy; this is a finite regression
exercise, not a proof against every possible incorrect implementation.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = (
    ('minimum replaced by maximum', 'core.py',
     '            return min(values)', '            return max(values)', 'test_concept_contracts.AggregationContracts'),
    ('EACH replaced by FINAL', 'core.py',
     'checks = [_compare(value, self.operator, self.expected, self.tolerance) for value in values]',
     'checks = [_compare(values[-1], self.operator, self.expected, self.tolerance)]', 'test_concept_contracts.AggregationContracts'),
    ('joint conflict prunes individual commitments', 'search.py',
     'if set(c.commitments).issubset(h.commitments):',
     'if set(c.commitments).intersection(h.commitments):', 'test_concept_contracts.ExclusionConeContracts'),
    ('revoked evidence remains applicable', 'search.py',
     'if not set(certificate.evidence).issubset(evidence):',
     'if False:  # intentionally ignore evidence withdrawal', 'test_concept_contracts.ExclusionConeContracts'),
    ('transport skips target proof', 'certificate_transport.py',
     'if not target_search.validates_conflict(proposed, target_evidence, budget=budget):',
     'if False:  # intentionally skip target proof', 'test_concept_contracts.ExclusionConeContracts'),
    ('absence of counterexamples implies success', 'correspondence.py',
     'return self.complete and self.commutes is True',
     'return not self.counterexamples', 'test_concept_contracts.CorrespondenceContracts'),
    ('maintenance checks only terminal value', 'interpretation.py',
     'aggregation = Aggregation.EACH', 'aggregation = Aggregation.FINAL',
     'test_execution_contracts.EffectMeaningTests'),
    ('late evaluator failure loses charged allowance', 'realization.py',
     'reserved = remaining_simulations', 'reserved = 0',
     'test_execution_contracts.FailureBudgetTests'),
    ('generation reads one candidate beyond limit', '_generation.py',
     'self.inspected >= self.limit', 'self.inspected > self.limit',
     'test_execution_contracts.GenerationBoundaryTests'),
    ('failure wording enters certificate identity', 'evaluation.py',
     'tuple(d.code for d in diagnostics))', 'tuple(d.detail for d in diagnostics))',
     'test_execution_contracts.FailureBudgetTests'),
    ('internal collection failure becomes free work', 'evaluation.py',
     'return self.simulation_limit', 'return len(self.traces)',
     'test_claim_boundaries.WorkAccountingTests'),
    ('mean rounded before truth judgment', 'core.py',
     'return sum((Fraction(value) for value in values), Fraction()) / len(values)',
     'return float(sum((Fraction(value) for value in values), Fraction()) / len(values))',
     'test_claim_boundaries.ExactMeanTests'),
    ('extra horizon silently changes acceptance', 'probes.py',
     'blocking: bool = False', 'blocking: bool = True',
     'test_claim_boundaries.ProbeScopeTests'),
    ('concept relation loses required provenance', 'extensions/concepts.py',
     'if any(not isinstance(v, str) or not v.strip() for v in (source, reason, applicability)):',
     'if False:', 'test_claim_boundaries.JudgmentAndExclusionTests'),

)


def run(source, target, cwd):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(source), str(ROOT/'tests'))),
               PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run([sys.executable, '-m', 'unittest', target, '-q'], cwd=cwd,
                          env=env, text=True, capture_output=True, timeout=30)


def main():
    for module in ('test_concept_contracts', 'test_execution_contracts', 'test_claim_boundaries'):
        baseline = run(ROOT/'src', module, ROOT)
        if baseline.returncode:
            print(baseline.stderr)
            return 1
    passed = True
    for name, filename, before, after, test_class in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix='concept-mutation-', dir=ROOT.parent) as folder:
            folder = Path(folder)
            source = folder/'src'
            shutil.copytree(ROOT/'src', source, ignore=shutil.ignore_patterns('__pycache__'))
            path = source/'bidirectional_modeling'/filename
            content = path.read_text()
            if content.count(before) != 1:
                raise RuntimeError('mutation anchor must match once: ' + name)
            path.write_text(content.replace(before, after))
            result = run(source, test_class, folder)
            # Import/runner errors do not count as experimentally detected mistakes.
            detected = result.returncode == 1 and 'FAIL:' in result.stderr and 'ERROR:' not in result.stderr
            print(('DETECTED' if detected else 'FAILED') + ': ' + name)
            if not detected:
                print(result.stderr)
                passed = False
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
