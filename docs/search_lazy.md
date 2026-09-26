# 候选级惰性预测（0.17.0）

`LazyExecutableSearch` 将可执行模型适配器接入统一查询接口。初始化只隔离输入、绑定声明并创建未知候选，不运行模拟。宏观查询按目录顺序逐个求值，找到异义见证后停止；低阶替代查询只求值目标及显式参考候选；约束查询只检查响应全集，不执行模型。

```python
from bidirectional_modeling import (
    LazyExecutableSearch, MacroAlternativeQuery, SearchWorkBudget,
    verify_query_result,
)

# protocol、candidates、cases 与 ExecutableSearchAdapter.prepare 的输入相同。
# world_answers 必须覆盖整个有限响应全集。
lazy = LazyExecutableSearch(protocol, candidates, cases,
                            target='output', world_answers=world_answers)
query = MacroAlternativeQuery('desired_answer', evidence)
result = lazy.execute(query, max_simulations=1000,
                      budget=SearchWorkBudget(10000))
check = verify_query_result(result.search, query, result.receipt)
```

返回 `LazyQueryResult`：`search` 是本次结果所绑定的有限问题快照，`receipt` 是三态查询回执，`simulations_used` 是本次调用消耗，`resolved_candidates` 是本次成功完成预测的候选，`batch_bindings` 是缓存内全部成功候选的双轮实验来源，`diagnostics` 记录本次失败或模拟预算中断。`declaration_fingerprint` 标识输入模型、案例、目标及回答映射的声明。

## 保守缓存和复核

每个执行的候选仍由 `ExecutableSearchAdapter` 完成两轮完整实验矩阵校验，检测模型声明变化、响应漂移和域外输出。只有完整成功结果进入缓存；失败或中断结果保持未知，下次可以重新尝试。原始调用者输入会被隔离，普通可复制配置的外部修改不影响已有实例。

复用同一实例可跨查询、观测撤回或重新校准复用预测，因为实验案例和模型声明固定，观测仅用于过滤。候选、实验、目标或回答映射变化时必须创建新实例；新实例不会继承旧缓存。执行前后发现实例内声明变化会抛出异常，要求重建。

已返回的有限问题快照不会随缓存增长改变。务必使用 `result.search` 复核 `result.receipt`；后续快照可能有不同指纹。复核保证有限目录中结论和见证的语义，不重新执行模型，也不认证来源元数据。声明指纹独立于有限查询指纹，不应将查询复核视为外部模型真实性证明。

`max_simulations` 是每次调用的模拟上限。`SearchWorkBudget` 控制查询操作，并在候选执行前检查取消；候选内部的完整矩阵运行是该接口的原子步骤，不支持中途取消。预算耗尽保留已验证的候选缓存，但不会把未完成的预测当作无解。共享工作预算的计数累计，重复有限查询也计入工作量。

## 限制和测量

这是候选级惰性执行，仍使用显式响应全集，且每个实际执行的候选仍计算全部案例。它没有实现单个候选的逐实验惰性推断、符号求解或持久化缓存。后端实例仅支持顺序调用。回调必须具有稳定语义；深拷贝和声明检查不能隔离未声明的外部服务、全局变量等副作用。第三方模型必须提供稳定的 `search_signature()`；无法绑定的声明在初始化时拒绝。

测试中两个单案例候选的完整准备需 4 次模拟；首个候选提供见证时只需 2 次，重复查询为 0 次。若必须证明无异义候选，仍可能需要全部模拟，并承担额外查询成本，因此不承诺普遍加速。

后续优先设计逐实验预测的可验证部分响应格式；在此之前，不使用未经完整校验的部分响应剪枝。
