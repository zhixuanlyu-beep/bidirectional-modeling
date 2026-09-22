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

`max_subsets` 控制组合搜索预算；耗尽则返回原始证据这个有效上界，同时设置 `minimum_cardinality=False`。零预算也不会声称已完成最小化。证书绑定协议、目标定义、全部候选声明及原始数据；扩展模型类、改变目标或替换证据后必须重做验证。`validates_macro` 还会重查声称的最小基数，验证开销可能是指数级，不能把它误称为廉价通用证明检查器。大空间应使用领域求解器或可验证的覆盖证明替代显式穷举。

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
