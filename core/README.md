# core/ —— 引擎内核(目录自己的文档)

> 依赖边界(STRUCTURE §1 权威):**core 不 import server/character**;纯逻辑、
> 单一职责、可测。产品/显示/角色的事不在这一层。

## 职责

事件源会话日志 + 单循环 + SYSTEM 段装配 + 工具驱动,谁都不 import:

| 文件 | 职责 |
|---|---|
| `loop.py` | AgentLoop:单循环,流式收 chunk → Assembler → 执行工具 → 再来一圈;source=user/self;busy 锁 |
| `session_log.py` | SessionLog:append-only 事件 / 回放 / 投影 / shadow+replace / 时间游标 |
| `composer.py` | SystemComposer + SystemSection:按优先级拼 SYSTEM,producer 动态段 |
| `heartbeat.py` | Heartbeat:后台节奏(醒→问 Gate→值得才自走);Gate 是可插拔接口 |
| `assembler.py` | 流式安全累积(工具按 index 聚合) |
| `tools.py` | ToolRegistry + 用法散文 |
| `llm.py` / `openai_compat.py` | LLM 接口 / OpenAI 兼容客户端(归一化 usage) |

## 本地拍板 / 边界(与内核相关的 ✅)

- 内核零显示/零实验台钩子:**"每次真实 LLM 调用都打输入"用装配层(lab 代理 / engine
  `_TracingLLM`)包,`core/loop.py` 不许为此改**(AI-GUARDRAILS §一.1)。
- `AgentLoop.run_turn` 已支持可选字段(temperature/max_tokens/model/max_rounds/
  system_prompt),供上层覆盖;`source=self` 无真人消息时 user 槽放协议占位串。
- 自走轮自语进上下文打标(`self_talk_prefix`)在**投影层**做,不是写日志时。

## 进内核前守则

lab/显示/调试诉求 → 先看装配层(prompt_lab、server/app/engine)能不能包;不能才谈
内核,而且要先报给用户。改动跑 `test/test_loop.py` 等对应测试。
