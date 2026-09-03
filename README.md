# 可验证的双向人机协作建模框架

[![CI](https://github.com/zhixuanlyu-beep/bidirectional-modeling/actions/workflows/ci.yml/badge.svg)](https://github.com/zhixuanlyu-beep/bidirectional-modeling/actions/workflows/ci.yml)

这是“宏观目的 ↔ 介观模型 ↔ 微观结构”的可运行参考实现。它不假设两个方向存在唯一答案，而是返回带验证证书、反例、边界和排序分数的候选集合。

从 `0.5.0` 起，尺度之间的状态投影不再只隐含在模型读出中：`Correspondence` 把粗粒化映射和场景映射声明为一等对象，`CorrespondenceValidator` 用上下层两个可执行模型检查动态交换图是否成立。

`0.6.0` 进一步把证书绑定到规范化的上下文指纹，并引入校准/留出验证套件：在已知场景上相容只产生 `compatibility_passed`，只有全部用例通过且至少包含一个声明为独立来源的留出用例，套件的 `passed` 才为真。

`0.7.0` 加入有限确定性系统上的残差语义商发现：框架从初态枚举可达微观状态，以动作序列作为区分上下文，逐层细化观察等价类，生成最短区分上下文、商转移和完整性/稳定性/同余性证书。只有完整探索并收敛到同余分区时，结果才声明为当前实验域上的最小行为模型。

`0.8.0` 把局部支撑集提升为一等语义：`FiniteStateModel.applicable` 显式声明动作何时无定义，`UndefinedTransition` 表示合法的 `⊥`，普通运行异常仍作为未知边阻断证明。残差报告还用反例循环提取有限区分上下文基，每次只加入一个能严格细化当前测试分区的最短上下文。

`0.9.0` 加入候选微观组合规则选择：多个 `CompositionRule` 必须接受同一组、由实验方持有的操作观察；错误观察结果、错误支撑和运行异常都会生成阻断反例。通过测试的规则还必须得到完整、稳定、同余且可由有限上下文基重建的残差商，之后才按“规则 + 语义状态 + 偏转移表 + 区分上下文 + 异常”的显式描述长度排序。

`0.9.1` 加固证明所依赖的语义底座：上下文、宏观规范、效果生成、闭合分析与残差分析共享同一个严格结构编码器，不再以对象的 `repr` 充当身份；有限状态模型在回调边界深度隔离状态，证明型分析会重放读出和转移以拒绝非确定性结果。初态也受 `max_states` 约束，非有限的度量或容差值以及负状态索引会显式失败。

```text
MacroSpec G ── Realizer ──> Pareto{(Model, complete Certificate)}
     ▲                              │
     │                              ▼
Concept refinement <── Counterexample / closure analysis
     │                              ▲
     └── Interpreter <── {effect, function, intention hypotheses}
```

## 设计原则

- 所有目的都相对于上下文 `Context Γ`：环境、尺度、历史证据、观察者、干预和假设。
- 无标签发现仍然相对于一个操作协议：模型动作给出允许的上下文，模型读出和 `EquivalenceSpec` 给出最终可观察判据；框架不会把缺少观察结构误称为“无先验语义”。
- 候选组合规则不能自行选择验收判据：`CompositionExperiment` 固定初态、动作、读出、等价关系与操作测试，所有规则在同一域中竞争；规则描述长度必须来自同一编码约定。
- `MacroSpec G` 明确可观测量、目标、等价关系、不变量、约束、误差与时间范围。
- 向下推断搜索满足 `M |=Γ G` 的模型，返回成本、复杂度和风险上的帕累托候选，而非虚构唯一实现。
- 向上推断严格区分效果、功能和意图。仅凭结构通常只能支持效果；功能依赖环境，意图还需要足够强的主体、设计或选择证据。
- 解释结果中的 `ranking_score` 是未校准的相对分数，不是“目的为真”的概率。区分实验的信息增益只使用显式声明的 `PurposeHypothesis.prior`，不会把排序分数偷换成概率。
- 模拟预算在候选、红队探测、目的解释、效果生成和双向往返的各阶段全局共享；结果同时报告 `simulations_used` 与 `truncated`。
- `FiniteStateModel` 深度复制进入和离开 `applicable`、`transition`、`readout` 的状态，避免候选通过嵌套可变对象污染实验或其他候选；残差与闭合证明还会重放同一输入，结果或动作支撑不一致时按未知行为失败关闭。
- 证书完整性必须相对于独立场景域证明。内置 `FiniteStateModel` 由框架从初态与干预导出场景清单；第三方模型必须由调用者通过 `Context.scenario_manifest` 提供 `ScenarioKey` 清单。候选提供的 `scenario_count()` 只作诊断提示，不能证明覆盖完整。
- 跨尺度验证允许多个微观场景映射到同一宏观场景，但要求每个微观场景都有映像、每个宏观场景都有原像，并且每个时间步的投影结果都满足上层等价关系；空场景域不能产生真空证明。
- 每份对应证书记录上下层 `Context` 的 SHA-256 指纹，防止把一个环境、假设或场景域中的结论冒充到另一个适用域。多用例套件在所有用例间共享同一模拟预算。
- 默认同时执行基线和显式干预，避免只在干预情形下通过、却在正常运行中失败。
- 目标是任务相关的最小充分模型，不是还原完整底层世界。

`MacroSpec.tolerance` 是所有数值字段要求和模型指标要求的默认误差界；某一要求设置非零 `tolerance` 时，以局部值覆盖全局默认值。宏观等价关系自身的分辨率仍由 `EquivalenceSpec.tolerances` 独立描述，二者不会混用。

## 已实现组件

- `FiniteStateModel`：有限状态、转移、行动、读出和透明资源指标。
- `ResidualQuotientAnalyzer`：枚举有限可达状态，构造按上下文深度单调细化的残差分区；合并所有未来观察行为及动作支撑相同的微观状态，并返回最短区分动作序列、反例引导上下文基和可验证的偏商转移。
- `CompositionRuleSelector`：先用共享操作测试排除观察、支撑或执行不一致的微观组合规则，再要求残差最小性证书，最后按透明的两段描述长度代理量排序；多实验用例必须全部通过。
- `SatisfactionEvaluator`：惰性消费模拟轨迹，对照调用方或框架持有的场景清单验证身份、去重、轨迹长度和模型归属，不信任候选自报的场景数；同一已认证轨迹批次可被多个规格安全复用。
- `Realizer`：接受设计库或参数化候选生成器，执行验证和红队探测，保留主证书与探测证书，并输出帕累托前沿、被支配候选和被拒候选。
- `Interpreter`：生成或接收目的假设，按时间范围缓存轨迹、共享模拟预算，用解释力、简洁性、鲁棒性和上下文证据排序，并提出信息增益最高的区分实验。
- `ClosureAnalyzer`：只从声明的初始状态探索可达状态，构造 `x₁ ~ x₂` 但未来宏观结果分化的见证，并列出可供人工批准的分离特征。
- `refine_until_closed`：把闭合性反例接回规格细化；每次提升新可观测量后重新验证，直到闭合、预算耗尽或人工拒绝。
- `ConceptLibrary`：保存定义、正反例、边界、相关概念、候选细化和一致的版本历史。
- `CorrespondenceValidator`：在共享模拟预算内分别认证上下层场景域，再检查 `projection(lower_t) ~ upper_t`；失败时返回具体场景、时间步和快照见证；`validate_suite` 进一步区分校准兼容性与独立留出复核。
- `ScaleGraph`：只接纳验证通过的直接对应边。多跳路径只表示每条边分别通过，不会被偷换成端到端对应证明。
- 双向往返检查：宏观往返只有在 `HypothesisGenerator.independent_recovery=True` 时才可通过，预先注入的假设目录只能证明兼容性；微观往返默认排除原模型本身，要求另一实现按“初始场景 + 干预”复现任务行为。往返的全部阶段共用一份预算。

## 安装与运行

需要 Python 3.9 或更新版本；核心包没有第三方运行依赖。

```bash
git clone https://github.com/zhixuanlyu-beep/bidirectional-modeling.git
cd bidirectional-modeling
python3 -m pip install -e '.[test]'

bidirectional-modeling demo
bidirectional-modeling demo --json
python3 -m unittest discover -s tests -v
```

不安装也可以从源码运行：

```bash
PYTHONPATH=src python3 -m bidirectional_modeling.cli demo
```

## 最小用法

```python
from bidirectional_modeling import (
    BidirectionalModelingEngine,
    HorizonExtensionProbe,
    Realizer,
    ResourceBudget,
)
from bidirectional_modeling.examples import software_scenario

goal, context, design_candidates = software_scenario()
engine = BidirectionalModelingEngine(
    realizer=Realizer(probes=(HorizonExtensionProbe(extra_steps=2),))
)
result = engine.realize(
    goal,
    context,
    design_candidates,
    ResourceBudget(max_candidates=20, max_simulations=500),
)

for candidate in result.candidates:
    certificate = candidate.certificate
    print(candidate.model.name)
    print("complete:", certificate.complete)
    print("verification score:", candidate.verification_score)
    for check in certificate.checks:
        print(check.name, check.passed, check.observed)
```

从无自然语言标签的操作观察中发现最小行为商：

```python
from bidirectional_modeling.examples import residual_quotient_scenario

equivalence, context, model = residual_quotient_scenario()
report = engine.discover_residual_quotient(model, equivalence, context)

print(report.minimal)                 # 完整、稳定且满足同余
print(report.explored_states)         # 5 个可达微观状态
print(report.quotient.class_count)    # 4 个残差行为类
for level in report.filtration:
    print(level.context_depth, level.class_count)
for witness in report.distinguishing_contexts:
    print(witness.actions)
print(report.context_basis)           # ((), ('probe',))
print(report.context_basis_reproduces_partition)  # 重建当前有界分区
```

第 0 层只按当前 `signal` 观察分组；深度 1 加入 `probe` 上下文后，未来结果不同的隐藏状态被拆开，而仅有无关 `copy` 字段不同的两个状态保持合并。`max_reachability_depth`、`max_states` 或 `max_context_depth` 截断时，报告会保留有界分区，但 `minimal` 必为假。

局部支撑通过 `FiniteStateModel.applicable` 声明。若一个残差类上的 `consume` 全部无定义，其商转移是良定义的偏转移；若同一类中只有部分状态支持它，该类会被继续拆分：

```python
from bidirectional_modeling import UndefinedTransition
from bidirectional_modeling.examples import partial_residual_scenario

equivalence, context, model = partial_residual_scenario()
report = engine.discover_residual_quotient(model, equivalence, context)
disabled = dict(report.quotient.initial_state_classes)["disabled"]
try:
    report.quotient.next_class(disabled, "consume")
except UndefinedTransition:
    pass  # 已认证的 ⊥，不是程序崩溃
```

用无自然语言标签的操作结果筛选候选微观组合规则：

```python
from bidirectional_modeling.examples import composition_rule_scenario

experiments, rules = composition_rule_scenario()
selection = engine.select_composition_rules(rules, experiments)

print(selection.selected_rule_names)  # ('parity',)
for candidate in selection.ranked:
    case = candidate.cases[0]
    print(
        candidate.rule.name,
        candidate.total_description_length,
        case.class_count,
    )
for rejected in selection.rejected:
    print(rejected.rule.name, [item.kind for item in rejected.counterexamples])
```

示例中的常量规则与错误偏支撑规则被操作反例直接排除；一个只在更深上下文才失败的过拟合规则虽然通过稀疏测试，但诱导出更多残差状态、更大的偏转移表和更长的区分上下文基，因此输给两状态的奇偶规则。向 `experiments` 加入更大尺度或留出枚举域后，候选必须逐域取得完整证书。

这里以 `transition(state, action, context)` 表示逐个叠加微观原子的柯里化组合 \(x \circ a\)；`action` 可以直接编码待加入的原子、图块或端口粘合操作，因此不要求一个宏观语义对应一层固定网络。

自动生成保守效果假设：

```python
from bidirectional_modeling.interpretation import ObservedEffectGenerator

interpretation = engine.interpret(
    design_candidates[0],
    context,
    ObservedEffectGenerator(horizon=goal.horizon),
)
print(interpretation.score_semantics)
for candidate in interpretation.candidates:
    print(candidate.hypothesis.level.value, candidate.ranking_score)
```

显式验证相邻尺度间的对应：

```python
from bidirectional_modeling.examples import scale_correspondence_scenario

correspondence, lower, upper, lower_context, upper_context = (
    scale_correspondence_scenario()
)
certificate = engine.verify_correspondence(
    correspondence,
    lower,
    upper,
    lower_context,
    upper_context,
    horizon=2,
)
print(certificate.passed)          # complete and commutes
print(certificate.counterexamples)
```

这里验证的是整个动态交换图，而不只是终态数值相等：对每个下层场景 `s` 和每个时间步 `t`，下层快照经 `projection` 后必须与 `scenario_projection(s)` 指向的上层快照等价。投影与场景映射由调用方持有，因此候选模型不能自行改变判据。

跨上下文复核使用验证套件：

```python
from bidirectional_modeling.examples import scale_correspondence_suite

correspondence, cases = scale_correspondence_suite()
suite = engine.verify_correspondence_suite(correspondence, cases)
print(suite.compatibility_passed)       # 所有已声明用例相容
print(suite.has_independent_holdout)    # 至少一个独立留出声明
print(suite.passed)                     # 两者同时成立
for result in suite.cases:
    print(result.case_name, result.certificate.lower_context_fingerprint)
```

开放式结构搜索可实现 `CandidateGenerator` 或使用 `ParametricCandidateGenerator`；已有设计库可直接传入序列或使用 `RegistryGenerator`。领域目的解释可实现 `HypothesisGenerator`，也可使用 `CatalogHypothesisGenerator` 提供明确候选。

## 七个内置验收场景

1. **软件行为**：多个可靠工作器形成帕累托候选；丢数据的“指标钻空子”方案被不变量拒绝；只在短期可靠的方案被延长时间探测反驳。
2. **科学动力学**：位置相同但速度不同的状态未来分化；系统给出非闭合见证，人工批准把速度提升为宏观状态。由于该示例状态空间无界，有限搜索在细化后只报告“未发现反例但证明不完整”，不会宣称全局闭合。
3. **残差语义商**：三个当前观察相同的不透明初态经 `probe` 暴露两种未来结果；框架自动拆分未来行为不同的状态，同时合并只有无关微观副本编号不同的状态。
4. **局部动作支撑**：两个当前观察相同的状态只有一个支持 `consume`；残差商将“有后继”和 `⊥` 作为不同操作行为，并产生支撑不闭合见证。
5. **组合规则选择**：常量规则、错误支撑规则和深层过拟合规则与正确奇偶组合规则竞争；前两者由反例排除，后者因残差商和上下文基更复杂而在描述长度上落败。
6. **组织机制**：同一审批结构兼容防欺诈、审计和中央控制等多个解释；系统保留不可识别性、限制弱证据意图推断，并选择区分候选的信息增益问题。
7. **跨尺度对应**：两个不同的微观分量划分映射到同一个宏观总量状态；验证器先在已知划分上校准，再用未见划分作独立留出复核，确认多对一粗粒化在完整时间轨迹上与宏观动力学交换。

## 扩展接口

一个新领域至少提供以下适配之一：

- 可执行模型的 `simulate`、资源指标和可观测读出；第三方模型还需要调用方提供 `Context.scenario_manifest`，可选的 `scenario_count` 仅提供诊断信息；
- `CandidateGenerator`，把领域设计空间转换成模型候选；
- `CompositionRule` 与一个或多个 `CompositionExperiment`，比较共享枚举域中的候选微观组合方式；
- `HypothesisGenerator`，产生有上下文依据的效果、功能或意图候选；
- `RedTeamProbe`，表达领域特有的安全、长期性或反事实检查；
- 自定义 `Requirement`，验证基础字段比较以外的规则。
- `Correspondence`，声明下层快照到上层快照的投影，以及下层场景到上层场景的映射。

第三方模型的场景域由调用方声明，而不是由候选模型自行决定：

```python
from bidirectional_modeling import Context, ScenarioKey

context = Context(
    scenario_manifest=(
        ScenarioKey("case-a", "baseline"),
        ScenarioKey("case-b", "stress"),
    )
)
```

`independent_recovery=True` 是生成器对实验隔离的显式声明，不是安全边界；严格盲测仍应在生成阶段隐藏原始 `MacroSpec`，并使用留出的状态、干预和时域复核。

## 当前边界

- 自然语言到严格 `MacroSpec` 的展开仍属于领域适配层。模糊词应保留在 `ambiguous_terms` 中供用户选择，核心不会静默替用户定义。
- 核心给出搜索、验证和闭环协议，不声称解决任意开放世界的模型发现；候选空间、领域先验和实验接口仍需外部提供。
- 参考模型是确定性有限状态系统。连续、随机或高维系统需要实现相同协议，并提供相应误差界和统计证书。
- 残差商只相对于声明的读出、观察等价关系、动作集合、上下文和可达状态域成立；它产生操作语义类，不会自动赋予自然语言含义、功能或意图。
- 只有显式的 `UndefinedTransition`（通常由 `applicable=False` 产生）才是合法 `⊥`；转移函数的其他异常仍是未知边并使完整性证明失败，避免把实现错误伪装成领域偏函数。
- 证明所用的状态、上下文和规范值必须由基本类型、枚举及其普通容器组成；不透明对象或循环容器没有稳定结构身份，因此会被明确拒绝，而不会退回进程相关的 `repr` 或对象地址。
- 反例引导的 `context_basis` 足以重建当前枚举域中的残差分区，但当前贪心顺序不保证它是所有可能测试集合中基数最小或描述长度最短的一组。
- 组合规则选择只排除与声明实验冲突或无法取得残差证书的规则；在所有给定上下文上观察等价的规则不可辨识。`unique_selection` 只表示固定协议下存在唯一最短候选，不等于证明真实机制唯一。`description_length` 是调用方在固定编码器下提供的长度，不是框架从 Python 函数中推断的 Kolmogorov 复杂度。
- 权威场景清单全部覆盖时，即使惰性迭代器恰好触及模拟上限也可证明任务域完整；没有调用方场景清单的第三方模型，即使返回已耗尽的普通 `tuple` 或 `list` 也不能自行证明场景域完整。
- 对应证书只覆盖指定的两个模型、上下文、场景域和时间范围；投影函数是调用方声明的待检验假说，不是由有限数据自动识别出的唯一映射。
- `CorrespondenceValidationCase.independent=True` 是调用方对数据隔离和来源独立性的显式声明，不是安全边界；严格盲测仍需在外部阻止投影构造过程读取留出模型、场景和结果。
- `ScaleGraph` 中的多跳路径只具有逐边证书。若要主张微观到宏观的端到端对应，必须另外声明并直接验证该端到端 `Correspondence`，不能仅凭传递闭包推断。
- 可达状态搜索受深度和状态数预算限制；预算内未发现反例只会产生不完整报告，不会被误报为闭合证明。
- 分离特征只是反例相关的候选，不自动等同于因果变量；`refine_until_closed` 要求显式的 `feature_selector` 决策。
- 结构不能单独证明设计者意图。弱意图证据受到分数上限约束，真正的意图判断需要独立历史或主体证据。

## 开发验证

CI 在 Python 3.9、3.11 和 3.13 上运行全部单元测试、覆盖率门槛、JSON 演示，并额外构建和检查 wheel 内容。许可证为 MIT。
