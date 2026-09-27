# 变更记录

版本记录描述各版本引入时的行为；当前使用方式以 README 和专题文档为准。

## 0.21.0

本版合并 PR #14 的解释状态修复及后续语义审查修复；不保留被删除接口的兼容层。

- 数值条件以精确整数/有理比较决定真假；浮点数按其实际二进制值参与，显式 tolerance 才放宽判断。大整数不再因转为浮点数被误判相等。
- EquivalenceSpec 与残差商、闭合、对应共用严格结构身份；未分桶的 True、1、1.0 区分类型，声明数值分辨率后才按桶归类。
- RealizationResult 新增 undecided，探针未完成不能进入候选；VerificationIssue 与 Counterexample 分开。闭合错误只进入 diagnostics；组合规则按操作反例拒绝，其余未认证情况保留未决。
- 删除 CheckResult/VerificationMeasures 的 robustness；条件返回原单位 margin 与 tolerance，证书仅报告场景覆盖，不聚合不同要求的余量。
- 删除 MacroRoundTripReport.passed 和 independent_recovery；分别报告 compatibility_passed、generation_source、independence_declared。生成器声明无法构成独立恢复证明。
- 组合验证默认保留全部 certified 规则，不要求编码长度、不执行描述长度选择；selection_policy="shortest_description" 显式启用选择。移除 exception_penalty 和异常描述长度。
- ConceptLibrary 移至 extensions.concepts，通用调用不加载；显式使用时保存判断来源与版本历史，诊断不能写入反例。删除顶层与 refinement 模块的旧导入。

PR #14 的配套改动：

- 检查器异常记录为 evaluation_error 和未决诊断，不再作为反证排除候选；未决或预算截断不能产生唯一性结论或全目录实验建议。
- InterpretationResult 的 identification_status 统一派生五种状态；non_identifiable 改为只读派生属性，删除对应构造参数，不提供兼容入口。rejected 与 undecided 分别记录验证失败和未完成验证。
- micro_round_trip 仅在唯一解释时自动选择目标；歧义或未决时须以 selected_hypothesis 指定已验证候选，报告记录所选名称。两个往返接口都接受 observations。
- 宏观往返不再把空的预算截断结果标为已识别；存在未决候选时不报告语义恢复成功。
- 假设和实验建议的允许集合采用不可变、可复制表示；新增 to_dict 审计导出，包含声明指纹与诊断，不序列化可执行回调。导出解释前检查规范绑定。

详细状态、显式选择和快照边界见 [集合解释](docs/set_interpretation.md)。

## 0.20.0

默认解释改用允许结果集合和观测相容性过滤。未知声明不补概率、不推导均匀分布；实验显式声明结果域，并按最坏结果下可排除的响应类数与成本调度。同声明候选按响应类计数，保留结构代表。旧概率没有自动转换为集合。

不兼容 API 调整：
- PurposeHypothesis 删除 prior / predictions，改用 allowed_outcomes；Experiment 必须提供 outcomes。
- 新增 InterpretationObservation；Interpreter/engine.interpret 接受 observations，报告 excluded 及其依赖观测。
- 删除 InterpretationScoringPolicy、normalized_entropy、ranking_score、confidence 别名和 verification_score。
- ConfidenceBreakdown 改为 VerificationMeasures，证书字段改为 verification，只含 coverage / robustness；探测验证逐维报告。
- 删除模型 prior_reliability，默认帕累托仅比较显式 ModelMetrics。模型声明指纹相应变化，旧可执行模型适配回执需重建。
- CLI JSON 用 compatible_hypotheses、ordering_policy、allowed_outcomes 和响应类区分计数替代概率/评分字段。
- 意图证据原样列出，不合成为置信度或按强度解除解释边界；空/截断候选空间保持不可识别。

详细语义与迁移示例见 [集合解释](docs/set_interpretation.md)。有限确定性证明、显式描述长度和已有搜索接口保持原范围；没有把集合声明当作真实世界完备性证明。

## 0.19.1

- 布尔搜索与复核提前验证所有声明变量的输入类型，非法未读取变量不再因枚举顺序被接受。
- 扫描约束查询逐行寻找见证，宏观异义查询逐候选检查；FOUND 复核只检查给定响应行，不建立索引或重建问题。ABSENT 仍独立穷尽复核。
- 筛查的全目录声明检查移到公共调用前后，保留部分预测自身重放与绑定核验；结束时检测到跨候选漂移则整个实例失效，不返回筛查结论。
- 转换关系按源响应投影建索引，构建、查询与关系输出均计入工作预算；稠密多对多关系的输出成本仍保留。
- 新增输入顺序、AST/模型语义一致性、冷/热缓存、跨候选漂移、直接复核和关系索引规模回归，以及可复现成本脚本。

## 0.19.0

`0.19.0` 将固定协议中的搜索扩展到可验证的情境变化：新增情境网络、跨协议冲突重证与会话迁移、有限局部拼接、部分预测个体排除、有界布尔 AST 重构及低阶语言覆盖，以及可只读取保留证据复核的宏观充分性证书。运行 `bidirectional-modeling context-demo --json` 查看六项贯通验收，接口、证明范围和兼容性见 [情境网络与结构重构](docs/context_network.md)。

本轮精简：布尔替代流式搜索与见证直接复核；筛查按倍增前缀重放；迁移内复用转换关系；低阶判断统一使用 LowerSubstituteQuery；布尔与拼接迁入可选 extensions；闭合与残差共享审计状态探索。移除本轮兼容层，领域与基准接口仅从各自模块导入。

### 不兼容 API 清理

- 删除 `boolean_reconstruction` 和顶层 `gluing` 模块；分别改用 `extensions.boolean`、`extensions.gluing`。
- 删除顶层布尔/拼接符号、`ContextNetwork` 和基准符号别名；分别从扩展、`context_network`、`search_benchmark` 导入。
- 删除 `certificate_transport` 的会话迁移别名；使用 `search_session.migrate_session` 或 `SearchSession.migrate_context`。
- 删除 `irreducible_against`；使用 `query(LowerSubstituteQuery(...))`，根据 FOUND / ABSENT / UNKNOWN 判断。重复低阶名称遵循查询校验规则，预算中断返回 UNKNOWN。
- search-demo JSON 将 `irreducible_against_x_and_z` 布尔字段替换为 `lower_substitute_status` 查询状态字段。

## 0.18.1

`0.18.1` 修复审查发现的跨接口问题：域外预测使已有缓存失效，失效实例不再提供当前快照；部分预测记录原始采集预算，允许独立设置复核执行预算；惰性查询批量发布已完成预测，消除逐候选全目录重扫。兼容性与规模回归结果见 [修复说明](docs/search_review_fixes.md)。

## 0.18.0

`0.18.0` 增加按实验选择的部分预测与独立重放验证；惰性后端可缓存选定实验矩阵，扩展时重放整个合并集合，并检测与已有结果的冲突。部分结果保持未知候选，覆盖全部实验且满足约束后才进入查询缓存。见 [部分响应文档](docs/search_partial.md)。

## 0.17.0

`0.17.0` 新增候选级惰性预测：按查询需要执行候选，找到见证后停止；仅缓存完成双轮完整实验校验的预测，预算中断不会发布部分结果。重复查询复用缓存，输入变更使用新实例。详见 [惰性预测文档](docs/search_lazy.md)。

## 0.16.0

`0.16.0` 新增统一查询接口：约束相容性、宏观异义候选、显式低阶候选替代。扫描与索引后端统一返回 `FOUND / ABSENT / UNKNOWN`，支持工作预算、取消与独立扫描复核。结果绑定问题和查询；空候选集不会被当作答案确定。使用方式与适用范围见 [查询接口文档](docs/search_queries.md)。

## 0.15.0

`0.15.0` 缓存不可变协议/问题指纹，并在候选新增、重构时复用同协议的只读完整索引。动态基准覆盖候选增长、证据撤回、重新校准，逐阶段对照完整扫描，报告重复试验的中位耗时和失效记录；运行 `bidirectional-modeling search-updates-benchmark --json`。

## 0.14.1

`0.14.1` 修复跨模块语义一致性：目标映射贯穿新增、重构和会话恢复；搜索问题公开配置只读；模型适配固定配置副本、复核声明并重放完整实验组。会话 schema 2 保存目标表，并可关联模型声明与两轮批次来源。

## 0.14.0

`0.14.0` 增加可选的精确位集合索引后端和声明式 `ReconstructionRule`。索引按需构建且计入共享预算；扫描后端保留为对照。重构显式撤回/添加承诺，先验证新候选再更新会话。基准新增包含首次建索引开销的第四种策略。

## 0.13.0

`0.13.0` 接通可执行模型与假设搜索：复用批次覆盖/绑定验证并重复采集检查响应；增加可保存、恢复和撤销证据依赖的 `SearchSession`，以及完整重放、失败模型缓存和冲突复用的对照基准。运行 `bidirectional-modeling search-benchmark --json` 可查看净开销与误剪检查。

## 0.12.1

`0.12.1` 为搜索、冲突证明、宏观证书验证与实验选点增加可共享的语义操作预算和协作取消；`verify_macro` 分开报告证据充分性与最小性，预算耗尽保留已完成的证明。搜索报告明确区分全目录商、存活候选商、已观测实验分组，并列出停止原因与逐项工作量。

## 0.12.0

`0.12.0` 增加实验限定的候选搜索层：先按全部允许实验的响应取商并保留结构代表，再按整套描述复杂度调度；从失败中提取可重放的共同冲突核，只剪除仍继承该矛盾的候选。新增宏观答案可识别性检查、最小证据子集、按宏观分歧/成本选实验和未决预算状态。详细定义与有限域边界见 [实验限定搜索](docs/experiment_relative_search.md)。

## 0.11.0

`0.11.0` 把同一证据链扩展到满足性验证和残差商。`TraceBatch` 现在绑定模型观测证据、上下文、时间范围、模拟上限、覆盖权威及批次结果；`evaluate_batch` 在 requirement 执行前后复核绑定，不能再把旧批次跨上下文或 horizon 重放。`SatisfactionCertificate` 进一步绑定具体规范、批次和资源协议，因此同名但语义不同的规范不能复用旧证书。`ResidualQuotientReport` 则绑定有界状态/转移证据、等价关系和全部搜索界。上下文与轨迹在回调边界深度隔离，原地修改不会污染调用方证据。

## 0.10.0

`0.10.0` 将跨尺度证明绑定到具体对应声明、上下层观测轨迹、上下文和验证协议：替换同名投影、场景映射或证据后，旧证书不能再加入 `ScaleGraph`。投影回调会在隔离副本上重放；输入修改不会泄漏，非确定输出或验证期间变化的身份会失败关闭。

## 0.9.1

`0.9.1` 加固证明所依赖的语义底座：上下文、宏观规范、效果生成、闭合分析与残差分析共享同一个严格结构编码器，不再以对象的 `repr` 充当身份；有限状态模型在回调边界深度隔离状态，证明型分析会重放读出和转移以拒绝非确定性结果。初态也受 `max_states` 约束，非有限的度量或容差值以及负状态索引会显式失败。

## 0.9.0

`0.9.0` 加入候选微观组合规则选择：多个 `CompositionRule` 必须接受同一组、由实验方持有的操作观察；错误观察结果、错误支撑和运行异常都会生成阻断反例。通过测试的规则还必须得到完整、稳定、同余且可由有限上下文基重建的残差商，之后才按“规则 + 语义状态 + 偏转移表 + 区分上下文 + 异常”的显式描述长度排序。

## 0.8.0

`0.8.0` 把局部支撑集提升为一等语义：`FiniteStateModel.applicable` 显式声明动作何时无定义，`UndefinedTransition` 表示合法的 `⊥`，普通运行异常仍作为未知边阻断证明。残差报告还用反例循环提取有限区分上下文基，每次只加入一个能严格细化当前测试分区的最短上下文。

## 0.7.0

`0.7.0` 加入有限确定性系统上的残差语义商发现：框架从初态枚举可达微观状态，以动作序列作为区分上下文，逐层细化观察等价类，生成最短区分上下文、商转移和完整性/稳定性/同余性证书。只有完整探索并收敛到同余分区时，结果才声明为当前实验域上的最小行为模型。

## 0.6.0

`0.6.0` 进一步把证书绑定到规范化的上下文指纹，并引入校准/留出验证套件：在已知场景上相容只产生 `compatibility_passed`，只有全部用例通过且至少包含一个声明为独立来源的留出用例，套件的 `passed` 才为真。

## 0.5.0

从 `0.5.0` 起，尺度之间的状态投影不再只隐含在模型读出中：`Correspondence` 把粗粒化映射和场景映射声明为一等对象，`CorrespondenceValidator` 用上下层两个可执行模型检查动态交换图是否成立。

## 搜索层历史细节

以下保存早期版本的接口演进和示例，包含后来被替代的实现说明。

## 0.12.1：预算、报告与兼容性

所有公开搜索/验证/选点方法接受 `budget=SearchWorkBudget(...)`，默认每次调用创建最多 1,000,000 个语义操作的新预算。传入同一个实例可跨多次调用累计，结果中的 `work` 是不可变的累计快照，不会随下一次调用而变化。

```python
from bidirectional_modeling import SearchWorkBudget
from bidirectional_modeling.search_examples import conflict_search_scenario

search, data = conflict_search_scenario()
budget = SearchWorkBudget(max_operations=10_000)
report = search.search(data, budget=budget)
assert report.determined
assert report.full_quotient == (('x',), ('z',), ('xz',))
assert report.surviving_quotient == (('xz',),)

basis = search.compress_evidence(data, budget=budget)
validation = search.verify_macro(basis, data, check_minimality=False, budget=budget)
assert validation.sufficiency == 'valid'
assert validation.minimality == 'not_checked'
# 只验证了充分性，尚不能确认该证书声称的最小性。
assert not validation.valid
```

### 工作量与取消

| 计数 | 含义 |
| --- | --- |
| `world_queries` | 启动一次响应宇宙过滤，在分配过滤集合前计数 |
| `response_checks` | 检查数据标签是否允许、比较响应是否符合观测 |
| `constraint_checks` | 检查响应行是否满足约束 |
| `candidate_checks` | 调度候选、收集宏观答案、检查目录成员 |
| `certificate_checks` | 启动证书验证/学习，或检查一个候选是否继承冲突 |
| `partition_checks` | 读取一个候选在一个实验上的分组响应 |
| `pair_checks` | 在一个实验上比较一个候选对 |
| `subset_checks` | 尝试一个证据子集 |

`work.total` 为上述计数之和，预算在执行对应操作前扣除，因此不会超过 `max_operations`。这是算法语义操作上限，**不是严格时间或内存上限**：输入构造、目录/约束索引构建、集合分配、排序、指纹编码和结果组装没有按 CPU 指令计费。大输入仍需要有界构造器与后续索引/求解后端。

通过 `SearchWorkBudget(cancelled=lambda: should_stop)` 提供协作取消信号，每次语义操作前检查。预算一旦取消或耗尽就保持停止；需要继续时创建新预算，并重放该操作。此版本不提供中途检查点恢复。

`search` 捕获预算中断，保留已完成的相容、拒绝与剪枝结论，未完成候选进入 `undecided`。如果失败模型已经重放完毕，但冲突核提取尚未完成，保留该模型的拒绝结果，丢弃尚未完成的证书。`verify_macro` 则保留已经验证的充分性。

其余返回简单值的方法（包括 `learn_conflict`、`validates_conflict`、`partition`、`compress_evidence`、`next_experiment`、`macro_identifiable`、`irreducible_against`）在操作预算中断时抛出 `SearchBudgetExceeded`，异常包含 `reason` 和 `work`。不能把异常转换为“模型失败”“没有可做实验”或“不可约性已证明”。`compress_evidence(max_subsets=...)` 的子集上限仍沿用旧行为：返回原始证据的有效上界；共享操作预算耗尽则抛出异常。

### 充分性与最小性分开报告

`verify_macro(certificate, data, check_minimality=True, max_subsets=10000)` 返回：

- `sufficiency`：`valid` / `invalid` / `undecided`。
- `minimality`：`valid` / `invalid` / `undecided` / `not_checked` / `not_claimed`。
- `stop_reason`、本次 `subsets_checked` 与累计 `work`。

最小性预算耗尽时，可以同时有 `sufficiency='valid'` 和 `minimality='undecided'`；这不是证书充分性失败。找到更小子集时最小性为 `invalid`，充分性仍可成立。只有充分性通过，而且声称的最小性也通过（或没有声称最小性），`valid` 才为真。

兼容入口 `validates_macro` 仍返回布尔值，但默认验证有界。其 `False` 同时可能表示失败和未决，不能用它判定证据必然错误；需要原因时改用 `verify_macro`。`check_minimality=False` 可避免指数级最小性搜索，但仍重查数据绑定与证据充分性。

### 商空间和停止原因

- `full_quotient`：完整候选目录在全部允许实验上的分组；旧字段 `quotient` 保留相同含义。
- `surviving_quotient`：上述分组限制到相容和未决候选。
- `observed_quotient`：相容和未决候选仅按已经观测的实验分组。它可以被下一次实验细化，不是理论全域等价。
- `experiment_domain` / `observed_experiments`：分别说明这两种实验范围。
- `partition_complete`：两种分组是否都已完成。分组过程中耗尽预算时，该值为假，空字段不能被解释为候选已经全部排除。
- `undecided_reasons`：逐个候选记录未知预测、候选重放预算耗尽或共享工作预算/取消导致的未决。

`stop_reason` 区分 `determined`、`macro_ambiguous`、`no_compatible_candidate`、`inconsistent_evidence`、`unknown_predictions`、`replay_budget_exhausted`、`work_budget_exhausted` 和 `cancelled`。其中响应宇宙中不存在与数据相容的行才是 `inconsistent_evidence`；数据在宇宙中允许、但没有候选符合时是 `no_compatible_candidate`，应考虑扩展目录。

`max_replays` 仍只限制候选重放数。即使设为零，输入检查与分组仍可能发生；若要限制整个操作流程的语义工作量，请同时传入共享 `SearchWorkBudget`。

## 0.13.0：可执行模型、会话与对照基准

### 从模型生成响应表

`BidirectionalModelingEngine.prepare_hypothesis_search` 使用引擎已有的 `SatisfactionEvaluator`；也可以直接使用 `ExecutableSearchAdapter`。

```python
from bidirectional_modeling import (
    BidirectionalModelingEngine, Context, DescriptionLength, FiniteStateModel,
    ModelMetrics, ModelSearchCandidate, ModelSearchCase, ScenarioKey,
    SearchExperiment, SearchProtocol, SearchObservation, SearchSession,
)

model = FiniteStateModel(
    name='constant', states={'s': {'y': '0'}}, initial_states=('s',),
    actions=('noop',), transition=lambda s, a, c: s,
    readout=lambda s, c: dict(s), metrics=ModelMetrics(1, 1, 0),
)
protocol = SearchProtocol('exact binary output', 'code-v1',
    (SearchExperiment('read', 'read final y'),), (('0',), ('1',)))
case = ModelSearchCase('read', Context(), ScenarioKey('s', 'baseline'), 'y')
prepared = BidirectionalModelingEngine().prepare_hypothesis_search(
    protocol, (ModelSearchCandidate(model, DescriptionLength(relations=1)),),
    (case,), target='output class', world_answers=('low', 'high'),
)
assert prepared.simulations_used == 2
assert not prepared.diagnostics

# 只有独立实验数据才成为 evidence；模型预测不会自动写入证据。
session = SearchSession(prepared.search, (SearchObservation('read', '0', 'lab'),))
assert session.run().determined
```

每个 `ModelSearchCase` 固定上下文、场景、时域和末步读出字段；必须按顺序覆盖协议的完整实验域。响应标签必须是字符串。`world_answers` 为响应宇宙的每一行定义目标答案，适配器据实际响应计算候选答案，避免逐模型手填标签。目标定义和读出协议分别绑定到目标/实验语义指纹。

每个候选、每个用例收集两份完整 `TraceBatch`，检查批次绑定与两次观测模型指纹相同。未知场景、不完整覆盖、响应超出宇宙、无效承诺、运行异常或重复采集不一致都会生成诊断，候选保留为 `world=None`；不会当作实验反例。未决候选不携带未经适配确认的承诺。

`max_simulations` 在全部候选、用例和两次采集之间共享。若自定义收集器抛出异常，无法确认其实际消耗，剩余额度全部预留，因而 `simulations_used` 在该情形是保守计费而非精确完成次数。操作预算与采集预算仍是两种不同资源。

`batch_bindings` 返回每个候选/用例的两份批次协议指纹，便于外部归档关联。重复采集只能检查本次有限域内的可重复性，不证明任意未来行为。当前没有跨调用预测缓存，不能用同名但已修改的可执行模型冒用旧预测；应重新执行适配。会话只保存适配后的离散声明，不保存原始模型代码或完整轨迹。

### 可恢复的证据会话

```python
from bidirectional_modeling import SearchSession
from bidirectional_modeling.search_examples import conflict_search_scenario

search, data = conflict_search_scenario()
session = SearchSession(search, data)
session.run()                       # 提取并保留共同冲突证书
session.save('search-session.json') # 同目录临时文件 + 原子替换
restored = SearchSession.load('search-session.json')
assert restored.run().compatible == ('xz',)
revoked = restored.replace_evidence(data[:1])
assert revoked                     # 旧反例不再受新证据支持
assert len(restored.run().compatible) == 3
```

会话 JSON 使用版本化 schema、完整问题指纹与内容校验和。加载时重新构造受验证的数据对象并验证冲突证书；不反序列化 Python 回调，不执行任意模型代码。`load` 默认限制输入为 5 MB；`from_json` 面向已在内存中的字符串。校验和用于损坏检查，不是真实性签名。

`replace_evidence` 先完成证据检查和证书再验证，再更新状态；中断时保持原会话不变。事件记录旧/新证据指纹及失效证书标识。`add_hypothesis(h, parent='old')` 保留旧候选并记录共同保留、撤回和新加的承诺；它记录领域提供的重构，不自动生成重构。事件历史用于审计，不充当证明。

搜索过程中耗尽预算不会删除尚未完成复核的旧证书；每次实际剪枝前仍然重新验证。扩大候选目录改变问题指纹，旧宏观证据证书不能直接复用；固定协议下的冲突证书仍可重新验证使用。并发写入同一会话文件目前采用最后一次替换的结果，不提供多进程合并或数据库事务。

### 比较净开销

运行 `bidirectional-modeling search-benchmark --json`，或调用：

```python
from bidirectional_modeling import benchmark_search
from bidirectional_modeling.search_examples import conflict_search_scenario
search, data = conflict_search_scenario()
result = benchmark_search(search, data, rounds=5)
assert all(row['agrees_with_reference'] for row in result['results'])
```

三种策略使用相同初始候选目录、固定证据、轮数与每种策略的累计操作预算：

1. `full_replay`：每轮完整重放，关闭冲突学习。
2. `failed_model_cache`：缓存此固定问题/证据下已拒绝的模型，后续只处理未缓存候选；缓存不会被用于宏观证据证明。
3. `conflict_reuse`：保留全部结构候选，学习、复核并复用冲突证书。

输出总耗时、受 `tracemalloc` 监测的 Python 分配峰值、候选重放数、分类操作量、已完成轮数、与完整重放的相容集合一致性及误剪名单。未完成全部轮数时，一致性字段为 `null`，并保留停止原因；不把预算未决当作结果不一致。各策略都在内存追踪开启时计时，时间包含追踪开销；内存不是进程 RSS。因内存追踪为进程全局工具，该基准要求独占追踪，不能与其他线程的追踪任务并发使用。

当前是**预计算响应表之后的重复评估基准**：不包含模型预测准备成本，也不采集新实验，不测量新候选持续到来的在线场景。报告显式给出 `prediction_preparation_included=False` 与 `new_experiments=0`。真值对照计算不计入各策略耗时。

小型 `x/z/xz` 示例的五轮结果中，完整重放、失败缓存、冲突复用分别重放 15、7、6 次，但语义操作量分别为 345、257、894。冲突复用减少重放，却增加证明开销，因此不能据此宣称整体加速。后续应加入高成本模型、大量矛盾继承者和新增候选场景，测量收益转折点。

## 0.14.0：索引后端与声明式重构

### 选择过滤后端

```python
from bidirectional_modeling import ExperimentHypothesisSearch, SearchWorkBudget
from bidirectional_modeling.search_examples import conflict_search_scenario

scan, data = conflict_search_scenario()
indexed = ExperimentHypothesisSearch(scan.protocol, scan.hypotheses, scan.target,
                                     backend='indexed')
budget = SearchWorkBudget(100_000)
assert indexed.search(data, budget=budget).compatible == scan.search(data).compatible
assert budget.work.index_entries > 0
assert indexed.fingerprint == scan.fingerprint
```

默认 `backend='scan'` 保留原有逐行过滤行为，作为兼容实现与正确性对照。`backend='indexed'` 为每个“实验、响应”及每个约束建立响应行位集合，通过精确交集过滤；不会合并结构候选，也不改变实验或宏观语义。实现策略不参与问题指纹，因此同一问题的证书可以跨这两个后端重新验证。

第一次过滤或证据标签检查时按需构建索引，并计入共享预算：`index_entries` 统计读取的响应单元和约束成员；`index_operations` 统计索引初始化和位集合交集。构建中断时不发布部分索引，下次使用新预算完整重建。相同搜索器随后复用已建索引；替换协议对象后重建。索引属于运行时派生状态，不写进 JSON 会话；恢复会话时保留后端选择，索引重新构建。旧会话中没有 `backend` 字段时默认使用扫描。

`ExecutableSearchAdapter.prepare(..., backend='indexed')` 与引擎的适配入口也接受该参数。新增或重构候选得到新的搜索器，当前会重新构建索引；尚未实现跨搜索器共享的全局索引缓存。

位集合交集的一次操作不等于常数 CPU 时间，其成本随响应宇宙长度变化。两种后端的分类计数帮助解释工作构成，不能用总操作数比例直接宣称速度提升。协议仍需提供完整有限响应宇宙，索引解决重复过滤成本，**不解决宇宙本身的指数规模**，也不是符号求解器或惰性模型预测后端。

### 重构规则

```python
from bidirectional_modeling import DescriptionLength, ReconstructionRule, SearchSession
from bidirectional_modeling.search_examples import conflict_search_scenario

search, data = conflict_search_scenario()
session = SearchSession(search, data)
session.run()
rule = ReconstructionRule('independent-to-joint', withdraw=('additive',))
session.reconstruct(rule, 'x', name='rebuilt', world=1, macro_answer='interaction',
                    description=DescriptionLength(concepts=1, relations=2))
assert 'rebuilt' in session.run().compatible
```

规则明确列出 `withdraw` 和 `add`。新候选的预测、目标答案和完整描述成本必须显式提供，材料可保留或替换；不会自动沿用父模型的正确性。规则不能撤回父模型未声明的约束，也不能同时撤回并添加同一约束。

`SearchSession.reconstruct` 先构造并验证候选，再添加到会话；如果新预测违反保留的承诺，操作失败且会话不变。事件记录规则名、父候选和承诺变更。仍然继承失败组合的后代继续被剪枝；撤回该组合的新候选需要重新检查数据，不能仅因撤回了承诺就自动通过。

这里“保留承诺”只针对有限协议中已声明的约束，不证明任意结构变换都保守，也不证明未来扩展实验域中的行为相同。此版本提供显式重构入口，不自动发明重构规则或证明无限低阶语言的覆盖完整性。

### 冷启动对照与验证

`search-benchmark` 现在增加 `indexed_conflict_reuse`。每种策略都创建新搜索器，索引策略首次建索引的时间、Python 分配峰值和操作量计入测量，避免拿免费预热的索引与冷启动扫描比较。

在五轮 `x/z/xz` 示例中，扫描冲突复用与索引冲突复用均重放 6 次；分类语义操作总量分别为 894 和 399，后者包含 70 个建索引条目。是否节省实际时间仍看 `seconds`，而且小型案例不能代表所有规模。实验采集和模型预测准备仍不属于这项基准。

测试穷举了两个二元实验上的全部 16 种约束允许集，比较所有响应向量及其证据子集下两种后端的相容集合、冲突核、最小证据、商分组与实验选点；另覆盖矛盾数据、未知预测、预算中断、协议替换、会话恢复和错误重构。

## 0.14.1：目标约束、问题快照与模型版本一致性

### 目标映射必须贯穿候选的整个生命周期

`ExperimentHypothesisSearch(..., world_answers=...)` 可声明覆盖整个响应宇宙的权威目标映射。已知预测候选必须满足 `macro_answer == world_answers[world]`；构造、会话新增、规则重构和会话恢复都执行同一检查。模型适配器现在始终传入该映射，不再只保留目标表的摘要。

新属性 `world_answers` 返回不可变元组；未知预测仍然未决，不会因提供某个标签而被确认为宏观答案。映射进入问题指纹，改变映射会使旧宏观证书不再绑定。冲突证书仍只涉及协议、约束和证据，不承担宏观目标证明。

直接构造目录时可以省略映射，以兼容原有“每个模型提供标签”的接口；此时 `world_answers is None` 明确表示没有权威目标表约束。不能把此类目录当作已验证过外部目标定义。更改目标应构造新的问题快照，并重新确认对应标签和证书。

### 搜索问题只通过验证入口变更

`protocol`、`hypotheses`、`target`、`backend`、`world_answers` 是只读属性，直接赋值会抛出 `AttributeError`。`with_hypotheses(...)` 构造新的、重新验证过的目录，并自动保留协议、目标映射与后端；会话新增/重构也调用这一入口。

协议或目标需要变化时显式构造新的搜索器，不能把旧索引与不一致的候选目录拼在一起。索引仍是搜索器内部可重建的缓存。这是正常 API 的一致性约束，不是对任意 Python 内省或私有字段篡改的安全沙箱。

### 固定模型配置后重放完整实验组

适配器对每个模型创建隔离副本，并绑定其可检查的声明。内置 `FiniteStateModel` 的声明包括初态、动作、度量、假设、读出和转移等回调的结构身份。每次批次收集前后都复核声明；发现配置变化则该候选变为未决，诊断为 `model_declaration_changed`，不会污染调用方模型。

采集顺序从相邻重复 `A,A,B,B` 改为整组重复 `A,B,A,B`。完整两轮结束后逐用例比对批次，避免只做局部重复而遗漏跨用例漂移。该检查能检出测试中“前两次稳定为 0、后两次稳定为 1”的外部响应变化；它仍然不是无限时间上的确定性证明。

第三方模型现在必须提供 `search_signature()`，返回严格结构编码器支持的声明数据，覆盖影响模型语义的配置/版本。无法确定声明身份则保留为未决。外部服务或隐藏状态的版本管理仍是适配者责任；系统不会声称一个任意自报签名证明了隐藏实现不变。具有无法结构编码的闭包依赖的内置模型同样会失败关闭，需要整理声明或提供适当的第三方适配。

只有模型在全部用例和两轮检查中通过，才发布其批次绑定；失败候选不会遗留看似通过的部分绑定。`batch_bindings` 每行现在为 `(candidate, experiment, first_batch, replay_batch, model_declaration)`，新增的第五项记录模型声明指纹。

### 会话升级与来源记录

`SearchSession.from_prepared(prepared, evidence)` 会保留适配结果的模型声明/批次绑定和原始问题指纹。这些关联也随 JSON 保存，供外部归档与审计；它们不是完整原始轨迹，不会代替证书重验或源模型重跑。

会话输出升级为 schema 2，保存 `world_answers`。仍支持 schema 1 的原目录及原指纹，但其 `world_answers` 为 `None`；旧适配会话只保存了目标摘要，不能从摘要恢复原目标表，需要重新适配或由调用方明确提供完整映射。不能把 schema 2 的已绑定问题简单降为 schema 1：问题指纹检查会拒绝这种不匹配。

已有 `SearchSession(search, evidence)` 构造仍然可用，但只有 `from_prepared` 自动携带适配来源。来源字段提供一致性记录，不提供真实性签名。新增的回归测试覆盖：错误目标标签、错误重构、公开字段赋值、跨用例配置变化、外部响应漂移、会话映射恢复以及旧格式兼容。

## 0.15.0：不可变指纹缓存、共享索引与动态基准

### 缓存不改变证明身份

协议指纹在首次访问时计算并缓存；搜索问题指纹也在首次访问时计算。摘要算法和编码不变，旧证书与会话的绑定不会因为缓存而改变。`dataclasses.replace(protocol, ...)` 创建新协议对象并重新计算指纹；缓存不是 dataclass 数据字段，不进入会话 JSON。

`with_hypotheses` 仍先验证整个新目录的预测、承诺与权威目标映射，再共享同一协议对象已经完整构建的响应索引。会话新增、重构候选因此不再反复支付建索引成本。索引包含只读映射，不能通过一个候选目录更改另一个目录的过滤结果。索引与候选或目标答案无关，但目标/目录变化仍改变问题指纹，旧宏观证书仍须重新验证。

未完成的索引不会共享；尚未建索引的两个搜索器可以各自构建。不同协议对象不会自动命中全局缓存，即使语义恰好相同。索引不序列化，会话加载时仍自行构建/验证。这些规则优先保持依赖边界明确，尚未实现跨进程缓存或并发建索引去重。

### 动态更新基准

```bash
bidirectional-modeling search-updates-benchmark --json
```

或使用 API：

```python
from bidirectional_modeling import benchmark_search_updates
from bidirectional_modeling.search_examples import dynamic_search_scenario

search, steps = dynamic_search_scenario(copies=20)
result = benchmark_search_updates(search, steps, repetitions=3)
assert all(row['agrees_with_reference'] for row in result['results'])
```

`SearchBenchmarkStep(name, evidence, additions=())` 将证据**整体替换**为指定集合，并向现有目录加入候选。固定协议和目标不变；同名候选不能静默覆盖。内置工作负载包括：

1. 从两个候选开始搜索并提取冲突。
2. 加入 20 个继承相同失败约束的结构候选。
3. 撤回部分证据，要求旧证书失效，并恢复先前被排除的候选。
4. 加入带新来源标识的完整校准数据，重新判断和生成证书。

四种策略分别是完整重放、失败模型缓存、扫描冲突复用、索引冲突复用。每个阶段都对照独立完整扫描的相容集合。失败缓存只在完全相同的证据下沿候选新增复用；证据值、顺序或来源变化均清空该缓存。冲突策略则逐份复核依赖，不根据名称沿用旧证书。

每种策略默认独立重复三次，报告完整试验的中位耗时和 Python 分配峰值，以及每次试验各阶段的候选数、重放次数、累计工作量、缓存失效、撤销证书数、误排除名单和一致性结果。任一试验未完成时，汇总中位值和一致性返回 `null`，保留中断原因与已完成阶段；不能把部分运行时间当成完成任务的速度。

每次测量都创建新的协议/搜索器，包含协议校验、冷指纹计算和首次索引构建的实际耗时。目录/响应准备及真值对照在测量之外，没有新实验采集；操作计数也仍不包含全部 Python 指令。`tracemalloc` 会影响时间测量，结果适合定位相对开销，不是没有测量开销的生产性能。测试只断言语义一致与预算边界，不使用机器相关的加速阈值。

可通过 `copies` 或自定义步骤扩大候选族并改变证据撤回方式。小型工作负载上，索引冲突复用仍可能比完整重放慢；这项基准用于观察收益转折点，而不是预设冲突学习必然获益。下一步仍需要惰性/符号查询接口来处理不能显式枚举的响应宇宙。


## 0.19.0：情境变化与宏观充分性

新增情境网络、显式证书迁移、会话分叉、有限局部拼接、有界布尔重构和独立宏观充分性证书。原固定协议搜索继续作为每个情境内的后端。新充分性证书支持只读取保留观测，既有 `compress_evidence` 和最小性验证继续承担更强的历史等价与最优性主张。参见 [情境网络与结构重构](docs/context_network.md)。
