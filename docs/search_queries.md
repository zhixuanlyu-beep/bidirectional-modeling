# 有限查询接口（0.16.0）

统一接口为未来惰性预测和符号后端提供扩展边界。目前实现仍要求显式有限响应全集及候选目录，不包含 SMT/SAT 求解器。

```python
from bidirectional_modeling import (
    ConstraintQuery, MacroAlternativeQuery, LowerSubstituteQuery,
    QueryStatus, SearchWorkBudget, verify_query_result,
)
from bidirectional_modeling.search_examples import conflict_search_scenario

problem, evidence = conflict_search_scenario()
query = ConstraintQuery(evidence=evidence)
receipt = problem.query(query, budget=SearchWorkBudget(10000))
verification = verify_query_result(problem, query, receipt)
assert receipt.status is QueryStatus.FOUND
assert verification.status == 'valid'
```

`problem.query` 使用问题配置的 `scan` 或 `indexed` 后端；`FiniteSearchQueryBackend.execute(problem, query, budget=...)` 提供相同能力。`SearchQueryBackend` 是扩展协议，外部实现可以遵循它，但结果不会自动触发剪枝或修改会话。

| 查询 | 寻找的见证 | ABSENT 的范围 |
| --- | --- | --- |
| `ConstraintQuery(commitments, evidence)` | 同时满足具名约束与观测的响应世界 | 已声明响应全集 |
| `MacroAlternativeQuery(answer, evidence)` | 与观测相容、宏观标签不同于 answer 的候选 | 已声明候选目录 |
| `LowerSubstituteQuery(candidate, lower_names)` | 完整响应与 candidate 相同的显式低阶候选 | 调用者指定的低阶目录 |

`lower_names` 是调用者对参考类的声明；接口不自动证明这些模型属于低阶，也不将结论推广到目录外。空参考类返回 `UNKNOWN`。候选完整响应未知时，除非找到已知见证，否则保守返回 `UNKNOWN`。

返回值 `QueryResult` 包含三态结论、查询指纹、适用范围、原因、工作计数以及可选见证。查询绑定包括协议、候选目录、目标、权威宏观映射，以及当前查询参数（包含观测来源）；切换计算后端不会改变绑定。修改候选或观测后，旧结果无法用于新查询。

宏观查询还返回 `compatible_catalogue_nonempty`：`True` 表示已找到相容候选，`False` 表示目录已穷尽且为空，`None` 表示未确立。只有 `ABSENT` 且该字段为 `True`，才能在已声明目录内解释为不存在异义候选；空目录本身不能确定答案。

## 独立复核与预算

`verify_query_result(problem, query, receipt, budget=...)` 返回 `valid / invalid / undecided`：

- `FOUND` 的见证由独立扫描实例重新检查；允许与默认搜索不同但有效的见证。
- `ABSENT` 必须由扫描实例重新穷尽查询，不能仅信任后端状态标志。
- `UNKNOWN` 不构成确定性证明，复核结果是 `undecided`。
- 绑定错误、错误范围、无效见证或复核分歧得到 `invalid`。复核预算耗尽或取消得到 `undecided`。

执行和复核都支持 `SearchWorkBudget`。预算是语义操作计数，不是墙钟超时或内存限制；问题构造、指纹计算和输入名称检查不计入工作计数。共享预算的工作计数为累计值。预算不足返回 `UNKNOWN`，不会伪装成无解。无效查询名称和域外观测会抛出异常；预算先耗尽时可能尚未完成观测校验。

结果是可重放的查询回执，不是简短的 SAT 无解证书。`reason` 和 `work` 是诊断信息，复核保证结论和见证的语义，不认证诊断信息。对外部后端结果，应先复核，再决定是否用于后续操作；当前接口不提供不受信任 JSON 的反序列化入口。

## 验证与后续方向

测试穷举双实验二值响应全集、全部 16 种约束子集和观测子集，对照直接真值表，并验证扫描/索引一致性。另覆盖所有预算截断点、取消、未知预测、空候选集、候选变化及伪造回执。

下一阶段可在此协议上增加惰性预测后端；需要继续保持未知状态、完整问题绑定和独立见证检查。符号无解证书的格式及验证器仍需另行设计。
