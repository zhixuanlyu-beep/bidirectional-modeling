# 情境转换、证书迁移与小证据验证

固定有限模型是每次计算的局部工作条件。框架允许通过显式转换扩展、限制、细化或重构这些条件，不要求所有情境属于预先存在的同一个全局状态空间。该实现不主张已经证明现实没有终极底层。

本轮核心是协议转换、证书迁移、部分排除与宏观充分性。布尔重构和拼接属于随包提供的可选扩展，通用调用链不依赖它们。

## 贯通验收

```bash
bidirectional-modeling context-demo --json
# 或 PYTHONPATH=src python3 -m bidirectional_modeling.cli context-demo --json
```

这个确定性有限域演示检查：

| 功能 | 预期结果 |
|---|---|
| 增加第二个实验 | 两个源响应类各拆成两个，未淘汰源响应 |
| 二元响应域扩大为包含 -1 的响应域 | 三点可加性反例不能迁移 |
| X=Y、Y=Z、X≠Z | 交叠投影一致，但没有整体赋值；给出三项冲突核 |
| 将 x 重构为 x AND z | 撤回可加承诺，由可执行模型产生 0001 响应 |
| 在只含常量、变量、取反的有界低阶语言中找替代 | 完整枚举后返回 absent；不推广到无限语言 |
| 用已认证部分预测排除 x | 2 次模拟；完整实验矩阵需要 8 次 |
| 宏观充分性证书 | 4 条历史观测保留 2 条，只读取保留记录即可复核 |

模拟次数节约属于这个例子，不是普遍加速保证。部分预测重放、冲突证书重验和历史证据读取分别计价。

## 情境与转换

`ModelingContext` 固定本地的 `SearchProtocol`、观测范围、分辨率、对象标识、模型语言与目标声明。字符串描述由领域适配方负责解释，不是自动生成的物理语义证明。

`ContextTransition` 使用三类显式有限映射：

- `experiments`：`(源实验名, 目标实验名)`，目前要求一对一；
- `responses`：`(源实验名, 目标响应, 源响应)`，必须显式声明，包括恒等翻译；
- `commitments`：`(源约束名, 目标约束名)`，只声明待验证的对应，不自动证明蕴含。

响应翻译方向为“目标响应回译到源响应”。细化与扩展目前检查相同的响应条件：必须覆盖全部源实验，每个目标响应行回译后必须能在源响应域中表示；类别名称是调用方提议，不单独证明结构细化或扩展。限制必须覆盖目标实验，并保留所有源响应在该限制下的表示。重构可以引入源域中没有对应的目标行为；但没有任何共享实验映射时只返回 `undecided`，不会把空对应记为有效关系。报告会计数，但不会自动继承任何证书。

`validate_context_transition` 返回 `valid / invalid / undecided`。`checked_properties` 列出实际检查通过的实验覆盖和响应行表示性质；`split_source_worlds`、`unrepresented_source_worlds`、`unmatched_target_worlds` 统计的是声明的响应行，不能称为结构候选被淘汰的数量。只有覆盖完整源实验时才报告拆分类数。

`bidirectional_modeling.context_network.ContextNetwork` 是可选的直接边容器，保存通过验证的直接转换，公开只读映射。名称相同而定义变化的节点被拒绝，应使用新的版本名。多条边不自动组成端到端证明；不同节点可以拥有独立响应域。

## 证书迁移与会话

```python
from bidirectional_modeling.certificate_transport import (transport_conflict, verify_transported_conflict)

receipt = transport_conflict(
    source_search, target_search, transition, source_certificate,
    source_evidence, target_evidence,
    evidence_links,  # (源 SearchObservation, 目标 SearchObservation) 对
)
if receipt.status == 'verified':
    assert verify_transported_conflict(
        receipt, source_search, target_search, transition, source_certificate,
        source_evidence, target_evidence,
    ) == 'valid'
```

迁移先重验源证书，再验证观测转译和目标约束对源约束的有限蕴含，最后在目标协议中重证矛盾。所得 `ConflictCertificate` 绑定目标协议；源证书指纹、转换指纹与证据来源连接保存在 `TransportedConflict` 中。

结果为 `verified / not_applicable / undecided`。`not_applicable` 只表示这条迁移路径没有证明继承，不代表新候选已经正确。重构改变了约束意义、旧实验无法回译或缺少来源连接时，不应静默继承反例。

`source_session.migrate_context(target_search, transition, target_evidence, evidence_links)` 创建新会话，保持源会话不变。未决时不发布新会话；已验证证书进入新会话，不适用的证书留在迁移记录中。新会话继续使用 schema 2，保存迁移来源事件；恢复时重验目标冲突证书。事件历史不是签名，独立重验跨情境迁移仍需提供原协议、转换和源证据。

会话迁移由 `search_session` 负责，一次迁移内只准备一次转换关系并供各证书使用；独立复核重新验证转换、源证书、证据链接、承诺含义及目标经验矛盾，不调用迁移生成入口。函数式入口从 `search_session` 导入；`certificate_transport` 不再提供会话接口别名。

数据来源标签与 evidence_links 都是调用方声明，不提供实验真实性或身份认证。转换也不会替代对实际校准条件的外部确认。

## 可选：局部—整体拼接

`LocalDescription` 是局部变量及其允许赋值关系。`GluingProblem.domains` 给出有限字符串域，默认整体类是其笛卡尔积；也可用 `global_assignments` 声明更窄的整体类。

```python
from bidirectional_modeling.extensions.gluing import LocalDescription, GluingProblem, solve_gluing

same = (('0', '0'), ('1', '1'))
different = (('0', '1'), ('1', '0'))
problem = GluingProblem(
    tuple((v, ('0', '1')) for v in ('x', 'y', 'z')),
    (LocalDescription('xy', ('x', 'y'), same),
     LocalDescription('yz', ('y', 'z'), same),
     LocalDescription('xz', ('x', 'z'), different)),
)
report = solve_gluing(problem, check_overlap=True, minimize_core=True)
assert report.overlap_consistent and report.status == 'absent'
```

`solve_gluing` 默认只检查整体存在性；`check_overlap=True` 请求重叠一致性，`minimize_core=True` 请求核极小化。整体检查先执行。`found` 携带整体赋值；`absent` 表示穷尽声明的整体类后没有赋值；`unknown` 保留预算未决。`overlap_consistent` 在未完成检查时为 `None`。重叠不一致与不存在整体赋值是两个不同检查，报告不会把它们混同。

存在性复核直接检查给定整体赋值的域、整体类成员资格和局部关系，不重新求解整个问题。不存在性复核检查所给冲突核；仅在声明极小时才逐项检查删除后的可满足性。

冲突核为包含意义下极小，不声称最小基数。重叠或核最小化中断时，已证明的 `found` 或 `absent` 仍成立；未完成重叠检查为 `None`，未完成极小化为 `core_minimal=False`。`verify_gluing_report` 重验实际核心和所声称的极小性，不信任外部标志。请求核极小化时，显式空整体类会返回空核心，表示失败来自整体类自身的限制。

此版本验证有限赋值关系；没有实现一般概率边缘分布的线性规划可行性求解。给局部支持附加均匀概率时，演示中的支持矛盾同样阻止联合分布，但一般的概率拼接还需要额外约束求解。

## 部分预测排除

```python
screen = lazy_search.screen_evidence(evidence, max_simulations=100)
for certificate in screen.certificates:
    # 根据 certificate.prediction.candidate 选择相应 candidate。
    check = verify_candidate_exclusion(
        protocol, candidate, cases, certificate, evidence,
        max_simulations=100,
    )
```

实验按协议声明的成本从低到高筛查，以 1、2、4、… 个实验的倍增前缀联合重放，末批覆盖剩余实验。每批保留两轮完整重放与漂移检查，避免逐次追加导致累计二次方工作量；代价是冲突可能到批次结束才被发现。一个已重放验证的预测与同域有效观测冲突，即停止该候选的后续模拟。报告同时给出 `excluded`、`matching_evidence` 和 `undecided`。

`matching_evidence` 仅表示吻合此次提供的数据，不证明完整预测、全部承诺或目标答案。部分筛查不会删改全目录，也不把部分行伪装为 `world`。撤回观测后再次筛查即可重新考虑候选。原有 `execute` 的完整响应语义保持不变；调用者使用筛查结果时必须保留个体排除证书，不能将缩减目录当作免费背景来证明原目录的宏观答案。

`verify_candidate_exclusion` 检查活跃观测依赖，并独立重放相应的部分实验矩阵。预算不足返回未决。个体证书不能用于排除模型族、后代或同名新版本。输入声明不变时的缓存仍依赖原有确定性/回调纯度约定；重验会检查实际执行。

## 可选：有界布尔重构与低阶覆盖

使用 `bidirectional_modeling.extensions.boolean` 显式导入本节接口。

`BooleanExpression` 是由常量、变量、取反、AND、OR、XOR 构成的规范 AST；二元交换操作按规范顺序排序。`replace(path, replacement)` 替换明确的子树，`to_model` 生成兼容既有模型验证器的可执行模型，布尔输入来自 `Context.environment`。

`BooleanLanguage` 明确变量、允许操作、常量开关及最大节点数。`enumerate_boolean_language` 只在穷尽该语言后声明 `complete=True`；预算截断不证明低阶类已被排除。`find_boolean_substitute` 比较指定实验输入上的响应，返回 `found / absent / unknown`；报告绑定目标 AST、语言和输入域，可用 `verify_boolean_substitute` 独立重验。目标不要求属于低阶语言本身。

替代搜索流式生成规范 AST 并立即比较，找到首个见证就停止。`found` 复核只检查见证的语言成员资格与指定输入响应，允许不同但有效的替代见证；`absent` 仍须穷尽有界语言。

描述长度采用版本固定的语言 JSON 头、节点数 gamma 编码和等宽前缀 AST token 计数，并返回现有 `DescriptionLength`。这是明确编码下的长度，不是任意 Python 代码的 Kolmogorov 复杂度。

`reconstruct_boolean` 接受原 AST/候选、替换路径、新 AST、目标语言及现有 `ReconstructionRule`。承诺按规则显式撤回/添加，新候选的完整预测和宏观答案由 `ExecutableSearchAdapter` 与权威目标表生成。未知或验证失败仍保留适配诊断，不能作为成功重构加入会话。

当前不自动发明任意对象边界、环境变量或自然语言概念；这些结构可以在后续领域语言中实现。布尔语言提供的是可检验的第一种结构重构后端。

## 宏观证书的三个层次

| 接口 | 证明对象 | 成本与限制 |
|---|---|---|
| `certify_macro_sufficiency` / `verify_macro_sufficiency` | 某个答案有相容见证，所有异义候选均被保留证据排除 | 贪心覆盖，不声称最优；复核不需要全部历史 |
| `compress_evidence` | 保留证据与完整历史具有相同答案集合 | 可保留多个答案；既有精确子集搜索 |
| `verify_macro(check_minimality=True)` | 既有压缩证书的最小基数主张 | 可能指数级；充分性与最小性分开报告 |

新充分性证书只包含原问题指纹、目标答案、保留观测、一个相容候选及每个异义候选的冲突观测。验证仍需绑定的原目录和响应数据，不能把目录准备成本隐藏为零，也不保证证书字节数很小。

`read_costs` 是观测到正整数读取成本的映射，默认每条为 1；它与实际采集成本分开。贪心生成不承担最优性承诺。未知预测、空版本空间或多个宏观答案时不生成确定答案证书。候选目录变化会使问题绑定失效。

复核者可以只提供保留的活跃原始观测；这些观测的来源真实性仍由实验方负责。没有保留证据，不能仅凭历史数据摘要或哈希恢复证明。

## API 和运行边界

核心没有第三方运行依赖，支持 Python 3.9+。接口迁移见 [变更记录](../CHANGELOG.md)。

新有限查询接受共享 `SearchWorkBudget`；该预算统计语义工作，不覆盖全部构造、哈希、排序、Python 指令或内存分配。布尔枚举和整体赋值穷举可能指数增长。部分预测另外使用模拟预算。

以上保证均相对于声明的有限域、模型语言及可靠证据。含噪声统计证书、一般概率拼接、无限语言完备性和任意跨框架自动翻译仍不在本版本保证内。


转换关系构建对源行的映射坐标投影建立一次索引，目标行回译后查找匹配源行。索引条目、目标查找、回译与输出关系均计入预算。
无映射坐标的重构可产生稠密关系，其输出仍可能为源域与目标域大小的乘积；预算不足保持 undecided。
布尔扩展在搜索与复核前检查全部声明变量的 bool 类型，包括目标或见证未读取的变量。
