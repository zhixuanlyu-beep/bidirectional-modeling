"""Check that distinguishing experiments reject explicit semantic mistakes.

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
    ('effect name inherits hash-dependent value repr', 'interpretation.py',
     'label = "maintain %s at %s" % (field_name, deterministic_repr(final_value))',
     'label = "maintain %s at %s" % (field_name, repr(final_value))',
     'test_deterministic_execution.HashSeedContracts'),
    ('requirement text inherits hash-dependent value repr', 'core.py',
     'deterministic_repr(self.expected)', 'repr(self.expected)',
     'test_deterministic_execution.HashSeedContracts'),
    ('ordered input silently accepts a set', 'structural.py',
     'if isinstance(values, AbstractSet):', 'if False:',
     'test_deterministic_execution.OrderedInputContracts.test_semantic_sets_remain_canonical_and_ordered_iterators_remain_lazy'),
    ('candidate stream chooses from an unordered set', '_generation.py',
     'if isinstance(items, AbstractSet):', 'if False:',
     'test_deterministic_execution.OrderedInputContracts.test_set_generator_is_undecided_without_selecting_a_candidate'),
    ('opaque requirement address becomes semantic identity', 'core.py',
     "raise TypeError('requirement must declare a deterministic semantic_signature')",
     "return ('opaque', id(requirement))",
     'test_deterministic_execution.OrderedInputContracts.test_opaque_identity_never_enters_a_semantic_signature'),
    ('simulation enumerates an unordered trace set', 'evaluation.py',
     'if isinstance(generated, AbstractSet):', 'if False:',
     'test_deterministic_execution.OrderedInputContracts.test_unordered_simulation_cannot_certify_a_batch'),
    ('boolean judgment enumerates the whole response domain', 'search.py',
     'return receipt.status is QueryStatus.FOUND',
     'return bool(self._worlds(commitments, evidence, budget=budget))',
     'test_redundancy_costs.BooleanWorkBoundaries'),
    ('interrupted existence becomes absence', 'search.py',
     'raise SearchBudgetExceeded(receipt.reason, receipt.work)', 'return False',
     'test_redundancy_costs.BooleanWorkBoundaries'),
    ('absence replay trusts the response index', 'search_queries.py',
     'budget=budget,force_scan=True)', 'budget=budget,force_scan=False)',
     'test_redundancy_costs.AbsenceWorkBoundaries'),
    ('prediction promotion constructs a temporary catalogue', 'search_adapter.py',
     'problem._validate_hypothesis(proposed)', 'problem.with_hypotheses((proposed,))',
     'test_redundancy_costs.PromotionWorkBoundaries'),
    ('composition trusts an unbound residual receipt', 'composition.py',
     'or residual_report.model_name != model.name',
     'or False',
     'test_cross_module_boundaries.CompositionWorkBoundaries'),
    ('correspondence suite ignores earlier case drift', 'correspondence.py',
     'if input_binding(case) != binding:', 'if False:',
     'test_cross_module_boundaries.CorrespondenceCollectionBoundaries'),
    ('correspondence collection failure becomes free work', 'correspondence.py',
     'return batch, allowance', 'return batch, 0',
     'test_cross_module_boundaries.CorrespondenceCollectionBoundaries'),
    ('correspondence skips final evidence binding', 'correspondence.py',
     '            if not batch.binds(model, context, horizon):\n                binding_complete = False',
     '            if False:\n                binding_complete = False',
     'test_cross_module_boundaries.CorrespondenceCollectionBoundaries'),
    ('graph publishes scales before conflict checks', 'correspondence.py',
     '        for scale in (correspondence.lower_scale, correspondence.upper_scale):\n            existing = self._scales.get(scale.name)',
     '        for scale in (correspondence.lower_scale, correspondence.upper_scale):\n            self._scales[scale.name] = scale\n            existing = self._scales.get(scale.name)',
     'test_cross_module_boundaries.GraphTransactionBoundaries'),
    ('composition ignores the first operational refutation', 'composition.py',
     '                        if not full_diagnostics:\n                            break',
     '                        if False:\n                            break',
     'test_cross_module_boundaries.CompositionWorkBoundaries'),
    ('malformed replay result becomes free work', 'search_partial.py',
     'self.used = self.limit', 'self.used = 0',
     'test_audit_execution_boundaries.ExecutionOutputBoundaries'),
    ('malformed adapter result becomes free work', 'search_adapter.py',
     'used = max_simulations', 'used = 0',
     'test_audit_execution_boundaries.ExecutionOutputBoundaries'),
    ('transport verifier skips independent target proof', 'certificate_transport.py',
     "return 'valid' if target_search.validates_conflict(\n            receipt.certificate, active, budget=budget) else 'invalid'",
     "return 'valid'",
     'test_audit_execution_boundaries.IndependentTransportBoundaries'),
    ('antichain construction ignores its local allowance', 'search.py',
     'if allowance[0] == 0:', 'if False:',
     'test_search_antichain.AntichainCostTests'),
    ('gluing existence performs unrequested overlap checks', 'extensions/gluing.py',
     'if check_overlap:', 'if True:',
     'test_review_cost_boundaries.GluingClaimBoundaries'),
    ('overlapping conflict cores treated as subsuming', 'search.py',
     'if core.issubset(proposed):', 'if core.intersection(proposed):',
     'test_search_antichain.AntichainTests'),
    ('residual refinement ignores future actions', 'residual.py',
     'signatures.append((current_class, tuple(targets)))',
     'signatures.append((current_class, ()))',
     'test_residual_relation_reference.ResidualRelationReference'),
    ('initialization StopIteration becomes exhaustion', '_generation.py',
     '            except Exception as error:\n                self._fail(error)',
     '            except StopIteration:\n                self._exhausted = True\n                raise\n            except Exception as error:\n                self._fail(error)',
     'test_execution_contracts.GenerationBoundaryTests'),
    ('effect generation eagerly materializes every hypothesis', 'interpretation.py',
     'factory = lambda: trace_generator(batch.traces, batch.complete)',
     'factory = lambda: tuple(trace_generator(batch.traces, batch.complete))',
     'test_execution_contracts.GenerationBoundaryTests'),
    ('implication witness need not violate conclusion', 'extensions/implications.py',
     'and claim.conclusion not in witness.attributes', 'and True',
     'test_implication_exploration.ImplicationContracts'),
    ('holdout refutation becomes verified', 'correspondence.py',
     "for status in ('refuted', 'undecided', 'not_applicable'):",
     "for status in ('undecided', 'not_applicable'):", 'test_audit_boundaries.AuditBoundaryTests'),
    ('unknown prediction becomes identifiable', 'search.py',
     "if unknown:\n            return MacroIdentifiabilityResult('undecided', 'unknown_prediction')",
     "if False:\n            return MacroIdentifiabilityResult('undecided', 'unknown_prediction')",
     'test_audit_boundaries.AuditBoundaryTests'),
    ('interpretation evaluator failure loses reserved allowance', 'interpretation.py',
     'simulations_used += remaining_simulations', 'simulations_used += 0',
     'test_audit_boundaries.AuditBoundaryTests'),
    ('minimum replaced by maximum', 'core.py',
     '            return min(values)', '            return max(values)', 'test_concept_contracts.AggregationContracts'),
    ('EACH replaced by FINAL', 'core.py',
     'checks = [_compare(value, self.operator, self.expected, self.tolerance) for value in values]',
     'checks = [_compare(values[-1], self.operator, self.expected, self.tolerance)]', 'test_concept_contracts.AggregationContracts'),
    ('joint conflict prunes individual commitments', 'search.py',
     'if core.issubset(h.commitments):',
     'if core.intersection(h.commitments):', 'test_concept_contracts.ExclusionConeContracts'),
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

    ('cached exclusion ignores withdrawn observation', 'search_partial.py',
     'if observation not in evidence:', 'if False:',
     'test_evidence_lifecycle.ScreeningLifecycleTests'),
    ('session retains revoked certificates', 'search_session.py',
     'self.evidence, self.certificates = evidence, tuple(retained)',
     'self.evidence, self.certificates = evidence, self.certificates',
     'test_evidence_lifecycle.MigrationLifecycleTests'),
    ('distinction ignores exact source state', 'residual.py',
     'if _state_key(state) != _state_key(claimed.micro_state):',
     'if False:', 'test_distinguishing_replay.DistinguishingReplayTests'),
    ('repair omits another minimum solution', 'search_repairs.py',
     "return 'valid' if tuple(actual) == proposed else 'invalid'",
     "return 'valid'", 'test_search_repairs.RepairContracts'),
    ('unsupported implication becomes proved', 'extensions/implications.py',
     'if not support:', 'if False:',
     'test_implication_exploration.ImplicationContracts'),
    ('implication generator ignores a refuting object', 'extensions/implications.py',
     'if implication.conclusion not in obj.attributes:', 'if False:',
     'test_implication_exploration.ImplicationContracts'),
    ('empty context mapping accepted as checked relation', 'context_network.py',
     'if not transition.experiments:', 'if False:',
     'test_context_network.ContextTests'),
    ('explanation ignores revoked evidence', 'search_explanations.py',
     "if not search.validates_conflict(certificate, active_evidence, budget=budget):\n            return 'invalid'",
     "if False:\n            return 'invalid'",
     'test_search_explanations.ExplanationContracts'),
    ('explanation builder drops observation node', 'search_explanations.py',
     "        nodes.append(DependencyNode(key, 'observation', observation, (prefix + 'protocol',)))",
     '        pass  # intentionally omit the declared observation node',
     'test_search_explanations.ExplanationContracts'),

)


def run(source, target, cwd):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(source), str(ROOT/'tests'))),
               PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run([sys.executable, '-m', 'unittest', target, '-q'], cwd=cwd,
                          env=env, text=True, capture_output=True, timeout=30)


def main():
    for target in dict.fromkeys(mutation[4] for mutation in MUTATIONS):
        baseline = run(ROOT/'src', target, ROOT)
        if baseline.returncode:
            print('BASELINE FAILED: ' + target)
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
