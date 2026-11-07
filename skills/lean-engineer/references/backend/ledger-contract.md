# 后端审查台账协议

## 用途

`assets/backend-review-template.json` 是空白起点。它不包含审查结果。`scripts/validate_review.py` 检查结构、引用和部分证据约束。脚本不能确认记录是否真实。该格式只拥有后端六专题；移动端、嵌入式和数据工程的额外检查按 [领域证据与台账范围](../verification.md#领域证据与台账范围) 记录并参与整体交付判定。

所有 ID 必须在各自集合中唯一。所有 `*_ids` 必须引用已登记的 ID。字符串必须给出实际内容，不能用空字符串代替未知项。

## 顶层结构

| 字段 | 内容 |
| --- | --- |
| `schema_version` | 当前固定为 `1.3` |
| `project` | 基线、范围、访问能力、深度和专题清单 |
| `surfaces` | 本次审查的 API、事件、任务、迁移、数据存储、外部服务或配置 |
| `evidence` | 实际读取、执行、观察或测量的记录 |
| `checks` | 针对一个检查对象的原子检查 |
| `findings` | 有实际与预期差异的发现 |
| `release` | 发布声明及其证据 |

`project.mode` 为 `quick` 或 `full`。full 必须在 `required_topics` 中登记本次要求的专题。只有实际完成对应检查后，才能把覆盖写成完成。

## 检查对象

每个 surface 包含 `id`、`kind`、`name`、`location` 和 `risk`。

`kind` 可取 `api`、`event`、`job`、`migration`、`datastore`、`external_service`、`configuration` 或 `other`。`risk` 为 `high` 或 `normal`。

`location` 必须指向真实文件、符号、接口或配置。无法定位时，先完成定位，不得编造路径。

## 证据

每项 evidence 包含 `id`、`kind`、`locator`、`observed_at`、`summary` 和 `baseline_ref`。

`observed_at` 必须是带时区的 ISO 8601 时间。`baseline_ref` 必须指向证据对应的源码或构建基线。发布证据必须与 `project.baseline` 一致。基线不一致时，分开记录，不得混用结论。

| `kind` | 含义 |
| --- | --- |
| `source` | 实际读取的源码和位置 |
| `test` | 实际运行的测试命令、环境和结果 |
| `runtime` | 真实入口的操作和观察结果 |
| `measurement` | 指定条件下的测量条件和数值 |
| `specification` | 需求、合同或预期，不是实现通过证据 |
| `model` | 指定模型和边界内的验证结果 |

敏感内容必须脱敏。`locator` 可以是受控原件位置，不必复制完整响应。

## 检查

每个 check 包含：

- `surface_id`：一个已登记对象。
- `topic`：六个专题之一。
- `kind`：`static`、`behavior`、`measurement` 或 `model`。
- `question`：一个可判定的检查问题。
- `status`：`passed`、`failed`、`blocked`、`not_run`、`not_checked` 或 `not_applicable`。
- `evidence_ids`：支持当前结论的证据。
- `finding_ids`：关联发现。`failed` 至少关联一个发现。用于修复证据或模型重放时，检查必须为 `passed`，并且 `finding_ids` 包含对应发现。
- `reason`：阻塞、未运行或未检查时，为具体原因字符串。`not_applicable` 时，为包含 `code` 和 `detail` 的对象。`code` 固定为 `no_check_object`，`detail` 必须说明具体原因。
- `exemption`：`not_applicable` 时必需的对象。`basis` 固定为 `no_check_object`。`scope` 必须说明为什么没有检查对象。`capability` 必须为 `available`。

证据类型必须支持检查类型：

| check `kind` | 可接受的证据 |
| --- | --- |
| `static` | `source`、`test`、`runtime` |
| `behavior` | `test`、`runtime` |
| `measurement` | `measurement` |
| `model` | `model`、`test` |

`passed` 和 `failed` 都必须有证据。`blocked`、`not_run`、`not_checked` 和 `not_applicable` 都必须有原因。

所有类型的 `not_applicable` 都必须同时满足：

1. `exemption.basis` 为 `no_check_object`，`exemption.scope` 非空且不是占位值，`exemption.capability` 为 `available`。
2. `reason.code` 为 `no_check_object`，并且 `reason.detail` 具体且不是占位值。
3. 关联当前基线的 `specification`，或者提供有效的 `decision.authority` 与 `decision.reason`。

缺少工具、配置、规则或运行时时，必须使用 `blocked` 或 `not_run`。授权人员接受未执行风险时，可以另行记录决定，但不得把检查状态改成 `not_applicable`。

`exemption.scope`、`reason.detail` 和所有决定的 `authority` 与 `reason` 不得使用 `unknown`、`not needed` 或其他明确占位值。拒绝占位值不等于已经认证负责人身份或证据真实性。

## 发现

每个 finding 必须给出标题、实际结果、预期结果、影响、复现、修复、复验、负责人和根因。它必须关联至少一个检查对象。没有证据时，必须给出结构化 `evidence_gap`。

`subject` 为 `code` 或 `design`。代码发现使用当前实现作为主体。设计发现使用尚无代码的模型或设计作为主体。

严重度为 `P0` 至 `P3`。置信度为 `confirmed`、`probable` 或 `needs_validation`。根因使用相同的置信度词表。

状态为 `open`、`in_progress`、`fixed_unverified`、`resolved`、`accepted_risk` 或 `dismissed`。

- `confirmed` 至少需要一项非 `specification` 证据。
- 代码发现的 `evidence_ids` 或 `resolution_evidence_ids` 包含模型证据时，必须具有关联的 `test` 或 `runtime` 真实代码重放。重放证据必须包含在 finding 的 `evidence_ids` 或 `resolution_evidence_ids` 中，并且来自一个通过检查。该检查的 `finding_ids` 必须包含原发现，并且其 `surface_id` 必须与原发现一致。设计发现可以使用模型证据。
- `probable` 需要支持性证据。`needs_validation` 可以使用 `evidence_gap`。
- `resolved` 需要 `resolution_evidence_ids`。每项修复证据必须对应当前 `project.baseline`，并且来自一个通过检查。该检查的 `finding_ids` 必须包含原发现，并且其 `surface_id` 必须与原发现一致。代码发现至少一项为 `source`、`test`、`runtime` 或 `measurement`。设计发现还可以使用 `model`。
- `accepted_risk` 和 `dismissed` 需要 `decision.authority` 与 `decision.reason`。代理不得替用户接受风险。

## 覆盖与发布

`review_coverage_complete` 表示：

- 至少有一个对象和一项检查。
- 每个对象至少有一项检查。
- quick 中声明的专题都有对应检查。
- full 的 `required_topics` 和实际检查都覆盖全部六个专题。
- 所有检查都已 `passed`、`failed` 或有依据的 `not_applicable`。
- 所有检查均具有有效证据和关联。`not_applicable` 必须具有有效豁免，`failed` 必须关联有效发现。无效检查不计入完成覆盖。
- 不支持的 `schema_version` 不得计为完成覆盖。

覆盖完成允许存在失败发现。它不等于发布就绪。

`release_ready` 同时要求：

1. `release.status` 明确写为 `ready`。
2. `release.decision.authority` 和 `release.decision.reason` 记录授权决定，并且不得使用占位值。
3. 项目名称、基线、范围和对象位置不是空值或占位值。
4. 覆盖完成，并且至少一项检查为 `passed`。
5. 所有检查均为 `passed` 或有依据的 `not_applicable`。
6. 每个对象至少有一项 `behavior` 检查以 `test` 或 `runtime` 证据通过，或者具备当前基线的规格证据或授权豁免决定。
7. 没有处于 `open`、`in_progress` 或 `fixed_unverified` 的 P0、P1 发现。
8. 所有支持 `passed` 检查的证据都与 `project.baseline` 一致。`release.evidence_ids` 至少有一项关联到通过检查，并且其 `baseline_ref` 与 `project.baseline` 一致。
9. 任一结构、引用或证据约束失败时，`release_ready` 必须为 `false`。

`accepted_risk` 和 `dismissed` 不计入未解决发现，但必须有授权决定。

## 校验命令与退出码

```bash
python scripts/validate_review.py <review.json>
python scripts/validate_review.py <review.json> --require-ready
```

- `0`：结构有效。使用 `--require-ready` 时还表示发布条件满足。
- `1`：结构、引用或证据约束失败。
- `2`：结构有效，但 `--require-ready` 的条件未满足。

普通校验通过不表示发布就绪。发布就绪只表示台账满足上述规则。它不认证真实系统。
