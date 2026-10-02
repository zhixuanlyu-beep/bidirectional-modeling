"""Distinguish ordered evidence, late input drift and exact resource claims."""
from dataclasses import replace
from fractions import Fraction
import unittest

from bidirectional_modeling import (Context, Evidence, Experiment, Intervention,
    InterpretationObservation, Interpreter, Realizer, ModelMetrics,
    ModelMetricRequirement, ResourceBudget, SatisfactionEvaluator, CustomRequirement,
    RequirementCategory, ScenarioKey)
from bidirectional_modeling.core import CheckResult, Counterexample, ProbeOutcome
from bidirectional_modeling.probes import HorizonExtensionProbe
from bidirectional_modeling.provenance import context_fingerprint, fingerprint_value
from test_interpretation_status import model, spec, hypothesis


def first_scenario(m, traces, c):
    return CheckResult('first', RequirementCategory.OBJECTIVE,
        traces[0].initial_state == 'a', traces[0].initial_state, 'a')


class OrderedBindings(unittest.TestCase):
    def test_reordered_history_cannot_reuse_a_previous_satisfaction_batch(self):
        first = Context(history=(Evidence('1', 'h', source='lab'), Evidence('0', 'h', source='lab')))
        reverse = replace(first, history=tuple(reversed(first.history)))
        m = replace(model(), readout=lambda s,c: {'x': int(c.history[0].statement)})
        e = SatisfactionEvaluator()
        batch = e.collect(m, first, 1)
        self.assertTrue(e.evaluate_batch(m, spec(), first, batch).satisfied)
        self.assertNotEqual(context_fingerprint(first), context_fingerprint(reverse))
        self.assertFalse(batch.binds(m, reverse, 1))
        reused = e.evaluate_batch(m, spec(), reverse, batch)
        self.assertFalse(reused.complete or reused.satisfied)
        fresh = e.evaluate(m, spec(), reverse)
        self.assertTrue(fresh.complete)
        self.assertFalse(fresh.satisfied)

    def test_callback_visible_sequences_bind_order_but_semantic_maps_do_not(self):
        ctx = Context(interventions=(Intervention('a'), Intervention('b')),
            assumptions=('one','two'),
            scenario_manifest=(ScenarioKey('a','baseline'), ScenarioKey('b','baseline')))
        for field in ('interventions','assumptions','scenario_manifest'):
            self.assertNotEqual(context_fingerprint(ctx), context_fingerprint(
                replace(ctx, **{field: tuple(reversed(getattr(ctx, field)))})))
        self.assertEqual(context_fingerprint(Context(environment={'a':1,'b':{'red','blue'}})),
            context_fingerprint(Context(environment={'b':{'blue','red'},'a':1})))
        self.assertNotEqual(context_fingerprint(Context()), fingerprint_value(
            ('context-v1', Context().semantic_signature())))

    def test_reordered_traces_are_different_satisfaction_evidence(self):
        m = replace(model(), states={'a':{'x':1}, 'b':{'x':1}}, initial_states=('a','b'))
        goal = replace(spec(), objectives=(CustomRequirement('first', RequirementCategory.OBJECTIVE,
            first_scenario, semantic_id='first-scenario-v1'),))
        e = SatisfactionEvaluator()
        batch = e.collect(m, Context(), 1)
        cert = e.evaluate_batch(m, goal, Context(), batch)
        reversed_traces = tuple(reversed(batch.traces))
        with self.assertRaises(ValueError):
            replace(batch, traces=reversed_traces)
        reverse_model = replace(m, initial_states=('b','a'))
        reverse = e.collect(reverse_model, Context(), 1)
        self.assertFalse(cert.binds_trace_batch(reverse))
        self.assertFalse(cert.binds_evidence(reverse_model, reverse.traces))
        result = e.evaluate_batch(reverse_model, goal, Context(), reverse)
        self.assertTrue(result.complete)
        self.assertFalse(result.satisfied)

    def test_certificate_rechecks_mutable_trace_content(self):
        e=SatisfactionEvaluator()
        batch=e.collect(model(),Context(),1)
        cert=e.evaluate_batch(model(),spec(),Context(),batch)
        batch.traces[0].snapshots[0]['x']=2
        self.assertFalse(cert.binds_trace_batch(batch))
        self.assertFalse(batch.binds(model(),Context(),1))


class FinalInputBindings(unittest.TestCase):
    def test_interpreter_cannot_publish_unique_after_context_drift(self):
        ctx = Context(environment={'x':1})
        m = replace(model(), readout=lambda s,c: {'x':c.environment['x']})
        def source():
            yield hypothesis('kept')
            ctx.environment['x'] = 0
            yield hypothesis('excluded', allowed={'e':('yes',)})
        result = Interpreter().interpret(m, ctx, source(),
            experiments=(Experiment('e','result',('yes','no')),),
            observations=(InterpretationObservation('e','no','lab'),))
        self.assertEqual(result.identification_status, 'undecided')
        self.assertFalse(result.candidates or result.rejected or result.excluded)
        self.assertTrue(result.diagnostics)
        self.assertEqual(result.simulations_used, 1)

    def test_realizer_rechecks_earlier_candidates_after_enumeration(self):
        ctx = Context(environment={'x':1})
        def source():
            yield model('first')
            ctx.environment['x'] = 0
            yield model('second')
        result = Realizer().realize(spec(), ctx, source())
        self.assertFalse(result.candidates or result.dominated or result.rejected)
        self.assertEqual(len(result.undecided), 2)
        self.assertEqual(result.simulations_used, 2)

    def test_late_metric_drift_is_not_a_pareto_candidate(self):
        first = model()
        def source():
            yield first
            first.metrics = ModelMetrics(99,99,99)
            yield model('second')
        result = Realizer().realize(spec(), Context(), source(), ResourceBudget(max_cost=2))
        self.assertEqual(tuple(i.model.name for i in result.candidates), ('second',))
        self.assertFalse(result.dominated or result.rejected)
        self.assertEqual(tuple(i.model.name for i in result.undecided), ('original',))
        self.assertEqual(result.simulations_used, 2)

    def test_advisory_probe_cannot_mutate_the_base_model_or_context(self):
        class ChangesInput:
            blocking = False
            def probe(self, m, s, c, evaluator, budget):
                c.environment['x'] = 0
                m.readout = lambda s,c: {'x':0}
                return ProbeOutcome(None)
        r = Realizer(probes=(ChangesInput(),)).realize(spec(), Context(environment={'x':1}), (model(),))
        self.assertFalse(r.candidates or r.rejected)
        self.assertTrue(r.undecided)

    def test_generator_drift_before_first_candidate_does_not_redefine_the_task(self):
        ctx=Context(environment={'x':1})
        class Source:
            def generate(self,*args):
                ctx.environment['x']=0
                return (model(),)
        r=Realizer().realize(spec(),ctx,Source())
        self.assertFalse(r.candidates or r.rejected)
        self.assertTrue(r.undecided)

    def test_finalization_does_not_repeat_simulation(self):
        calls=[]
        def transition(s,a,c):
            calls.append(1)
            return s
        r=Realizer().realize(spec(),Context(),(replace(model(),transition=transition),))
        self.assertEqual(len(r.candidates),1)
        self.assertEqual(len(calls),2)
        self.assertEqual(r.simulations_used,1)

    def test_context_drift_after_rejection_never_certifies_all_excluded(self):
        ctx=Context(environment={'x':1})
        m=replace(model(),readout=lambda s,c:{'x':c.environment['x']})
        def source():
            yield replace(hypothesis('false'),spec=spec(expected=0))
            ctx.environment['x']=0
        r=Interpreter().interpret(m,ctx,source())
        self.assertEqual(r.identification_status,'undecided')
        self.assertFalse(r.rejected)

    def test_model_drift_also_invalidates_observation_only_exclusions(self):
        m=model()
        def source():
            yield hypothesis('one',allowed={'e':('yes',)})
            m.readout=lambda s,c:{'x':0}
            yield hypothesis('two',allowed={'e':('yes',)})
        r=Interpreter().interpret(m,Context(),source(),
            experiments=(Experiment('e','result',('yes','no')),),
            observations=(InterpretationObservation('e','no','lab'),))
        self.assertEqual(r.identification_status,'undecided')
        self.assertFalse(r.excluded)
        self.assertEqual(r.simulations_used,0)

    def test_model_replacement_and_late_rejected_proof_also_need_rechecking(self):
        for change in ('readout','states'):
            m = model()
            def source():
                yield replace(hypothesis('false'), spec=spec(expected=0))
                if change == 'readout': m.readout = lambda s,c: {'x':0}
                else: m.states['s']['x'] = 0
            r = Interpreter().interpret(m, Context(), source())
            self.assertFalse(r.rejected or r.candidates)
            self.assertEqual(r.identification_status,'undecided')

    def test_late_hypothesis_change_invalidates_only_its_verdict(self):
        expected = {'a':1}
        from bidirectional_modeling import FieldRequirement
        goal = replace(spec(), objectives=(FieldRequirement('value','x','eq', expected),))
        h = replace(hypothesis('changed'), spec=goal)
        m = replace(model(), readout=lambda s,c:{'x':{'a':1}})
        def source():
            yield h
            expected['a']=2
            yield replace(hypothesis('stable'), spec=replace(goal, objectives=()))
        r = Interpreter().interpret(m, Context(), source())
        self.assertEqual(tuple(c.hypothesis.name for c in r.candidates), ('stable',))
        self.assertEqual(tuple(n for n,_ in r.undecided), ('changed',))
        self.assertEqual(r.identification_status,'undecided')


class ProbeContracts(unittest.TestCase):
    def test_flags_and_step_limits_are_explicit_types(self):
        for value in ('false', None, 0, 1):
            with self.assertRaises(TypeError): HorizonExtensionProbe(blocking=value)
            with self.assertRaises(TypeError): Counterexample('failure','failure',{},blocking=value)
        for value in (True, 1.5, '1', None):
            with self.assertRaises(TypeError): HorizonExtensionProbe(extra_steps=value)
        for value in (0,-1):
            with self.assertRaises(ValueError): HorizonExtensionProbe(extra_steps=value)
        class BadFlag: blocking = 'false'
        with self.assertRaises(TypeError): Realizer(probes=(BadFlag(),))

    def test_empty_required_probe_is_pending_and_empty_advisory_preserves_base(self):
        class Empty:
            def __init__(self, blocking): self.blocking=blocking
            def probe(self,*args): return ProbeOutcome(None)
        for required in (True,False):
            r = Realizer(probes=(Empty(required),)).realize(spec(), Context(), (model(),))
            self.assertEqual(len(r.undecided), int(required))
            self.assertEqual(len(r.candidates), int(not required))
            self.assertFalse(r.rejected)
            self.assertEqual(r.simulations_used,1)
            item=(r.undecided or r.candidates)[0]
            self.assertTrue(item.diagnostics)

    def test_completed_probes_have_distinct_verified_and_refuted_results(self):
        for expected in (True,False):
            class Checked:
                blocking=True
                def probe(self,m,s,c,e,b):
                    return ProbeOutcome(None,e.evaluate(m,s if expected else spec(expected=0),c,b))
            r = Realizer(probes=(Checked(),)).realize(spec(),Context(),(model(),))
            self.assertEqual(bool(r.candidates), expected)
            self.assertEqual(bool(r.rejected), not expected)
            self.assertFalse(r.undecided)


class ExactResources(unittest.TestCase):
    def test_exact_limit_round_trips_into_the_certificate(self):
        for limit in (2**53+1,Fraction(7,3),10**400):
            budget=ResourceBudget(max_cost=limit)
            cert=SatisfactionEvaluator().evaluate(model(),spec(),Context(),budget)
            self.assertEqual(budget.max_cost,limit)
            self.assertEqual(cert.max_cost,limit)
            self.assertEqual(type(cert.max_cost),type(limit))

    def test_integer_and_rational_cost_limits_are_not_rounded(self):
        n=2**53
        for cost,limit,passes in ((n+1,n,False),(n,n,True),
                (10**400+1,10**400,False),(Fraction(7,3),Fraction(2),False),
                (Fraction(7,3),Fraction(7,3),True)):
            m=replace(model(),metrics=ModelMetrics(cost,1,0))
            goal=replace(spec(),constraints=(ModelMetricRequirement('cost','cost','le',limit),))
            cert=SatisfactionEvaluator().evaluate(m,goal,Context(),ResourceBudget(max_cost=limit))
            self.assertEqual(m.metrics.cost,cost)
            self.assertEqual(cert.max_cost,limit)
            self.assertTrue(cert.complete)
            self.assertEqual(cert.satisfied,passes)
        self.assertEqual(ResourceBudget(max_cost=n+1).max_cost,n+1)

    def test_exact_complexity_distinguishes_pareto_models(self):
        n=2**53
        r=Realizer().realize(spec(),Context(),(
            replace(model('large'),metrics=ModelMetrics(1,n+1,0)),
            replace(model('small'),metrics=ModelMetrics(1,n,0))))
        self.assertEqual(tuple(i.model.name for i in r.candidates),('small',))
        self.assertEqual(tuple(i.model.name for i in r.dominated),('large',))

    def test_invalid_numeric_declarations_are_rejected_without_float_conversion(self):
        for value in (True,'1',None):
            with self.assertRaises(TypeError):ModelMetrics(value,1,0)
            with self.assertRaises(TypeError):ResourceBudget(max_cost=value)
        for value in (-1,float('nan'),float('-inf')):
            with self.assertRaises(ValueError):ResourceBudget(max_cost=value)
            with self.assertRaises(ValueError):ModelMetrics(value,1,0)
        with self.assertRaises(ValueError):ModelMetrics(float('inf'),1,0)
        self.assertEqual(ResourceBudget().max_cost,float('inf'))


if __name__ == '__main__':
    unittest.main()
