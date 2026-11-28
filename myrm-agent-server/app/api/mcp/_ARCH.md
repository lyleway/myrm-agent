# api/mcp/

## 架构概述

MCP 服务注册与健康 HTTP 层。上级文档：[../_ARCH.md](../_ARCH.md)。

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
|------|------|------|-------|
| `__init__.py` | 入口 | MCP memory endpoint mount helpers. | ✅ |
| `endpoint.py` | 模块 | Exposes the memory system as a stateless Streamable HTTP MCP endpoint with per-token agent scoping. A Bearer token is either a delegation credential (an external agent Myrm itself delegated to; resolved first, read only) or a Connect token (resolved via ConnectService, the whole tool set). Either way the middleware binds the agent's MemoryManager, wiki boundary flag and wiki tool bundle (when its profile enables wiki) and the desktop flag via ContextVar, then runs the MCP app inside the request's read-only scope. Stateless mode eliminates Mcp-Session-Id tracking since all tools are inherently per-request. Setup builds no MemoryManager (every request binds its agent's), so mounting never opens the vector store or reaches the embedding backend; it wraps the SDK's tool listing and tool call so a read-only request can neither see nor call a writing tool. | ✅ |
| `delegation_scope.py` | 守卫 | 委托凭据在 `/mcp` 上能做什么：只读。`READ_ONLY_TOOLS`（`memory_recall`、`memory_list`、`wiki_query_tool`）是允许名单，之后新增的工具对委托 Agent 默认关闭；`readable_only` 在工具列表端过滤，`require_readable` 在调用端拒绝（Agent 可以直接按名字调用从未列出的工具，所以光隐藏不是守卫）。`wiki_query_tool` 之所以算只读，是因为只读请求的 wiki 工具包按 `read_only=True` 构建，它的查询不会把答案连同调用方的问题写回知识库 | ✅ |
| `desktop_scope.py` | 模块 | 桌面控制工具在 `/mcp` 上的范围：请求级开关（ContextVar）、Agent 是否具备桌面控制、每个 Agent 一个的桌面会话与其清理 | ✅ |
| `wiki_scope.py` | 模块 | wiki 在 `/mcp` 上的范围：Agent 档案是否启用 wiki（memory→wiki 写入边界随之），以及按与进程内 Agent 相同的库解析与引擎工厂构建的 wiki 工具包（只读调用方得到不写回答案的查询；构建失败降级为 None，该请求看不到 wiki 工具） | ✅ |
| `origin_guard.py` | 守卫 | DNS-rebinding and origin validation ASGI middleware and helper for local Streamable HTTP / SSE MCP transports. | ✅ |
