"""Yona 新内核 · Agent 循环 —— 干净的单循环,流式消费(对齐 dsh 的 step())

循环规则:
- 每步:流式收 chunk -> 每条 append 成 assistant/chunk(原始真相)
  -> 喂给 Assembler(安全累积)-> finish 后拼出 assistant/message(安全投影)
- 消息里有 tool-call -> 执行工具 -> 结果写回日志 -> 再来一圈
- 没有 tool-call(或 finish=stop)-> 这就是答案 -> turn 结束
- finish=length(max-tokens)-> 丢弃未执行的 tool-call,turn 以 max-tokens 结束

两个触发源(VISION 决策 6):run_turn(source="user"|"self")
- user:真用户消息(现状)
- self:后台心跳自走,没有真人消息,user 槽自动放协议占位串
同一时刻只跑一个 turn(内部锁):角色一次只能做一件事。
"""

from __future__ import annotations

import inspect
import json
import threading
from dataclasses import dataclass
from typing import Any, Callable

from .assembler import FINISH_LENGTH, Assembler
from .llm import LLM, ToolCall
from .session_log import SessionLog
from .tools import ToolRegistry


@dataclass
class TurnResult:
    turn: int
    reason: dict[str, Any]
    steps: int


class StreamInterrupted(Exception):
    """流被中断(用户取消/连接断开)。循环应收尾为 aborted。"""


class AgentLoop:
    def __init__(
        self,
        log: SessionLog,
        llm: LLM,
        tools: ToolRegistry,
        system_prompt: str | Callable[[ToolRegistry], str] = "",
        max_steps: int = 20,
        on_chunk: Callable[[dict[str, Any]], None] | None = None,
        fold_tool_traces: bool = False,
    ) -> None:
        self.log = log
        self.llm = llm
        self.tools = tools
        # system_prompt:静态字符串,或 builder callable。
        # builder 每 step 现取,拿到本轮 registry(与 schema 同批工具);
        # builder 可按接受参数数拿到更多上下文:
        #   (registry)                    单参 —— 通用
        #   (registry, source)            两参 —— 触发源(自走/陪聊,决策 6)
        #   (registry, source, log)       三参 —— 本轮日志(可读 time_cursor,
        #   识别回放轮/普通轮,server 层据此切 persona 与时间源)
        self.system_prompt = system_prompt
        self._builder_arity = _builder_arity(system_prompt)
        self.max_steps = max_steps
        self.on_chunk = on_chunk  # 可观测性钩子:每个 chunk 回调(如终端打字)
        # fold_tool_traces:跨轮折叠工具痕迹(视图)。日志原文不动;
        # retain_result=True 的工具痕迹仍保留。默认 False = 忠实回放(业界主流)。
        self.fold_tool_traces = fold_tool_traces
        self._retained: set[str] = set()
        for name in tools.names():
            tool = tools.get(name)
            if tool is not None and tool.retain_result:
                self._retained.add(tool.name)
        self._last_finish: str | None = None
        # 同一时刻只跑一个 turn(VISION 决策 7):角色一次只能做一件事,
        # 用户消息来了若后台轮还在跑,她"正在忙"是合理设定。
        self._turn_lock = threading.Lock()

    # ---------- 对外入口 ----------

    def is_busy(self) -> bool:
        """此刻是否正在跑一轮(供 UI 显示"她在忙")。"""
        return self._turn_lock.locked()

    def run_turn(
        self,
        user_input: str | None = None,
        tools: ToolRegistry | list | None = None,
        source: str = "user",
        log: SessionLog | None = None,
        on_chunk: Callable[[dict[str, Any]], None] | None = None,
        self_note: str | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        max_rounds: int | None = None,
        system_prompt: str | None = None,
    ) -> TurnResult:
        """跑一轮。

        tools: 本轮开放的工具(子集)。None = 用构造时的全量注册表;
        传子集则本轮只给模型这些工具的 schema,也只执行这些(不给即不可用)。
        source: 触发源。"user" = 真人消息;"self" = 后台心跳自走,
        无真人消息,user 槽自动放协议占位串(防模型把占位当指令)。
        self_note: 仅 source="self" 生效 —— 自走轮的情境说明
        (如"刚睡醒,离线了很久"),替换默认协议占位串作为本轮 user 槽。
        调用方要自己保留"没人跟你说话"的语义(它是占位,不是指令)。
        已结束轮的自走 user 在投影中一律跳过,说明只在当轮可见,不污染后续历史。
        log: 本轮写哪条日志。None = 构造时的 log(单会话)。
        多会话(server):每次 run_turn 传目标会话的 log,loop 实例复用
        (llm/tools/builder 共享),事件各自落各自会话 —— 她这个人跨会话一致。
        on_chunk: 本轮流式回调(每 chunk 触发)。None = 用构造时的 on_chunk;
        显式传则覆盖本轮(server 每请求一个回调,多客户端互不串流)。

        可选字段(2026-09,产品设置面板真接线;不给 = 用默认):
        - temperature/max_tokens: 本轮 LLM 调用覆盖(None = 实例默认,
          服务端默认见 server/params.py;输出上限固定不暴露给用户)。
        - model: 本轮模型 id 覆盖 —— 仅限**同一端点内**换模型
          (同 key 同 base_url;跨厂商/端点请走 engine 连接管理,
          见 server/app/llm_setup.py)。None = 实例默认(.env 已弃用,
          服务端默认 = UI 连接配置的模型)。
        - max_rounds: 上下文窗口 —— 保留最近 N 个已结束轮(+当前轮),
          整轮裁剪绝不切散工具配对;0/None = 全量历史。
        - system_prompt: 本轮 SYSTEM 覆盖串(静态文本,替换 builder)。
          留空/None = 用构造时的旗舰 builder(人格/状态/世界照常注入)。
        """
        with self._turn_lock:
            return self._run_turn_locked(
                user_input, tools, source, log or self.log, on_chunk, self_note,
                temperature=temperature, max_tokens=max_tokens, model=model,
                max_rounds=max_rounds, system_prompt=system_prompt,
            )

    def _run_turn_locked(
        self,
        user_input: str | None,
        tools: ToolRegistry | list | None,
        source: str,
        log: SessionLog,
        on_chunk: Callable[[dict[str, Any]], None] | None,
        self_note: str | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        max_rounds: int | None = None,
        system_prompt: str | None = None,
    ) -> TurnResult:
        active = self.tools if tools is None else (
            tools if isinstance(tools, ToolRegistry) else ToolRegistry(tools)
        )
        turn = len(log.of_type("turn/start")) + 1
        log.append("turn/start", turn=turn, source=source)
        if source == "self":
            # 自走轮没有真人消息:user 槽放协议占位串,明示模型这轮没真人说话。
            # self_note:自走轮的情境说明(如"刚醒/睡了很久"),替换默认占位,
            # 让这轮的开场有真实情境。仍走 user 槽占位逻辑:已结束轮的自走
            # user 在投影中会跳过,所以说明只在当轮可见,不留历史。
            user_input = self_note if self_note is not None else (
                "【自动轮】此为自我触发的轮次,没有用户自主消息,此刻没有人跟你说话。"
            )
        log.append(
            "user/message",
            content=[{"type": "text", "text": user_input or ""}],
            source=source,  # 供时间线等派生:区分真人互动 vs 自走占位
            turn=turn,
        )

        step = 0
        reason: dict[str, Any] | None = None
        try:
            while step < self.max_steps:
                step += 1
                log.append("step/start", turn=turn, step=step)

                messages = self._build_messages(
                    active, source, log,
                    max_rounds=max_rounds, system_prompt=system_prompt,
                )
                blocks = self._stream_and_assemble(
                    turn, step, messages, active, log, on_chunk,
                    temperature=temperature, max_tokens=max_tokens, model=model,
                )

                tool_calls = [b for b in blocks if b.get("type") == "tool-call"]
                if self._last_finish == FINISH_LENGTH:
                    # max-tokens 截断:Assembler 已丢弃未执行的 tool-call
                    log.append("step/end", turn=turn, step=step)
                    reason = {"kind": "max-tokens"}
                    break

                if tool_calls:
                    self._execute_tools(turn, step, tool_calls, active, log)
                    log.append("step/end", turn=turn, step=step)
                    continue

                log.append("step/end", turn=turn, step=step)
                reason = {"kind": "completed"}
                break

            if reason is None:
                reason = {"kind": "max-steps"}
        except StreamInterrupted:
            # 中断:部分消息已由 _stream_and_assemble 以 interrupted=True 记好
            reason = {"kind": "aborted"}
        except Exception as exc:  # noqa: BLE001
            # 流内错误:日志由 finally 收尾(不伪造结果),然后抛出给调用方
            reason = {"kind": "error", "message": str(exc)}
            raise
        finally:
            log.append("turn/end", turn=turn, reason=reason)
        return TurnResult(turn=turn, reason=reason, steps=step)

    # ---------- 内部 ----------

    def _system_text(
        self, registry: ToolRegistry, source: str, log: SessionLog
    ) -> str:
        """取 SYSTEM:静态串直接用;builder 每 step 现取,按接受参数数喂上下文。"""
        sp = self.system_prompt
        if not callable(sp):
            return sp
        arity = self._builder_arity
        if arity >= 3:
            return sp(registry, source, log)
        if arity == 2:
            return sp(registry, source)
        return sp(registry)

    def _build_messages(
        self,
        registry: ToolRegistry,
        source: str,
        log: SessionLog,
        *,
        max_rounds: int | None = None,
        system_prompt: str | None = None,
    ) -> list[dict[str, Any]]:
        """喂给模型的完整输入 = system + 从日志投影出的历史。

        system_prompt 非 None = 本轮静态覆盖(替换 builder,persona 覆盖);
        历史按 max_rounds 轮窗口收口(0/None = 全量,整轮裁不切散配对)。
        """
        messages: list[dict[str, Any]] = []
        if system_prompt is not None:
            sys_text = system_prompt  # 本轮覆盖(UI"角色设定",留空 = 旗舰)
        else:
            sys_text = self._system_text(registry, source, log)
        if sys_text:
            messages.append({"role": "system", "content": sys_text})
        messages.extend(
            log.derive_messages(
                fold_tool_traces=self.fold_tool_traces,
                retained_tools=self._retained,
                last_turns=max_rounds if max_rounds else None,
            )
        )
        return messages

    def _stream_and_assemble(
        self,
        turn: int,
        step: int,
        messages: list[dict[str, Any]],
        registry: ToolRegistry,
        log: SessionLog,
        on_chunk: Callable[[dict[str, Any]], None] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """流式收 chunk:逐条记日志(原始真相),喂 Assembler,finish 后拼安全投影。

        temperature/max_tokens/model:本轮 LLM 调用覆盖(None = 客户端默认)。
        每次真实调用若供应商给了 usage,会以独立 usage chunk 到达 —— 记日志
        (assistant/chunk 原始层)并锚到本条 assistant/message 的 data 上
        (与 dsh 同款锚点:usage 以 (turn, step) 记在成功 assistant 消息上)。
        """
        assembler = Assembler()
        cb = self.on_chunk if on_chunk is None else on_chunk
        usage: dict[str, Any] | None = None
        llm_kwargs: dict[str, Any] = {}
        if temperature is not None:
            llm_kwargs["temperature"] = temperature
        if max_tokens is not None:
            llm_kwargs["max_tokens"] = max_tokens
        if model is not None:
            llm_kwargs["model"] = model
        try:
            for chunk in self.llm.stream(messages, registry.schemas(), **llm_kwargs):
                log.append("assistant/chunk", turn=turn, step=step, chunk=chunk)
                if chunk.get("kind") == "usage":
                    usage = chunk["usage"]  # 锚到本条 assistant 消息
                if cb is not None:
                    cb(chunk)
                assembler.push(chunk)
        except (KeyboardInterrupt, StreamInterrupted):
            # 中断:保留已流出的文本,工具调用一律不执行(不伪造结果)
            self._log_interrupted(assembler, turn, step, log)
            raise StreamInterrupted() from None
        except Exception as exc:  # noqa: BLE001
            # 流内错误(网络断开等):同样只留已流出的文本,然后抛给 run_turn
            self._log_interrupted(assembler, turn, step, log)
            raise

        self._last_finish = assembler.finish
        blocks = assembler.blocks()
        # 白名单过滤:本轮没提供的工具调用,在 message 层静默剔除。
        # 不执行、不喂 "tool unavailable" 错误 —— 那会把工具名泄露给模型,
        # 与折叠/mask 同哲学:不给 schema 就当它不存在。原始调用已在
        # assistant/chunk 层留痕(可观测性),只是不进投影、不触发执行。
        allowed = {t for t in registry.names()}
        blocks = [
            b
            for b in blocks
            if b.get("type") != "tool-call" or b.get("name") in allowed
        ]
        # 空消息(如 max-tokens 截断、或 tool-call 全被过滤且无文本)不写日志:
        # chunk 层已留原始真相,空 assistant 消息进历史会让下一轮 400。
        if blocks:
            data: dict[str, Any] = {"content": blocks}
            if usage is not None:
                data["usage"] = usage  # 锚点:本次 (turn,step) 调用的 token 用量
            if self._last_finish is not None:
                data["finish"] = self._last_finish  # stop|length|tool_calls|...
            log.append("assistant/message", turn=turn, step=step, **data)
        return blocks

    def _log_interrupted(
        self, assembler: Assembler, turn: int, step: int, log: SessionLog
    ) -> None:
        """中断时把已流出的文本收尾成一条 interrupted 消息(不伪造工具结果)。"""
        partial = assembler.interrupted_blocks()
        if partial:
            log.append(
                "assistant/message",
                turn=turn,
                step=step,
                content=partial,
                interrupted=True,
            )

    def _execute_tools(
        self,
        turn: int,
        step: int,
        blocks: list[dict[str, Any]],
        registry: ToolRegistry,
        log: SessionLog,
    ) -> None:
        for block in blocks:
            call = ToolCall(
                id=block["id"], name=block["name"], arguments=block["arguments"]
            )
            log.append(
                "tool/call",
                turn=turn,
                step=step,
                call_id=call.id,
                name=call.name,
                arguments=call.arguments,
            )
            try:
                args = json.loads(call.arguments) if call.arguments else {}
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            text, is_error = registry.execute(call.name, args)
            log.append(
                "tool/result",
                turn=turn,
                step=step,
                tool_call_id=call.id,
                content=[{"type": "text", "text": text}],
                is_error=is_error,
            )


def _builder_arity(builder) -> int:
    """builder 接受几个位置参数:0(静态/不可调)=0,1=(registry),
    2=(registry, source),3=(registry, source, log)。越界按 3 计。"""
    if not callable(builder):
        return 0
    try:
        sig = inspect.signature(builder)
    except (TypeError, ValueError):
        return 0
    positional = [
        p
        for p in sig.parameters.values()
        if p.kind
        in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL)
    ]
    return min(3, len(positional))
