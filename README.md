# Lean Engineer

**先复用，再实现；按风险验证，用证据交付。**

为 AI 编码建立可执行的工程纪律。Lean Engineer 把能力复用、最小实现、风险分档、机器质量门、独立审查和交付证据组织成一套完整工作流，适用于功能开发、代码修复、重构、技术选型、Review 与验收。

版本 **1.1.3** · [MIT License](LICENSE) · [主技能](skills/lean-engineer/SKILL.md) · [验证记录](VALIDATION.md)

## 为什么使用 Lean Engineer

把你反复提醒 AI 的工程要求，变成每次任务都有入口、执行条件和交付依据的工作规则。

| 你在意的问题 | Lean Engineer 的处理方式 |
| --- | --- |
| 项目明明有现成能力，却又写了一套 | 先查能力索引和真实源码，适用时直接接入，并验证实际调用方 |
| 一个局部需求，变成无关的大范围修改 | 按最小必要改动实施；每行变化都要能追溯到本次要求 |
| 小改过度折腾，大改检查不足 | 根据契约、调用方、数据、权限与失败后果分档 |
| 测试全绿，却没有真正检验业务要求 | 检查期望值来源、边界反例与测试发现错误的能力 |
| 实现者写完，再由自己宣布正确 | 对规定的高风险任务引入独立审查，并在修复后复验 |
| 审查一轮又一轮，没有收尾依据 | 保留发现编号、历史和处置结果，默认最多三轮审查 |

**少造轮子，少动无关代码，让每一次“完成”都能对应具体结果。**

## 六个核心能力

### 1. 优先复用，让项目能力持续积累

先找已有实现，再考虑标准库、原生平台和已安装依赖。确实需要新能力时，只实现当前需求；边界明确的可复用部分连同调用方法、使用条件和验证入口一起收录。

已有共享模块和索引就沿用，不另建平行体系。没有可收录对象时不创建空库，不为了假想需求提前搭建通用框架。

[能力库规则](skills/lean-engineer/references/reusable-capabilities.md)

### 2. 最小实现，保留必要保护

最小体现在职责、状态、依赖和分支，而不是把代码挤成最少行数。沿用项目结构和命名，只解决本次问题，同时保留输入校验、错误处理、安全措施和用户已经确认的功能。

[实现纪律](skills/lean-engineer/references/implementation.md)

### 3. 风险分档，把检查投入放到关键处

| 档位 | 处理重点 |
| --- | --- |
| 小改 | 相关检查、实际调用方和可观察行为 |
| 受控大改 | 明确的影响范围、契约与兼容证据、失败及恢复路径 |
| 厚审大改 | 风险专项检查、三道机器质量门与独立审查 |

金额、身份权限、不可恢复数据操作和设备安全等高后果变化另有独立审查要求。风险按行为与后果判断，修改一行也可能触及重要边界。

[风险分档](skills/lean-engineer/references/risk-gates.md)

### 4. 三道质量门，把工程要求落到检查上

厚审大改要求实际执行三道质量门：

| 质量门 | 检查什么 |
| --- | --- |
| 复杂度 | 新增或修改函数的逻辑复杂度；项目阈值优先，默认圈复杂度上限为 10 |
| 测试与变异 | 相关测试通过，变更代码的非等价变异被全部检出；等价项保留判断依据 |
| 架构约束 | 按项目已有机器规则检查分层、循环依赖、命名与位置边界 |

**验收要求明确，执行工具灵活。** 工具表是候选清单，优先复用项目已有配置；替代工具须核对当前官方文档、版本、维护状态与适用范围，再实际运行。

质量门由项目工具与 CI 执行。Lean Engineer 负责触发条件、执行要求和结果解释；缺少必要条件时保留阻塞状态，完整执行后再给出对应结论。

[验证与证据](skills/lean-engineer/references/verification.md) · [各语言工具候选](skills/lean-engineer/references/tooling.md)

### 5. 独立审查，既找问题也核对反证

审查者围绕需求、规范、安全边界和证据寻找反例，核对真实代码路径、触发条件与影响。支持问题的证据和推翻问题的证据都要检查。

发现保留稳定编号、来源、严重度、置信度、处置结果与复验方法；区分本次引入、既有问题和来源未知。修复后复验原问题及受影响行为，默认最多三轮，不为凑数制造发现。

附带只读审查 agent `lean-reviewer`，提供 Kimi 与 Claude 的适配入口。其他宿主可按独立审查协议组织可用角色。

[独立审查协议](skills/lean-engineer/references/review-protocol.md) · [审查 agent](agents/lean-reviewer.md)

### 6. 多领域按需加载，一个入口完成路由

| 领域 | 内置检查重点 |
| --- | --- |
| Web 前端 | 路由、组件、异步状态、表单、可访问性、布局、安全、性能与交付 |
| 后端 | API 契约、身份权限、事务、数据恢复、并发消息、资源安全与运行 |
| 移动端 | 生命周期、系统权限、端侧存储、客户端升级与设备条件 |
| 嵌入式 | 固件、中断、设备时序、硬件故障、安全状态与升级 |
| 数据工程 | 批流处理、指标口径、数据质量、历史重算与运行证据 |

按实际任务读取命中的专题。跨领域任务组合必要检查，共用同一套证据状态，合并重复工作。

安全变化还可触发历史对照、调用路径追踪和防护核验；状态机、竞态和协议任务可按需进入形式化验证流程。

[前端专题](skills/lean-engineer/references/frontend-review.md) · [后端专题](skills/lean-engineer/references/backend-review.md) · [安全差异审查](skills/lean-engineer/references/security-diff-review.md)

## 快速安装

### 使用 Skills CLI

```bash
npx skills add Joe-zizhen/lean-engineer --skill lean-engineer
```

按提示选择目标 agent 和安装范围。此方式安装主技能及其配套参考文件。

### 手动安装主技能

```bash
git clone https://github.com/Joe-zizhen/lean-engineer.git
```

把完整的 `skills/lean-engineer/` 目录放入宿主的技能目录。保留目录内的参考资料、脚本、`LICENSE` 和 `NOTICE`，不要只复制 `SKILL.md`。

根目录 `SKILL.md` 是兼容入口，实际技能规则以 `skills/lean-engineer/SKILL.md` 为准。

### 使用完整插件

仓库同时提供以下适配入口，按宿主选择：

| 入口 | 用途 |
| --- | --- |
| `plugin.json` | Agent Plugins 1.0 通用清单 |
| `kimi.plugin.json` | Kimi Code 插件 |
| `.claude-plugin/plugin.json` | Claude Code 插件 |
| `skills/lean-engineer/agents/openai.yaml` | Codex / ChatGPT 的技能元数据 |
| `agents/lean-reviewer.md` | 独立审查 agent 的适配定义 |

Kimi Code 中可使用：

```text
/plugins install https://github.com/Joe-zizhen/lean-engineer
/reload
```

Claude Code 可从克隆目录加载：

```bash
claude --plugin-dir ./lean-engineer
```

单技能安装与完整插件安装按宿主选择一种。独立审查角色的可用性由宿主决定；各入口说明见对应宿主文档。

## 怎么用

可以直接说明任务和约束：

```text
使用 lean-engineer 完成这个功能。先查现有能力，沿用项目结构，
按实际风险实施和验证，最后给出结果及对应证据。
```

```text
使用 lean-engineer 审查这次改动。保持只读，核对需求、调用方、
失败路径和测试依据，给出有证据的发现。
```

```text
使用 lean-engineer 验收这次修复。重跑原始复现，检查受影响的
调用方，并明确列出已通过、失败、受阻和未运行的项目。
```

## 一次任务如何推进

1. 明确本轮是实现、只读审查、验收还是工具搭建。
2. 动代码前同时读取风险分档和实现纪律。
3. 累计判断能力库、领域检查、验证和独立审查条件。
4. 选择满足需求的最小方案，实施并运行对应检查。
5. 对需要独立审查的改动提交证据，修复后复验。
6. 逐项对应用户要求，给出验证结果与交付状态。

项目已有更具体的规则时沿用项目要求，相同检查合并执行。读取规则不产生新的执行授权；只读角色保持只读，发布和风险接受由授权人员决定。

## 适用范围与证据

Lean Engineer 聚焦编码、审查和验收。纯产品需求、开放式方案设计和视觉设计由对应流程处理，再衔接实现。性能或容量的测量型诊断属于 Lean Engineer 的 Review 范围，需有工作负载与可执行的测量入口。

规则、领域专题和工具候选用于指导项目执行；实际验收以项目环境、真实检查结果和声明范围为准。后端台账脚本校验六专题的结构、引用与部分证据约束，其他领域按协议补充记录。

[验证记录](VALIDATION.md) 保留各版本的检查结果、执行环境与结论范围。[评估协议](evals/README.md)、[案例定义](evals/tasks.json)和[运行索引](evals/runs.json)提供可追溯的评估材料。

## 开发与验证

仓库内置包校验、审查台账校验、测试套件和两种打包格式。使用 Python 3.8 或更高版本的标准库即可运行这些仓库脚本：

```bash
python scripts/validate_package.py .
python -m unittest discover -s tests -v
python skills/lean-engineer/scripts/validate_review.py skills/lean-engineer/assets/backend-review-template.json
python scripts/package.py .
python scripts/package.py . --format skill
```

后端空白台账加上 `--require-ready` 应返回非零，防止未完成的台账被当作发布就绪。业务项目的质量门工具按其语言和构建环境配置。

## 许可证与致谢

本项目按 [MIT](LICENSE) 发布。第三方署名及许可文本保留在 [NOTICE](NOTICE)，包括 ponytail 与 pstack 的方法来源。

## 参考文档

- [Agent Skills 规范](https://agentskills.io/specification)
- [Agent Plugins 规范](https://github.com/agentplugins/agent-plugins-spec/blob/main/spec/1.0.0.md)
- [Skills CLI](https://github.com/vercel-labs/skills)
- [Kimi Code 插件](https://www.kimi.com/code/docs/en/kimi-code-cli/customization/plugins.html)
- [Claude Code 插件](https://code.claude.com/docs/en/plugins)
- [Codex Skills](https://developers.openai.com/codex/skills/)
