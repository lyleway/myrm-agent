"""GeneralAgent 专用中间件 (Agent Middlewares)

包含 GeneralAgent 专用的 LangGraph Agent 中间件。

中间件列表:
- citation_rules_middleware: 当前 user turn 含外部来源时追加引用规则

注意: 共享中间件（如 user_instructions_middleware）位于 app.ai_agents.agent_middlewares
"""
