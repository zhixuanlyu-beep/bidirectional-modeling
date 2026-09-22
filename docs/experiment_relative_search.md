# 实验限定的假设搜索与宏观证据基

本模块将“复杂度安排搜索、矛盾决定剪枝、重构产生解释、宏观分歧决定停止”落实为有限确定性参考实现。它与 `ResidualQuotientAnalyzer` 互补：后者对一个模型的内部状态取商；`ExperimentHypothesisSearch` 对多个候选模型的实验响应取商。

## 使用

```python
from bidirectional_modeling.search_examples import conflict_search_scenario

search, data = conflict_search_scenario()
report = search.search(data)
assert report.compatible == ('xz',)
assert report.rejected == ('x',)
assert report.pruned == ('z',)
assert report.determined

basis = search.compress_evidence(data)
assert basis.minimum_cardinality
assert search.validates_macro(basis, data)
assert len(basis.retained_evidence) == 2
```

也可运行 `bidirectional-modeling search-demo --json`，或从源码运行：

```bash
PYTHONPATH=src python -m bidirectional_modeling.cli search-demo --json
```

## 三个独立关系

| 关系 | 实现 | 保证边界 |
| --- | --- | --- |
| 描述复杂度 | `DescriptionLength` 的框架、概念、关系、参数、观测映射和辅助假设总和 | 所有项使用同一 `SearchProtocol.coding`；编码费用由领域适配者提供，不是自动计算的 Kolmogorov 复杂度 |
| 实验等价 | `partition()` 比较全部允许实验的响应；`partition(names)` 只比较指定实验 | 全域相等只在声明的有限实验域成立；部分域分组可被新实验拆分 |
| 矛盾继承 | 候选的 `commitments` 包含证书的整个约束组合 | 不依据共同材料、名称相似、变量数量或父子关系剪枝 |

每个商类按描述复杂度排列，但保留全部结构代表。模型当前等价不保证重构后等价；调用方必须保留它们的生成路径。当前版本接受领域适配者提供的有限候选目录，不会穷举全局零阶结构，也不会自动发明领域概念或重构算子。

`irreducible_against(name, lower_names)` 检查目标候选是否不能被给定低阶目录中的任何一个候选在完整实验域上替代。空目录或未决预测返回 `None`。低阶语言及允许的组合、辅助变量和资源约束由调用方定义；两个变量不自动意味着二阶，排除给定目录也不等于排除所有可能的低阶理论。

## 实验协议与有限逻辑语义

`SearchProtocol` 包含固定的适用范围/校准版本、统一编码约定、实验集合、全部允许的响应向量和约束定义。每个 `SearchExperiment.semantics` 应说明干预、观测映射与条件；响应为精确的字符串标签，没有隐含数值容差。

`worlds` 是本次证明允许的**响应宇宙**，每一行对应全部实验上的一个完整响应向量。它不同于 `hypotheses`：后者是实际枚举的结构候选目录。约束 `ResponseConstraint` 用满足它的响应行索引定义，因此不是仅凭规则名称相信逻辑蕴含。例如示例枚举四个二元实验的全部 16 种二元输出；`additive` 对应满足

\[
y_{11}-y_{10}-y_{01}+y_{00}=0
\]

的所有行。候选的已知预测若违反它声称继承的约束，构造搜索器时就会报错。

适配现有可执行模型时，应先用同一场景、干预及读出协议生成响应表，再保留模型对象与候选标识的对应。模型运行异常、超时、尚未计算等情形使用 `SearchHypothesis.world=None`，不填造一个失败响应。数据由实验方提供，不能把候选自己的预测直接当作观测。协议声明与目标 `macro_answer` 的领域正确性仍是调用方责任；本模块不会从名字推导真实世界语义。

## 带适用条件的冲突证书

在协议所限定的响应宇宙 \(\Omega\) 中，定义 \(W(U,S)\) 为满足约束组合 \(U\) 且符合证据 \(S\) 的响应行。证书要求：

\[
W(\varnothing,S)\ne\varnothing,\quad
W(U,\varnothing)\ne\varnothing,\quad W(U,S)=\varnothing.
\]

前两个条件避免把自身不一致的数据或已不一致的约束冒充新实验反例。`learn_conflict` 逐项删除冗余约束与观测，得到包含意义下极小的共同冲突核；它**不保证最小基数**。核心可能由多条单独可行、共同不可行的规则组成，不能把每条规则各自判错。

`validates_conflict` 重算矛盾并核对协议指纹、证据值与来源。搜索只使用仍有效的证书；删除或替换依赖证据、改变校准范围、实验语义或约束定义都会阻断旧证书。相同数值的新校准数据可以重新生成一份新证书，不能冒用旧绑定。

证书不绑定某一候选目录，因此同一协议下可排除后来新增的矛盾继承者。撤销证书时使用新的活跃证书集合调用 `search`；所有候选仍在原目录内，能被重新评估。

实验中的 `y=x` 与 `y=z` 失败后，`y=xz` 保留 `x,z` 材料但撤回独立充分/可加承诺，因而不在原证书的保真扩张锥内。这就是此处“错错得对”的操作含义，绝不是把两个矛盾命题同时保留。

在二元输出宇宙中，示例的 `10→0, 01→0, 11→1` 三条证据已否定可加性：要可加就必须有 `00→-1`，超出该宇宙。一般实数输出模型中不能省略第四点。这说明证书必须携带假设范围，不能跨范围套用。

## 剩余集合、停止与未决

仅用冲突剪枝的剩余目录是

\[
\widehat{\mathcal V}(S)=\mathcal H\setminus\bigcup_c\mathcal K(c).
\]

可靠证书只保证真正相容目录 \(\mathcal V(S)\subseteq\widehat{\mathcal V}(S)\)。`search` 再重放数据，分别返回 `compatible`、`rejected`、`pruned` 与 `undecided`。候选按总描述长度调度；`max_replays` 限制候选重放数，耗尽后不假装模型失败。

`answers` 保留相容与未决候选的目标答案。只有相容集合非空、没有未决且所有相容候选目标一致时才设置 `determined=True`。这个保证相对于完整的**给定目录**，不是对未生成的候选或未声明的实验的结论。`macro_identifiable()` 还检查目标在完整实验等价类上是否恒定；若相同响应对应相反宏观答案，仅增加同域观测也不能解决。

## 小证据基

令 \(\Phi(S)=\{g(M):M\in\mathcal V(S)\}\)。`compress_evidence(D)` 在原始候选目录上按子集大小穷举，寻找满足

\[
S\subseteq D,\qquad \Phi(S)=\Phi(D)\ne\varnothing
\]

的最小基数证据。它不使用先前的剪枝结果或已筛选候选作为免费背景，也不要求唯一模型。\(\Phi(D)\) 有多个答案时，仍可压缩证据，但 `determined=False`。空版本空间和未知预测不能产生这种精确证书。

`max_subsets` 控制组合搜索预算；耗尽则返回原始证据这个有效上界，同时设置 `minimum_cardinality=False`。零预算也不会声称已完成最小化。证书绑定协议、目标定义、全部候选声明及原始数据；扩展模型类、改变目标或替换证据后必须重做验证。`verify_macro` 将充分性和最小性验证分开，并对最小性搜索设置 `max_subsets` 和共享操作预算。最小性检查仍可能是指数级，不能把它误称为廉价通用证明检查器；预算耗尽返回未决。大空间应使用领域求解器或可验证的覆盖证明替代显式穷举。

示例中两条数据已区分目录内的 `x/z/xz`，但仍需三条数据才能排除响应宇宙中的整个可加模型族；两种证书的证明对象不同。

不能普遍保证证据很小：若目录包含“全部为零”和“仅第 i 个位置异常”的模型，确认无异常仍需检查全部位置。测试覆盖此下界。

## 下一次实验与成本

`next_experiment` 只覆盖宏观答案不同的存活响应对，选择“覆盖数 / 实验成本”最大的未测实验；等价结构副本不重复计权。它是明确的贪心启发式，没有先验概率，不宣称信息增益最优。返回 `None` 可能表示没有已知的可区分对，也可能表示不可识别或预测未决，不能将其直接当作成功。

`replay_checks` 是候选对已有数据的检查数；它不包含建立响应表、构造证书和子集搜索的代价，也不代表真实采集实验数。当前后端不会调用实验硬件或自动采集数据。少做实验、少读取证据、少检查候选必须分别计价。

## 尚未实现的推广

- 开放、连续或随机假设空间；含噪声统计证书、时间一致覆盖及跨模型多重检验。
- 自动跨框架翻译、非保守重构生成与语义保持证明。
- 对无限候选语言的公平复杂度枚举、通用求解器 UNSAT 证书与最优性分支限界。
- 通用 MDL 数据编码：这里精确匹配数据后按模型描述成本排序，不实现概率似然或 `L(D|M)`。

这些限制不妨碍在有限域中验证算法的可靠性，但不能把有限域的确定性结果升级为开放世界的最终真理。


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
