# 可验证的双向人机协作建模框架

[![CI](https://github.com/zhixuanlyu-beep/bidirectional-modeling/actions/workflows/ci.yml/badge.svg)](https://github.com/zhixuanlyu-beep/bidirectional-modeling/actions/workflows/ci.yml)

这是“宏观目的 ↔ 介观模型 ↔ 微观结构”的可运行参考实现。它返回带证书、反例和边界的候选集合，支持实验限定的假设搜索与情境转换。

固定有限模型是每次计算的局部工作条件。情境之间可以扩展、细化、限制或重构；框架不要求所有情境属于同一个预设全局状态空间。

## 核心概念与主要路径

| 任务 | 入口 | 结果 |
|---|---|---|
| 从目的寻找实现 | `MacroSpec`、`Realizer` | 帕累托候选及满足性证书 |
| 检查跨尺度关系 | `CorrespondenceValidator` | 绑定映射、轨迹和协议的对应证书 |
| 分析一个模型的状态 | `ClosureAnalyzer`、`ResidualQuotientAnalyzer` | 闭合反例或有限残差商 |
| 在多个模型间搜索 | `ExperimentHypothesisSearch.query` | 相容、宏观异义或低阶替代查询 |
| 按需执行候选 | `LazyExecutableSearch` | 完整查询及证据限定的部分筛查 |
| 更换实验协议 | `ContextTransition`、`SearchSession.migrate_context` | 转换验证、冲突重证及新会话 |
| 减少证据复核 | `certify_macro_sufficiency` | 可仅读取保留观测的充分性证书 |

相容性、宏观异义和低阶替代共享统一查询接口，低阶替代直接使用 `LowerSubstituteQuery`。查询区分 `FOUND / ABSENT / UNKNOWN`，预算耗尽不能作为不存在的证明。

描述复杂度决定搜索顺序，矛盾继承决定可剪除的候选。共同材料、相似名称或父子关系本身都不能传播反例。

## 安装与运行

支持 Python 3.9+，核心没有第三方运行依赖。在仓库目录执行：

```bash
python3 -m pip install -e '.[test]'
bidirectional-modeling demo --json
bidirectional-modeling search-demo --json
bidirectional-modeling context-demo --json
```

不安装时可使用 `PYTHONPATH=src python3 -m bidirectional_modeling.cli search-demo --json`。

## 最小搜索示例

```python
from bidirectional_modeling.search_examples import conflict_search_scenario

search, observations = conflict_search_scenario()
report = search.search(observations)
assert report.compatible == ('xz',)
assert report.rejected == ('x',)
assert report.pruned == ('z',)
assert report.determined

basis = search.compress_evidence(observations)
assert search.validates_macro(basis, observations)
assert len(basis.retained_evidence) == 2
```

完整报告用于检查整个目录；简单存在性问题优先使用 `query`，可执行候选使用惰性查询。只需证明一个宏观答案时，可选择充分性证书；最小基数搜索单独请求并承担其成本。

## 可选领域模块

协议转换、证书迁移、部分排除和宏观充分性属于本轮核心范围。布尔重构与有限关系拼接随包提供，按需显式导入：

```python
from bidirectional_modeling.extensions.boolean import BooleanExpression, BooleanLanguage
from bidirectional_modeling.extensions.gluing import GluingProblem, solve_gluing
```

通用包导入和查询链不加载这两个领域模块。布尔搜索找到替代即停止；布尔与拼接的存在性证书直接验证见证。不存在及极小性主张仍需相应的穷尽检查。

领域接口仅从上述扩展模块导入；网络容器从 `bidirectional_modeling.context_network` 导入；基准从 `bidirectional_modeling.search_benchmark` 导入或通过 CLI 使用。已移除旧领域模块路径、顶层领域/基准导出和 `irreducible_against`，迁移方式见 [变更记录](CHANGELOG.md)。

## 文档

| 内容 | 文档 |
|---|---|
| 建模、对应、闭合和残差商示例 | [完整用法与边界](docs/usage.md) |
| 实验域、冲突继承与宏观证据 | [实验限定搜索](docs/experiment_relative_search.md) |
| 统一判断与独立复核 | [查询接口](docs/search_queries.md) |
| 按候选执行与缓存 | [惰性预测](docs/search_lazy.md) |
| 按实验预测与排除 | [部分响应](docs/search_partial.md) |
| 协议变化、证书迁移及可选扩展 | [情境转换](docs/context_network.md) |
| 历史变化与迁移说明 | [CHANGELOG](CHANGELOG.md) |

## 证明边界

- 结论相对于声明的有限候选、响应域、动作、上下文和证据；不推广为开放世界或无限语言的完备性。
- 证书绑定具体问题、协议和证据。哈希保证一致性检查，不证明来源真实；独立数据和校准声明由实验方负责。
- 回调边界隔离状态，证明型执行重放转移与读出。显式无定义转移是合法偏函数，普通异常与预算截断保留未知。
- 部分吻合不等于完整模型通过；排除证书只针对绑定的候选及活跃证据。重构或跨协议转换后需重证适用性。
- 描述长度依赖显式编码，排序分数不是概率；充分性不等于最小性，结构也不能单独证明意图。
- 闭合与残差共用内部可达状态探索，但各自保留判断、反例与证书规则。穷举与核最小化仍可能指数增长。

## 开发验证

```bash
python3 -m unittest discover -s tests -q
bidirectional-modeling search-benchmark --json
bidirectional-modeling search-updates-benchmark --json
```

CI 在 Python 3.9、3.11 和 3.13 上运行测试、覆盖率门槛、JSON 演示及 wheel 内容检查。许可证为 MIT。
