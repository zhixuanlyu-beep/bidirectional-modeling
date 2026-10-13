# 有限任务抽象：判断因子化与操作同余

显式导入的实验 API `bidirectional_modeling.task_abstraction` 检查[语义委托](semantic_delegation.md)中的两个条件：同一宏观类内全部声明判断一致；逐项抽象后的操作结果及可适用性一致。它不读目的名称来猜测正确性，也不替代满足性、残差最小性或搜索完备性。

## 数据声明

`FiniteTaskAbstraction` 包含非空具体状态、非空宏观状态、非空判断列、满射 `projection`、完整 `responses`、可选 `operations`、`scope`、`assumptions` 和明确布尔 `complete`。所有序列快照为元组，拒绝以集合声明顺序；状态和判断名称唯一，响应为离散字符串标签。

- `projection[i]` 是具体状态 i 的宏观索引，每个声明宏观状态都须有具体代表。
- `responses[i][j]` 是第 i 个状态在第 j 个判断下的结果。可将环境写入判断名称，列出所有承诺的环境／判断对。
- `FiniteOperation(name, arity, outputs)` 按 `product(range(n), repeat=arity)` 的字典序列出完整 n^arity 行，输出为具体状态索引或 `None`。
- `None` 仅表示**已知无定义**；未知预测不能编码为它。尚未调查完整域时用 `complete=False`，不要用虚构响应填充未知单元来请求通过。
- `complete=True` 声明这些表覆盖 `scope`，不是由表形状自动推导的事实。检查器证明的是这些数据表的关系；状态和判断确实覆盖现实任务仍由调用方负责。

```python
from bidirectional_modeling.task_abstraction import (
    FiniteOperation, FiniteTaskAbstraction,
    analyze_task_abstraction, verify_task_abstraction,
)

problem = FiniteTaskAbstraction(
    scope='Two implementations with the same declared response',
    states=('implementation_a', 'implementation_b'),
    macro_states=('acceptable',), judgments=('meets_requirement',),
    projection=(0, 0), responses=(('yes',), ('yes',)),
    operations=(FiniteOperation('step', 1, (0, 1)),),
    complete=True,
)
result = analyze_task_abstraction(problem)
assert result.status == 'verified'
assert verify_task_abstraction(problem, result.certificate) == 'valid'
```

两个微观输出不同，但投影后都为 acceptable，因此操作可以保持语义。换为 `(None, 1)` 会被反驳：同类状态的可适用性不同。

## 回执与独立复核

| 输出 | 意义 | 复核 |
| --- | --- | --- |
| `verified` 与 `TaskAbstractionCertificate` | 完整声明下，判断与全部声明操作都能因子化 | `verify_task_abstraction` 返回 `valid / invalid / undecided` |
| `refuted` 与 `AbstractionWitness` | 一个相同宏观输入对给出不同判断或操作结果 | `verify_abstraction_witness` 直接读取原表重放，返回 `valid / invalid` |
| `undecided` | 工作预算不足，或未声明范围覆盖完整 | 不产生正证书，不能作为失败、不存在或完备性的结论 |

正证书包含具体问题指纹、宏观判断表和宏观操作表。复核逐项检查维度、名称、元数、输出范围，并直接将每个具体表单元与候选因子比较；它不调用分析器，也不信任分析器的代表映射。两条路径共享声明校验、索引约定和指纹编码，并仍需信任 Python。这里“独立”限定为直接复核未委托生成过程。

指纹域为 `finite-task-abstraction-v1`，绑定范围、状态与宏观名称及顺序、判断列、映射、全部响应和操作、覆盖声明与假设。换规格、换环境或换操作后旧证书失效。指纹检查材料一致性，不认证其来源。回执仅是 Python 数据对象及 CLI JSON 输出，没有长期证书归档读取器。

反例含判断／操作名称和两组具体输入索引；复核重新检查输入成员、宏观输入相同及输出确实不同。单个反例只反驳该组数据的抽象主张，不证明整个方案不可实现。范围未完整时，已经读取的真实冲突仍能反驳当前抽象；无冲突则保持未决。

没有操作时，通过仅涉及静态判断。多元操作只对声明的组合规则成立，反馈循环仅覆盖这些规则的有限展开；无限行为与活性、所有现实组合均不在承诺内。

## 预算与成本

`max_checks` 是非负整数或 `None`；一次检查读取一个具体响应单元或一个具体操作行。完整无冲突扫描需要

$$n|Q|+\sum_{F}n^{\operatorname{arity}(F)}$$

次检查。分析与复核各有自己的额度，任何检查截断都保持未决；精确达到完整扫描所需额度可以通过。已有局部反例可以提前返回，不需穷尽整个域。

数据构造、完整性校验、哈希、内存分配与序列化不计入逻辑检查额度。具体和宏观操作表都可能指数增长，额度不是墙钟或内存沙箱，也没有普遍的短证书或廉价复核保证。本模块不搜索最粗划分，不优化总成本；`ResidualQuotientAnalyzer` 的最小商也仅针对其自己的操作和观察协议。

## 演示与回归

`abstraction-demo` 中，原“等工时”规格通过；任务方加入夜班负担判断后粗抽象被反驳，旧证书失效；保持新判断并分开状态后通过。另一组夹具静态判断可因子化，但 `combine` 的相同宏观输入因隐藏占用状态产生 on_time／late 两种输出，增加资源区分后通过。

回归用独立成对比较定义核对 720 组二状态一元／二元偏操作与判断表，检查已知无定义、各预算截点、范围不完整、规格变化、证书篡改、生成器错误注入及跨哈希种子输出。它检验有限判据的实现，不认证调用方自然语言意图。
