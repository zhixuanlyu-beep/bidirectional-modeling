# 可验证的双向人机协作建模框架

[![CI](https://github.com/zhixuanlyu-beep/bidirectional-modeling/actions/workflows/ci.yml/badge.svg)](https://github.com/zhixuanlyu-beep/bidirectional-modeling/actions/workflows/ci.yml)

这是“宏观目的 ↔ 介观模型 ↔ 微观结构”的可运行参考实现。它不假设两个方向存在唯一答案，而是返回带验证证书、反例、边界和排序分数的候选集合。

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
- `MacroSpec G` 明确可观测量、目标、等价关系、不变量、约束、误差与时间范围。
- 向下推断搜索满足 `M |=Γ G` 的模型，返回成本、复杂度和风险上的帕累托候选，而非虚构唯一实现。
- 向上推断严格区分效果、功能和意图。仅凭结构通常只能支持效果；功能依赖环境，意图还需要足够强的主体、设计或选择证据。
- 概率仅用于排序。解释结果中的 `ranking_score` 是未校准的相对分数，不是“目的为真”的概率。
- 单次 `Realizer` 调用中的验证预算由所有候选和红队探测全局共享。证书只有覆盖全部声明场景时才是 `complete`；部分覆盖即使所有已检查要求通过，也不能成为满足证书。
- 默认同时执行基线和显式干预，避免只在干预情形下通过、却在正常运行中失败。
- 目标是任务相关的最小充分模型，不是还原完整底层世界。

## 已实现组件

- `FiniteStateModel`：有限状态、转移、行动、读出和透明资源指标。
- `SatisfactionEvaluator`：惰性消费模拟轨迹，逐项检查要求并生成完整性、覆盖率、鲁棒余量和假设可靠性证书。
- `Realizer`：接受设计库或参数化候选生成器，执行验证和红队探测，保留主证书与探测证书，并输出帕累托前沿、被支配候选和被拒候选。
- `Interpreter`：生成或接收目的假设，用解释力、简洁性、鲁棒性和上下文证据排序，并提出信息增益最高的区分实验。
- `ClosureAnalyzer`：探索可达状态，构造 `x₁ ~ x₂` 但未来宏观结果分化的见证，并列出可供人工批准的分离特征。
- `refine_until_closed`：把闭合性反例接回规格细化；每次提升新可观测量后重新验证，直到闭合、预算耗尽或人工拒绝。
- `ConceptLibrary`：保存定义、正反例、边界、相关概念、候选细化和一致的版本历史。
- 双向往返检查：宏观往返比较规格语义，微观往返比较相同场景和干预下的任务行为等价，而不要求文字或内部结构相同。

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

开放式结构搜索可实现 `CandidateGenerator` 或使用 `ParametricCandidateGenerator`；已有设计库可直接传入序列或使用 `RegistryGenerator`。领域目的解释可实现 `HypothesisGenerator`，也可使用 `CatalogHypothesisGenerator` 提供明确候选。

## 三个内置验收场景

1. **软件行为**：多个可靠工作器形成帕累托候选；丢数据的“指标钻空子”方案被不变量拒绝；只在短期可靠的方案被延长时间探测反驳。
2. **科学动力学**：位置相同但速度不同的状态未来分化；系统给出非闭合见证，人工批准把速度提升为宏观状态后重新检查闭合性。
3. **组织机制**：同一审批结构兼容防欺诈、审计和中央控制等多个解释；系统保留不可识别性、限制弱证据意图推断，并选择区分候选的信息增益问题。

## 扩展接口

一个新领域至少提供以下适配之一：

- 可执行模型的 `simulate`、`scenario_count`、资源指标和可观测读出；
- `CandidateGenerator`，把领域设计空间转换成模型候选；
- `HypothesisGenerator`，产生有上下文依据的效果、功能或意图候选；
- `RedTeamProbe`，表达领域特有的安全、长期性或反事实检查；
- 自定义 `Requirement`，验证基础字段比较以外的规则。

## 当前边界

- 自然语言到严格 `MacroSpec` 的展开仍属于领域适配层。模糊词应保留在 `ambiguous_terms` 中供用户选择，核心不会静默替用户定义。
- 核心给出搜索、验证和闭环协议，不声称解决任意开放世界的模型发现；候选空间、领域先验和实验接口仍需外部提供。
- 参考模型是确定性有限状态系统。连续、随机或高维系统需要实现相同协议，并提供相应误差界和统计证书。
- 可达状态搜索受深度和状态数预算限制；预算内未发现反例只会产生不完整报告，不会被误报为闭合证明。
- 分离特征只是反例相关的候选，不自动等同于因果变量；`refine_until_closed` 要求显式的 `feature_selector` 决策。
- 结构不能单独证明设计者意图。弱意图证据受到分数上限约束，真正的意图判断需要独立历史或主体证据。

## 开发验证

CI 在 Python 3.9、3.11 和 3.13 上运行全部单元测试、覆盖率门槛、JSON 演示，并额外构建和检查 wheel 内容。许可证为 MIT。
