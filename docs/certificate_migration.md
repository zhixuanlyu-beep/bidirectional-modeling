# 满足性证书格式与迁移

当前满足性协议标识为 `satisfaction-evaluator-v3`。`SatisfactionCertificate` 新增必填的 SHA-256 字段 `model_declaration_fingerprint`，与规范、观测证据、上下文、轨迹批次和成本上限一起进入协议指纹。`model_fingerprint` 仍标识有序的有限观测证据，两者承担不同的绑定检查。

`provenance.model_declaration_fingerprint(model)` 是公共声明指纹入口。精确的 `FiniteStateModel` 绑定名称、全部状态、初态顺序、动作顺序、cost/complexity/risk、假设、失败边界、能力、transition/readout/applicable 回调及实例 simulate 替换。回调使用确定性代码、默认参数、闭包和相关全局绑定；不能规范化的外部依赖需要显式的 `callback_semantic_id`。该 ID 是调用方的版本承诺：改变外部实现或隐藏配置时必须同步改 ID。它不证明任意外部代码的真实行为。

其他模型须提供返回规范化基本值/容器的 `search_signature()`，声明完整的行为配置；类型、名称和可读取的资源指标也参与绑定。只有场景清单而没有模型声明身份，不能发布完整满足性结论。未能核验声明身份时，本地评估返回未决，可靠批次消耗继续按原规则记账。Realizer 收到无法绑定当前声明的完整通过或失败时，返回未决，预留该回调的剩余额度并停止继续分配。

旧证书没有新字段，旧协议指纹也不能恢复新绑定。请丢弃旧满足性证书，并使用当前模型、规范、上下文和预算重新运行：

```python
from bidirectional_modeling import SatisfactionEvaluator, ResourceBudget

certificate = SatisfactionEvaluator().evaluate(
    model, spec, context, ResourceBudget(max_cost=10, max_simulations=1000)
)
assert certificate.binds_model(model)
```

需要搜索结果时重新运行 `Realizer.realize(...)`；请保存新的通过、失败或未决状态。不要只给旧证书补字段、重算哈希或把模型名当作声明身份。成本 50、上限 10 的模型重新评估会失败；便宜同名模型的旧通过回放会未决。

可执行搜索适配器仍提供原有的 `search_adapter.model_declaration_fingerprint` 导入位置，但实现来自 provenance，声明摘要增加 `model-declaration-v1` 域标识并覆盖实例 simulate 和可读取资源指标。因此需要重新运行 `ExecutableSearchAdapter.prepare(...)`，更新批次/声明关联与会话来源记录。双轮采集重放仍保留。有限搜索协议、冲突及查询回执的指纹格式未改；旧冲突证书只能在当前活跃证据下重新核验后使用。

证据快照是单次调用内部优化，不是新持久化格式。入口合法性扫描完成后，内部子查询复用已核验的证据子集；快照不保存到搜索对象、会话或证书。下一次调用重新校验，独立复核扫描原始声明。证据撤回不会保留旧快照的授权。预算截断、非空场景、覆盖权威、有限证明域和异常预留规则沿用原语义。
