# 可验证的部分响应

`collect_partial_prediction` 只执行明确选定的实验；结果是模型预测，不能作为真实观测输入，也不能直接作为完整世界的见证。`verify_partial_prediction` 会独立重新执行所选实验矩阵，验证响应与两轮批次来源。

下面的示例可以直接运行：

```python
from itertools import product
from bidirectional_modeling import (Context, DescriptionLength, FiniteStateModel, MacroAlternativeQuery, ModelMetrics, ScenarioKey, SearchExperiment, SearchProtocol)
from bidirectional_modeling.search_lazy import (LazyExecutableSearch)
from bidirectional_modeling.search_adapter import (ModelSearchCandidate, ModelSearchCase)
from bidirectional_modeling.search_partial import (verify_partial_prediction)

protocol = SearchProtocol('demo', 'binary',
    (SearchExperiment('a', 'read y'), SearchExperiment('b', 'read y')),
    tuple(product(('0', '1'), repeat=2)))
model = FiniteStateModel('zero', {'s': {'y': '0'}}, ('s',), ('noop',),
    lambda state, action, context: state,
    lambda state, context: dict(state), ModelMetrics(1, 1, 0))
candidate = ModelSearchCandidate(model, DescriptionLength(relations=1))
cases = tuple(ModelSearchCase(name, Context(), ScenarioKey('s', 'baseline'), 'y')
              for name in ('a', 'b'))
lazy = LazyExecutableSearch(protocol, (candidate,), cases,
    target='output', world_answers=('low', 'mixed', 'mixed', 'high'))

first = lazy.predict_experiments('zero', ('a',))
assert first.simulations_used == 2
assert lazy.snapshot.hypotheses[0].world is None
check = verify_partial_prediction(protocol, candidate, cases, first.prediction)
assert check.status == 'valid'

# 扩展重放 a、b 两轮，共 4 次模拟；不会拼接独立验证的单实验结果。
complete = lazy.predict_experiments('zero', ('b',))
assert complete.prediction.experiments == ('a', 'b')
assert complete.simulations_used == 4
# 完整且通过约束检查的结果已进入查询缓存。
result = lazy.execute(MacroAlternativeQuery('high'), max_simulations=0)
assert result.receipt.status.value == 'found'
```

## 回执与验证

`PartialPrediction` 包含原始输入指纹、候选名称、按协议排序的实验名称、对应响应及双轮批次绑定。以及原始 `simulation_limit`；复核的 `max_simulations` 是独立执行上限，只要足够完成重放，无须与原始预算相等。缺失原始预算的旧对象返回 `undecided / missing_collection_protocol`，需重新采集；不会猜测原始资源协议。输入指纹覆盖完整协议、模型声明、候选描述/约束/材料和全部案例声明，包括此次未执行的案例。修改未执行的案例也会使旧回执的绑定检查失败。选择顺序会规范化为协议顺序；空集合、重复或域外名称会被拒绝。

收集返回 `PartialPredictionResult`，含 `prediction`、本次 `simulations_used`、原因和诊断。无法完成时 `prediction=None`，不发布半成品。投影响应全集会去重；部分回执仅表明响应属于该投影，不断言原始候选约束成立，也不确定尚未执行的坐标。

验证返回 `valid / invalid / undecided`。绑定错误、响应或来源伪造得到 `invalid`；重放预算不足、执行失败或漂移导致无法完成时得到 `undecided`。验证要重新运行模拟，并非廉价的指纹校验，也不能证明回调在所有未来调用中稳定。

## 缓存扩展和失效

`LazyExecutableSearch.predict_experiments(candidate, experiments, ...)` 保留每个候选最近一次完整验证的选定矩阵：

- 请求已覆盖的子集时，返回缓存的整个矩阵，可能包含额外实验，模拟次数为 0。
- 请求新实验时，对旧集合与新集合的并集完整执行两轮；不会拼接多个独立收集的回执。
- 预算中断或普通收集失败保留旧矩阵，不发布未完成的扩展。
- 新的成功响应若与旧部分响应或已完成的候选响应矛盾，实例失效，后续操作要求重建。已有缓存的候选在重放中检测到声明或响应漂移、或完整输出落到声明响应域外，也会使实例失效。
- 覆盖全部实验后，还须通过原始约束及权威宏观回答映射检查，才能更新有限查询缓存。违反约束抛出异常，两个缓存均不发布该扩展。

`execute` 完整求值时也会检查与已有部分响应的一致性。旧的已返回对象保持不变；它们是历史回执，不会被远程撤销。实例失效后 `snapshot`、`execute` 和 `predict_experiments` 都拒绝访问；已持有的历史对象仍可用于审计，但不代表当前有效结论。

## 预算与适用范围

收集和复核支持 `max_simulations` 与 `SearchWorkBudget`。取消检查发生在矩阵执行前；选定矩阵的双轮执行仍是原子操作，不支持内部取消。指纹和响应全集投影属于准备工作，不计入语义工作预算。缓存命中也检查工作预算和实例声明。

此次扩展提供显式逐实验预测入口；查询规划尚不自动选择实验。部分响应不会直接剪枝，也不会把响应全集中唯一可能的补全当作实际执行结果。仍需显式有限响应全集，不支持符号求解。回调与第三方 `search_signature()` 必须稳定且诚实，未声明外部状态的隔离限制与可执行适配器一致。

双实验示例只请求一个实验时需 2 次模拟，完整求值需 4 次；先请求一个再扩展到全部共需 6 次。扩展的重放成本用于保留跨实验漂移检查，因此按需执行并非在所有工作负载上更快。

证据限定的个体排除使用下节专用接口，并保留候选与证据绑定。


## 证据限定的个体筛查

新增 `LazyExecutableSearch.screen_evidence` 与 `verify_candidate_exclusion`。已认证的部分预测冲突可提前排除一个候选；部分吻合仍不构成完整响应。筛查结果携带观测依赖且不改变全目录，原有 `execute` 保持完整矩阵语义。详细验收及限制见 [情境网络文档](context_network.md#部分预测排除)。

筛查按成本排序并采用 1、2、4、… 个实验的倍增前缀，每批联合重放两轮。单场景、无缓存且全部吻合时，累计模拟次数小于 6k（k 为不同证据实验数）；四个实验由逐项扩展的 20 次降至 14 次，直接完整矩阵仍只需 8 次。首个实验冲突仍只需 2 次。这里减少重复扩展成本，不保证筛查始终优于一次完整求值。


筛查在公共调用开始和结束时各核验一次完整候选目录声明；内部批次继续执行部分预测自身的绑定与双轮重放检查。
若其他候选在筛查期间被回调改变，结束检查使整个实例失效且不返回筛查结论。此后 snapshot、预测与查询均拒绝访问。
该优化减少全目录指纹重复计算，不承诺所有准备工作均为线性；完整预测提升仍需验证候选目录。

完整与部分预测共享双轮矩阵采集。部分响应必须能扩展为声明响应全集中的至少一行；检查受工作预算约束，失败或耗尽时不发布预测。此条件只证明局部响应处于协议域内，不验证候选的全部承诺。


完整响应升级为候选时，承诺核验失败返回 prediction_declaration_invalid 与 diagnostics，完整搜索快照和此前部分缓存保持不变。screen_evidence 将该候选列为 undecided 并继续筛查其他候选；该诊断不是实验反例，不生成排除证书。
