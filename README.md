# 可验证的双向人机协作建模框架

这是“宏观目的 → 介观模型 → 微观结构”以及反方向解释的可运行参考实现。它不把两侧伪装成互逆函数，而是维护候选集合、证书、反例、置信度分量和可区分实验。

## 已实现的闭环

```text
MacroSpec G ── Realizer ──> Pareto{(Model, Certificate, Confidence)}
     ▲                              │
     │                              ▼
Concept refinement <── Counterexample / closure analysis
     │                              ▲
     └── Interpreter <── {effect, function, intention hypotheses}
```

- `Context Γ`：环境、尺度、历史证据、观察者、干预和假设。
- `MacroSpec G`：可观测量、目标、等价关系、不变量、约束、误差与时间范围。
- `FiniteStateModel M`：状态、转移、行动、读出、参数化资源指标。
- `SatisfactionEvaluator`：逐项检查 `M |=Γ G`，返回覆盖率、鲁棒余量和假设可靠性，而非单一黑箱分数。
- `Realizer`：接受设计库或参数化生成器，搜索、红队探测并输出成本/复杂度/风险/置信度的帕累托前沿。
- `Interpreter`：区分效果、功能和意图；意图缺少主体或设计证据时置信度被明确封顶。
- `ClosureAnalyzer`：构造 `x₁ ~ x₂` 但 `T(x₁) !~ T(x₂)` 的见证，并排序应提升为宏观变量的微观特征。
- `ConceptLibrary`：保存定义、正反例、边界、候选细化和版本。
- 往返检查：验证宏观语义保持和任务相关的微观行为等价，而不要求文字或结构完全相同。

## 运行

只需 Python 3.9+，没有第三方运行依赖：

```bash
cd outputs/bidirectional-modeling
PYTHONPATH=src python3 -m bidirectional_modeling.cli demo
PYTHONPATH=src python3 -m bidirectional_modeling.cli demo --json
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

也可以安装为本地包：

```bash
python3 -m pip install -e .
bidirectional-modeling demo
```

## 最小用法

```python
from bidirectional_modeling import (
    BidirectionalModelingEngine,
    HorizonExtensionProbe,
    Realizer,
)
from bidirectional_modeling.examples import software_scenario

goal, context, design_candidates = software_scenario()
engine = BidirectionalModelingEngine(
    realizer=Realizer(probes=(HorizonExtensionProbe(extra_steps=2),))
)
result = engine.realize(goal, context, design_candidates)

for candidate in result.candidates:
    print(candidate.model.name, candidate.confidence)
    for check in candidate.certificate.checks:
        print(check.name, check.passed, check.observed)
```

开放式结构搜索使用 `ParametricCandidateGenerator`；已有设计库使用 `RegistryGenerator`。向上推断可使用领域提供的 `CatalogHypothesisGenerator`，也可用 `ObservedEffectGenerator` 直接从行为生成保守的“效果”假设。

## 三个验收场景

1. **软件**：多个可靠工作器形成帕累托候选；丢数据的规格钻空子方案被不变量拒绝；仅在短期可靠的方案被延长时间探测反驳。
2. **科学**：位置相同但速度不同的状态下一步分化，系统给出非闭合见证并建议把速度提升为宏观状态。
3. **组织**：同一审批结构同时兼容防欺诈、审计和中央控制；系统保留多重解释，限制无证据的意图推断，并选择信息增益最高的问题。

## 扩展边界

核心不假设某个行业。新的领域需提供以下最小适配之一：

- 可执行模型的 `simulate`、资源指标和可观测读出；
- `CandidateGenerator`，把领域设计空间转换成模型候选；
- `HypothesisGenerator`，提供有上下文依据的功能/意图候选；
- `RedTeamProbe`，表达领域特有的安全、长期性或反事实检查；
- 自定义 `Requirement`，处理基础字段比较以外的验证逻辑。

自然语言到严格 `MacroSpec` 的语义展开属于领域适配层：模糊词必须保留为 `ambiguous_terms` 的候选解释，不能由核心静默选定。开放世界中的“真实意图”也不可能只从结构证明；框架会把这种不可识别性作为结果返回，而不是用最高分掩盖。

