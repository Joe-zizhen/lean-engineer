# 安全与隐私

## 检查对象

追踪不可信文本、HTML、URL、文件、模型输出、浏览器存储、构建产物、第三方脚本和遥测。标明数据从不可信变成可信的位置。

## 必须成立的不变量

- 输出按文本、HTML、属性、URL、CSS 或脚本上下文处理。框架默认转义不能覆盖所有危险输出位置。
- 私密凭据不进入浏览器产物、源码映射、日志或错误报告。公开配置与私密凭据分开。
- 前端隐藏、按钮禁用和路由守卫只负责体验。服务端负责不可绕过的授权和业务规则。
- 账号或租户切换后，缓存、持久化数据、待处理请求和草稿不会串用。
- iframe、postMessage、外链、上传和下载具有明确的来源与权限边界。
- 日志、埋点、回放和第三方请求遵循最小必要采集。

## 必造反例

使用无害样本检查原始 HTML、危险 URL、富文本、SVG、Markdown 和流式片段。

- 不可信窗口发送错误或过期消息。
- 修改下载对象 ID、导出参数和重定向目标。
- 切换账号后读取旧缓存或继续旧请求。
- 在日志和异常路径放置合成敏感标记。

## 最小证据与复验

- 在生产构建中追踪不可信输入到实际 DOM、URL、CSS 或脚本接收点。记录输入来源、输出上下文和采用的处理机制。
- 使用无害样本验证危险能力不可执行，合法格式与业务流程仍可用。框架默认转义、CSP 或 Trusted Types 不能脱离实际输出路径单独证明安全。
- 按风险选择输入类型、输出上下文、身份和来源组合。检查 `postMessage` 来源、iframe、第三方脚本、浏览器存储、遥测和 source map。记录组合分母和未查原因。
- 缺少服务端、存储或身份配置时标记 `blocked`，不得推断后端安全或越权。报告不得包含真实秘密或个人信息。

## 来源

- [OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/)
- [OWASP Cross Site Scripting Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html)
- [OWASP Content Security Policy](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)
- [MDN Window.postMessage](https://developer.mozilla.org/en-US/docs/Web/API/Window/postMessage)
