# 有限状态声明与重放

`finite_replay` 提供受控的数据语言：状态名、初态、动作、全部状态/动作对的后继或 `null`、每个状态的读出、整数 cost/complexity/risk。读出仅支持字符串、整数、布尔和 null；v1 不支持浮点、表达式、动态环境或 Python 回调。必须显式声明 `noop` 的语义，缺失转移不是合法偏转移，只有显式 null 表示未定义。

```python
from bidirectional_modeling.finite_replay import FiniteModelDeclaration, DeclaredFiniteModel

declaration = FiniteModelDeclaration(
    name='switch', states=('off', 'on'), initial_states=('off',),
    actions=('noop', 'toggle'),
    transitions=(('off', 'noop', 'off'), ('off', 'toggle', 'on'),
                 ('on', 'noop', 'on'), ('on', 'toggle', 'off')),
    readouts=(('off', (('status', 'off'),)), ('on', (('status', 'on'),)),
)
model = DeclaredFiniteModel(declaration)
```

此模型通过数据 `search_signature()` 绑定，不依赖回调字节码指纹。模型类冻结，声明快照嵌套序列。用例仍需由调用方给出完整 `Context.scenario_manifest`，普通第三方模型的自报枚举不会升级为覆盖权威。显式未定义路径导致采集未完成并保留诊断。

## 归档和重放

```bash
PYTHONPATH=src python examples/approval_workflow.py --output-dir /tmp/approval
```

产生 `report.json` 与 `replay.json`。应用集成时调用 `create_replay_bundle(protocol, candidates, cases, target=..., world_answers=..., evidence=...)`；candidates 是现有 `ModelSearchCandidate`，模型必须为 `DeclaredFiniteModel`。cases 是现有 `ModelSearchCase`，v1 要求空 environment/history/assumptions、明确布尔策略及调用方场景清单。调用同时准备双轮预测并运行一次搜索，证据入口超预算时抛出 `SearchBudgetExceeded`，不写半成归档。

```python
from pathlib import Path
from bidirectional_modeling.finite_replay import replay_bundle

result = replay_bundle(Path('/tmp/approval/replay.json').read_text(encoding='utf-8'))
assert result.status == 'verified'
assert result.report.compatible == ('guarded',)
```

包内保存原协议、所有模型表和候选承诺/描述、完整用例声明、目标答案表、证据及来源、模拟和操作上限、生产包版本、格式与身份域清单、预测消耗及诊断、原会话与证书、搜索工作量。原会话具有自己的校验和，外层清单也具有校验和。原始外部实验录音/日志仍由采集方保存，来源文字不是真实性证明。

重放每次构建新的模型并双轮预测，核对问题、模型声明、批次及消耗记录，再复核活跃冲突并搜索。verified 表示归档核验完成；实际通过、歧义或空相容集合仍看 report。中断验证、预测未完成和搜索截断返回 undecided；预测或声明不一致返回 invalid；损坏及未知 schema 抛出 ValueError。默认文本上限 5 MB，是读取前已有字符串的大小检查，不是 JSON 构造的内存预算。

操作上限分别应用于保存会话核验、活跃证据检查和搜索阶段；双轮预测共用一个模拟上限。不是覆盖整个生命周期的单一总额。历史 work 是审计记录，不要求不同兼容实现有相同操作计数。

## 外部回调边界

普通 `FiniteStateModel`、闭包、外部配置和服务继续使用[证书迁移](certificate_migration.md)中的声明约定；改变行为须改变声明身份。搜索会话只保存它们已适配的离散结果和来源摘要，不保存代码、闭包或完整实验轨迹，不能声称它们可跨进程重建。有限表重放不会反序列化或导入用户代码。格式变化按[版本政策](version_policy.md)显式迁移。
