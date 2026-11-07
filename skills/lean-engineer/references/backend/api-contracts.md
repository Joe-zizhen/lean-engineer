# API 与契约

## 检查对象

列出 producer、consumer、端点或事件、版本、字段、单位、默认值和错误语义。包含列表、详情、搜索、批量、导出和异步状态查询。包含调试端点、旧版本和第三方 API 调用。

## 必须成立的不变量

- 字段结构、可空性、枚举、单位、时间和精度符合已发布契约。
- 新增可选字段不破坏真实消费者。严格反序列化和未知字段处理必须实测。
- 请求在信任边界完成结构和业务校验。内部类型声明不能代替运行时输入校验。
- 错误码、HTTP 状态、重试提示和结果未知语义一致。错误响应不泄露内部秘密。
- 分页、排序、过滤、游标和最大页长有确定语义。无界请求有明确上限。
- 文档、实际请求和实际响应来自同一基线。

## 必造反例

- 缺失、`null`、零、空串、未知枚举和额外字段。
- 数值溢出、精度边界、秒与毫秒、时区和夏令时边界。
- 负页码、越界页、失效游标、重复记录和超大请求。
- 旧 consumer 对新 producer，以及新 consumer 对旧 producer。
- 请求已执行但响应丢失，以及请求明确被拒绝。

## 证据与复验

运行 producer 与 consumer 契约测试。至少覆盖一个真实调用方。对外契约变化必须检查文档、schema、生成代码和手写 consumer 是否一致。

接口文档只能作为 `specification`。真实调用、集成测试或录制响应才能作为实现证据。

修复后复验合法请求、非法请求、旧调用方和错误语义。不得把“字段还在”当作兼容已经证明。

## 来源

- [OpenAPI Specification](https://spec.openapis.org/oas/latest.html)
- [JSON Schema](https://json-schema.org/specification)
- [HTTP Semantics RFC 9110](https://httpwg.org/specs/rfc9110.html)
- [OWASP API9 Improper Inventory Management 与 API10 Unsafe Consumption of APIs](https://owasp.org/API-Security/editions/2023/en/0x11-t10/)
