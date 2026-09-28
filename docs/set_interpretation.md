# 基于允许结果集合的解释

默认解释链不使用先验、预测概率、贝叶斯更新或熵。候选必须先通过对应宏观规范的确定性验证；额外实验观测只能排除违反显式响应承诺的候选。结果不提供目的为真的概率或综合可信度。

## 声明与未知

`Experiment(name, question, outcomes, cost=0)` 必须声明有限的结果域。
`PurposeHypothesis.allowed_outcomes` 将实验名映射到非空允许结果集合；单元素集合表示确定承诺。缺少某实验声明表示未知，计算时保留该实验域内所有结果，不填 0.5、不生成均匀分布。空集合、重复标签、概率数值和域外标签被拒绝。

允许集合是调用者明确提出的待检验约束，不能仅凭历史样本中未出现某结果，就自动把该结果认定为不可能。演示中的集合是假设性的硬承诺，未从旧概率阈值转换，也不表示真实组织必然遵守这些规则。

## 观测过滤与撤回

```python
from bidirectional_modeling import (Experiment, InterpretationObservation, PurposeHypothesis, PurposeLevel)

experiment = Experiment("probe-v1", "结果是什么？", ("0", "1"), cost=1)
# spec 是调用者提供的 MacroSpec；model/context 与它的验证域相对应。
hypotheses = (
    PurposeHypothesis("A", PurposeLevel.FUNCTION, spec,
                      allowed_outcomes={"probe-v1": ("0",)}),
    PurposeHypothesis("B", PurposeLevel.FUNCTION, spec,
                      allowed_outcomes={"probe-v1": ("1",)}),
    PurposeHypothesis("C", PurposeLevel.FUNCTION, spec),
)
observation = InterpretationObservation("probe-v1", "0", "lab-record-v1")
result = engine.interpret(
    model, context, hypotheses, experiments=(experiment,),
    observations=(observation,),
)
# 若三个候选的规范都通过：A、C 保留，B 及其冲突观测列入 result.excluded。
```

解释调用不修改原候选集合。撤回观测后重新调用即可重新考虑候选；旧结果保留当次诊断；排除记录保存候选规范指纹、情境指纹、实验声明、允许集合与冲突观测；嵌套规范的修改仍须遵守下文的快照边界。
同一调用中，每个实验实例只允许一个结果；重复试验或情境变化需要新的实验实例/版本名。
域外或相互矛盾的观测报错，不被当成候选失败。

`source` 是调用方的来源声明，不认证实验真实性。排除记录不是可跨协议传递的证明证书；改变实验语义或上下文必须重新提供适用的声明和观测。

## 实验选择政策

对每个尚未观测的实验，按允许结果集合对候选分组；相同响应声明只计一类，避免重复候选改变权重。计算每个可能结果会排除多少类，再取最小值：

- `guaranteed_class_eliminations`：最坏结果下仍会排除的响应类数；
- `separated_class_pairs`：允许集合互不相交的响应类对数；
- `selection_score`：前者除以 `1 + cost`，只用于实验调度。

按调度分数、分离类对数、较低成本、实验名字顺序破同分。若没有实验保证排除任何类，则不提出实验；这不表示候选已被识别。域内但所有候选都禁止的结果一旦实际发生，会排除全部候选，表明需要重构候选空间。

这些计数不是概率、熵或最优性证明。该解释层区分声明响应类；`ExperimentHypothesisSearch.next_experiment` 仍是面向宏观异义响应对的另一种明确任务政策。

`equivalent_explanations` 仅列出在全部给定实验上具有相同显式允许集合的候选。未知声明不作为等价证据；相同声明也不证明真实结构相同或对未声明实验等价。

## 分开展示验证与证据

候选按名字稳定展示，不按信念程度排序。证书提供 `verification.coverage` 场景覆盖率；探测证书的最小覆盖率可通过 `CandidateEvaluation.verification` 获取。具体数值要求的 `CheckResult.margin` 和 `tolerance` 保留原单位，不跨要求合成为稳健度或可信度评分。

`Realizer` 的默认帕累托比较仅使用显式 `ModelMetrics`，不使用声明可靠度或验证分数。指标语义仍由任务方负责；例如 risk 不应隐含为未经声明的概率。

解释结果另列规范要求数、原始证据和直接主体/设计类证据。证据强度只作为原始记录保留，不累加为支持分数，也不取消“行为相容不能证明意图”的提示。空候选空间或预算截断都保持不可识别。

明确编码下的描述长度、集合基数以及用户显式选择的数值聚合仍可使用；它们不是关于底层结构真实性的统计保证。

## 识别状态与未决诊断

`identification_status` 是唯一的状态实现；`non_identifiable` 由它派生，不再接受构造参数。

| 状态 | 含义 |
| --- | --- |
| `unique` | 目录完整验证后恰好保留一个候选 |
| `ambiguous` | 完整验证后保留多个候选 |
| `all_excluded` | 已检查候选均被观测或确定性验证排除 |
| `empty_catalogue` | 没有候选且没有排除记录 |
| `undecided` | 存在未完成验证或目录/执行预算截断 |

`unique` 仅相对于本次候选目录、规范、情境和观测，不证明世界中不存在其他解释。`undecided` 优先于候选数量：一个已验证候选加一个检查器异常，仍然未决。

`excluded` 保存观测冲突；`rejected` 保存完整验证不通过的证书；`undecided` 保存无法完成验证的候选及原因。检查器异常通过 `CheckResult.evaluation_error` 保留，证书不得标为 complete。存在未决或截断时，不提出声称覆盖整个目录的实验建议。

## 往返目标与观测

`micro_round_trip(..., selected_hypothesis="A", observations=(observation,))` 显式选择已验证相容候选。省略目标仅在解释为 `unique` 时有效；歧义或未决时抛出 ValueError，不能用名字排序决定验证哪项任务。报告的 `selected_hypothesis` 记录目标。

显式选择时，往返通过只表示所选规范下的任务成功；其他候选仍可能未决，不因此宣称目录唯一。`macro_round_trip(..., observations=...)` 同样传入观测；预算截断或存在未决候选时，不报告语义恢复成功。

## 复制与审计导出

假设与实验建议的 `allowed_outcomes` 在构造时复制为不可变映射，内部结果值也是元组，支持 `copy.deepcopy` 与 `dataclasses.asdict`。修改原始字典或列表不会改变声明或既有调度指标。

`PurposeHypothesis.to_dict()`、`DiscriminatingQuery.to_dict()`、`InterpretationResult.to_dict()` 导出独立普通数据，可交给 `json.dumps`。解释导出包含状态、观测、排除/未决诊断、候选规范与证书绑定指纹，不包含模型、规范回调或完整可复核证明对象，因此没有反序列化恢复执行接口。

不可变保证针对允许集合，未把整个模型与嵌套规范改成深度冻结对象。导出前会检查候选规范是否仍与证书绑定；规范修改后必须重新验证，不能混用旧证书和新规范。


宏观往返只报告 `compatibility_passed`；`generation_source` 记录生成路径，`independence_declared` 仅转述调用方声明，二者都不是独立恢复证明。已删除宏观报告的 `passed`。同批轨迹生成并验证效果只是有限域检查，不是留出实验。
