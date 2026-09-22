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
