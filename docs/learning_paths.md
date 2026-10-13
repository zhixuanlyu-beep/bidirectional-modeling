# 三条学习路径

## 首次使用

先读 README 的最小搜索，运行 `search-demo`，认识 compatible、rejected、pruned、undecided 和宏观答案 determined 的区别。然后读[允许结果集合](set_interpretation.md)：保留候选不表示概率可信度，答案唯一不表示结构唯一。

接着运行[审批工作流评估](application_evaluation.md)的完整证据生命周期，查看 action_path、predicted、actual 三列反例。只在需要执行模型时读[有限状态重放](finite_replay.md)。这一流程中不需要概念记忆、拼接或布尔扩展。

需要实际枚举预设架构时，运行 `computation-demo` 并读[自主计算搜索](autonomous_computation.md)。示例区分“符合已知样例”“完整有限目标通过”“排除全部更低成本方案”，并说明初态装载和无限运行候选怎样计费。

## 应用集成

按[查询接口](search_queries.md)声明有限响应域、候选域、目标答案与未知预测；在 [惰性预测](search_lazy.md)和[部分响应](search_partial.md)之间选择采集方式。将预测和外部观测分别保存，观测来源改变时调用 `search_session.SearchSession.replace_evidence` 并处理事务中断。

协议变化时读[情境转换](context_network.md)并在目标域重证；长期归档选择[有限状态重放](finite_replay.md)，或者明确接受外部回调只能归档离散声明的边界。性能问题先运行[全成本基准](performance_lifecycle.md)，根据实际开销选择索引、缓存或调整受控规模。

## 语义与证明边界

先读[可验证的语义委托](semantic_delegation.md)，区分可追溯性、方案正确、判断保持及方法完备，理解目的拓扑中的共享资源和反馈。运行 `abstraction-demo`，再读[有限任务抽象](task_abstraction.md)；排班示例区分新验收标准和同一标准下的模型细化，组合示例说明静态判断充分仍可能不能独立演算。

以[核心契约](core_contracts.md)为主线，逐项查看[契约映射](core_contracts.json)中的独立真值表、状态机、错误注入和预算截断测试，再读[完整用法](usage.md)的对应、闭合及残差边界。格式演进遵循[版本政策](version_policy.md)与[证书迁移](certificate_migration.md)。

修改规则时先标出契约编号及受影响模块，再添加有区分能力的回归；内部优化不能让 UNKNOWN 变成失败或不存在。领域扩展仅在任务确实需要该领域时阅读，通用学习路径不以演示数量为目标。
