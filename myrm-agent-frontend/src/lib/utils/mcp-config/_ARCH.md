# lib/utils/mcp-config/

MCP 配置域子包：transport/keepalive 语义归一化、JSON 配置解析、扫描 finding 文案与服务器身份判重。**无** React 组件，全部纯函数。

对外唯一门面为 `index.ts` barrel（`@/lib/utils/mcp-config`）；组件层消费点一律经 barrel 取公开符号，不深路径引用内部构件。

`__tests__/` 覆盖归一化、解析与身份行为。

- `index.ts`：域门面 barrel — 显式导出清单聚合四文件公开符号，组件层统一导入入口；新增导出须同步本清单（漏加即 typecheck 断裂，编译器护栏）。
- `mcpConfigNormalizer.ts`：MCP 配置语义归一化 — transport 规范化（`http` → `streamable_http`）、keepalive 语义清零（`stdio` 无 keepalive）与单条/批量服务配置归一。
- `mcpConfigParser.ts`：MCP 配置解析器 — JSON 文本到 MCPServiceConfig 解析（parseMCPConfigsFromJSON / parseServerConfig），transport/keepalive 经包内 `mcpConfigNormalizer.ts` 归一；结果缓存 TTL/工具白名单保留 camelCase 与 snake_case 两种来源。
- `mcpServerIdentity.ts`：服务器身份 — 显示名对应的工具名 `mcp__{server}__{tool}` 里的 `{server}` 段（`mcpServerIdentity`，仅包内与测试使用）：ASCII 名字折叠后非空且不超过上界 `MAX_MCP_IDENTITY_LENGTH`（49，仅包内与测试使用）则取折叠结果；更长的 ASCII 名字取「折叠结果的可读开头 + 折叠结果的哈希」，所以 `a-b` / `a_b` / `a b` 仍是同一台服务器；其余名字取「折叠结果 + 显示名的 6 位 FNV-1a 哈希」。上界保证任何服务器的前缀 `mcp__{身份}__` 都给 8 位摘要留出位置，工具全名不会超过 64 个字符（OpenAI 文档写明函数名最长 64，Anthropic 为 128）；与后端 `toolkits/mcp/server_identity.py` 同一规则，两侧测试读同一份 `shared/config/mcp_server_identity.json` 向量。身份相同的两台服务器不能并存，所以新增服务器的入口都按身份判重：`findSameMcpServer`（表单校验、目录连接对话框的重复守卫与落库复核、目录卡片的连接状态）、`partitionByMcpIdentity`（JSON 导入，含同批内）、`uniqueMcpName`（注册表安装自动加 `-2`、`-3`）。反方向由 `mcpServerNameResolver` 承担：把工具名里的身份还原成用户起的名字（排在前面的服务器占有身份，与运行时先到先得一致），供工具行、审批标题与被拦下的操作行显示服务器名，而不是 `unnamed_dd4acd`。
- `mcpScanFindingText.ts`：MCP 扫描 finding 文案 — finding 字段/描述/建议/阻断消息格式化与 API 错误详情解析（parseMcpFindingsFromApiErrorDetails）；有译文用译文、没有就显示服务端原文，是否有译文用 `t.has(key)` 判断（next-intl 对缺失的键返回带命名空间的键路径，比较返回值永远不相等）；单测用真实 `createTranslator` 跑中英双语的 `mcpThreat` / `mcpThreatRec`。

## 依赖

- `@/store/config/types`（POS: 配置类型定义）— MCPServiceConfig / MCPScanFinding 类型。
- `@shared/config/mcp_server_identity.json`（POS: 名字与期望身份的向量）— 仅 `__tests__/mcpServerIdentity.test.ts` 读取，运行时代码不读。
