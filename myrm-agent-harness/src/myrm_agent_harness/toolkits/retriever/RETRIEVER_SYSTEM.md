# 检索系统设计文档

> 框架：`myrm_agent_harness.toolkits.retriever`

---

## 一、设计目标

提供一组检索原语，由记忆、Wiki、技能搜索等上层模块按需组合：

- **语义 + 关键词**：嵌入向量（语义）与 BM25（关键词）互补，提高召回率
- **可插拔**：嵌入模型、重排序服务与融合策略可替换
- **确定性**：RRF 融合带多级平局裁决，同样的输入总是同样的顺序

这个包不含「抓取页面后按问题取片段」的编排层：`web_fetch` 只读全文，`web_search` 把各搜索服务返回的结果直接合并。

---

## 二、系统架构

### 2.1 子模块职责与仓库内调用方

| 子模块 | 职责 | 仓库内调用方 |
|--------|------|--------------|
| **embedding/** | 文本嵌入（本地/云端）、嵌入窗口策略 | 记忆与 Wiki 的向量索引；服务端检索配置接口 |
| **bm25/**、**bm25_retrieval** | BM25 算法实现、分词器与 IDF 区分度词项选择 | 记忆的稀疏检索；技能搜索 |
| **reranker/** | 检索结果重排序（提高相关性） | 记忆的交叉重排门 |
| **splitter/** | 文档分块（多种分块策略）、嵌入前的预算切分 | Wiki 入库分块；记忆与 Wiki 的嵌入前切分 |
| **sufficiency/** | 检索充分性评估（LLM-based 后检索质量判断 + 负面约束检测） | `web_search` 的深度模式 |
| **cjk_tokenizer** | 确定性单趟保序 CJK/ASCII 分词与两档检索规划（无 C 扩展依赖） | FTS5 索引与 Wiki 检索 |
| **fusion_strategies** | RRF 融合 | Wiki 检索（全文 + 向量两路）；服务端会话搜索 |

### 2.2 一次混合检索怎么拼

调用方自己拼，包里没有统一的管道：

```
分块 (splitter) → 嵌入 (embedding) → 并行检索（向量 + BM25）→ 融合 (fusion_strategies) → 重排序 (reranker)
```

Wiki 检索把全文索引与向量两路结果交给 `rrf_fusion`；记忆的候选头部经 `reranker/` 的重排门精排。

---

## 三、融合策略

`fusion_strategies.py` 提供：

- **`rrf_fusion`**：倒数排名融合（RRF），支持列表级权重
- **`fuse_rrf_deterministic`**：带多级平局裁决与分数归一化的确定性 RRF，同时返回逐来源的召回调试信息

---

## 四、与 app/core/retriever 的关系

- **myrm_agent_harness.toolkits.retriever**：通用检索框架，零业务依赖
- **app.core.retriever**：业务层封装，集成知识库、权限、图存储等

---

## 五、相关文档

- [ARCHITECTURE.md](../../../../ARCHITECTURE.md) - 项目整体架构
- `embedding/` - 嵌入服务模块
- `bm25/` - BM25 实现模块
- `reranker/` - 重排序服务模块
