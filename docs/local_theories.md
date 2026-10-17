# 按需局部理论、有限覆盖与成本保持

`extensions.local_theories` 是显式导入的实验扩展。它将“为一类问题引入局部概念与关系，再提取整体答案”落实为有限数据与可复核的证明义务。当前实现提供局部规格和见证的注册、启用、覆盖验证与搜索提示；概念的自动发明、通用适用性识别以及任意数学证明搜索尚未实现。

## 三种保证分别检查

| 保证 | 入口与材料 | 不自动推出的结论 |
| --- | --- | --- |
| 局部语义及整体覆盖 | `certify_local_cover`：每个局部输入有带来源的规格、局部见证正确、重叠输出一致、联合覆盖完整有限任务且符合独立目标 | 有限子覆盖的普适存在性、无限维外推、局部约束任意可拼接 |
| 整体程序提取 | 同一入口直接执行调用方提供的基础语言程序，确认全部输入上等于拼接结果；证书保存实测整体成本 | 系统已自动发明或编译概念；整体成本等于局部成本之和 |
| 单个翻译保持成本 | `verify_cost_preserving_translation`：在完整任务域上比较两个基础程序的行为及最大运行成本 | 目标正确、全部候选已覆盖、运行最优 |
| 搜索不漏原候选及最优性 | `LocalTheoryLibrary.search` 保留基础枚举；`verify_computation_certificate` 独立复核目标并穷尽更低成本层 | 模块一定加速、先得到证书的程序即最优、对任意形式问题可判定 |

这里“局部”指完整有限输入域的子集，可以重叠。它与 `extensions.gluing` 的共享变量约束问题不同：后者必须验证整体赋值存在，不能把两两相容当作充分条件。当前局部输出是确定的函数值，重叠一致后才定义一个拼接函数；再检查它的定义域是否覆盖所有合法输入、输出是否符合原目标。

## 声明与运行

```python
from bidirectional_modeling.computation_search import BitEvidence, BitProgram, FiniteBitTask
from bidirectional_modeling.extensions.local_theories import (
    LocalBitTheory, LocalTheoryLibrary, certify_local_cover, verify_local_cover,
)

task = FiniteBitTask('AND fixture', 2, (0, 0, 0, 1))
zero = LocalBitTheory('first-zero', task.scope, 2,
    (BitEvidence((0, 0), 0, 'fixture:00'), BitEvidence((0, 1), 0, 'fixture:01')),
    BitProgram(('zero', 'halt')))
projection = LocalBitTheory('first-one', task.scope, 2,
    (BitEvidence((1, 0), 0, 'fixture:10'), BitEvidence((1, 1), 1, 'fixture:11')),
    BitProgram(('input:1', 'halt')))
locals_ = (zero, projection)
extracted = BitProgram(('input:0', 'input:1', 'and', 'halt'))
report = certify_local_cover(task, locals_, extracted, max_cost=9)
assert report.status == 'valid'
assert verify_local_cover(task, locals_, report.certificate) == 'valid'
result = LocalTheoryLibrary(locals_).search(task, max_cost=9)
```

完整可执行示例：

```bash
PYTHONPATH=src python3 examples/local_theories.py
```

局部见证各需 5 个机器成本单位，提取后的 AND 程序需 9 单位。证书分别保存这些量；不把 `5+5` 计作整体程序成本，也不把局部证明工作计作最终运行。提取程序可以没有分类判断、模块名称或调用分支。额外的知识库、建索引、证书存储、哈希和 Python 容器成本仍存在，不包含在位栈机器成本中。

## 作用域与公平调度

库构造时按 `(scope, width)` 建立只读索引；查询只取完全匹配的桶。作用域是调用方的明确适用性声明，不是基于命名相似度的推断。注册无关模块会增加建库时间和存储，但不会增加本任务启用的提示集合。一个桶可以很大，目前没有常数活跃模块数或全局搜索复杂度不增长的定理。

`search_computations(..., hints=())` 接受有限有序基础程序提示。先验证语言及栈结构；每个成本层只考虑能装入该层的提示，并在每个基础候选前最多尝试一个提示。提示重复去除，不改变基础枚举的成员与顺序。每个基础候选至多被一次提示试验推迟；该层试验次数至多为原层的两倍。知识库搜索不删除没有局部理论的区域，空库完全退回原搜索。

提示只决定发现顺序，既不充当证明，也不剪枝。局部程序在自己的范围内正确，可能在别处失败；它若被作为整体候选，仍须通过活跃证据与完整任务。错误输出才成为反例，预算不足保持未知。理论变化、目标变化、来源变化均不能沿用旧覆盖证书。失败局部结构没有继承特权，可替换为全新理论。

设原机器有限层为 H_B。调度仍访问其中每个成员，因此对原最优解 p* 始终存在恒等表示 p*，发出的程序无模块调度开销。这保证**候选覆盖和原运行最优解不丢失**，并不保证有限试验额度内能找到它。若用纯局部语言替换基础路径，则必须另证：每个相关原程序 p 都有等价局部表示 q，翻译后 C(q) ≤ C(p)。单个翻译复核通过不能冒充这一全体量化。

## 复核、预算与理论边界

覆盖证书绑定 `task-v1` 任务、`local-bit-theory-v1` 局部声明、来源、见证及有序 `local-bit-cover-v1` 清单，最终程序沿用 `autonomous-bit-stack-v1`。复核直接重放原始局部与完整目标，不调用证书生成入口；精确检查实测成本。仍信任机器执行、输入声明、校验和编码。来源字段证明绑定一致性，不证明观测真实。

`max_cost` 限制每个局部及整体程序的装载、执行和输出。`SearchWorkBudget` 限制逐项语义检查，生成阶段每个局部输入一次 `response_checks`、每个完整目标输入一次 `constraint_checks`；复核使用相同计数类别。它不约束构造、遍历提示、内存或墙钟。资源耗尽或解释器故障返回 `undecided`，覆盖缺口或实际输出冲突返回 `invalid`，并附生成阶段原因；无证书表示没有通过，不能据此排除整个问题可解性。

覆盖通过仅保证该任务的完整有限域，输入宽度变化必须重新声明。低维枚举承担高维证明，需要独立的完备验证界或结构归纳证书；这里没有从有限实验推导这样的定理。Rising Sea 的概念重述与有限覆盖提供研究方向，当前位栈夹具不构成其普适复杂度定理。具有有限可靠可检查证书的候选可由公平证明搜索发现，但有限表达本身不保证证书存在。

若要宣布最优，使用 `verify_computation_certificate` 对搜索结果另行复核；覆盖证书没有最优字段。更低成本层复核截断时，见证有效性可以保留，最优性继续未决。发行版本、既有会话格式及机器成本模型不变。
