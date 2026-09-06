"""Yona 新内核 · SYSTEM 装配(SystemComposer)

把"SYSTEM 长什么样"从循环里剥出来:
- 各上下文段独立注册(name/priority/enabled),compose() 时按优先级拼成一条 system 文本
- 段的内容两种来源:静态模板(支持 {变量} 插值)或动态 producer(闭包拿状态/注册表)
- 插值值源是任意 dict(state 投影只是其中一种变量源)——VISION: RAG/记忆随时能作为新段接回

对齐 dsh 的 section 思想;取代旧 Yona `src/context/_composer.py` 的
"注册表 + 优先级 + producer 过滤器链"。旧版作用在 messages 列表上(插消息),
这里作用在**单条 system 文本的分段**上——更薄,循环仍只看到一条 system。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

# producer 签名: (values) -> 该段的文本;返回 None/空 表示本段不出现
Producer = Callable[[dict[str, Any]], str | None]


def interpolate(template: str, values: dict[str, Any]) -> str:
    """把模板里的 {name} 替换成 values[name]。

    逐字面替换而非 str.format:模板里可能出现别的花括号(如 JSON 示例),
    format 会误伤,replace 不会。
    未知键原样保留(不炸):模板作者引用了尚未提供的变量源时,
    compose 输出里能看见残留的 {name},可观测性兜底;报错会杀整个 turn,不值。
    """
    out = template
    for key, val in values.items():
        out = out.replace("{" + key + "}", str(val))
    return out


@dataclass
class SystemSection:
    """一个上下文段:名字 + 优先级 + 启停 + 内容来源(template 或 producer 二选一)。"""

    name: str
    priority: int = 100
    enabled: bool = True
    template: str = ""  # 静态内容,支持 {变量} 插值
    producer: Producer | None = None  # 动态内容(优先于 template)

    def render(self, values: dict[str, Any]) -> str | None:
        if self.producer is not None:
            return self.producer(values)
        if self.template:
            return interpolate(self.template, values)
        return None


class SystemComposer:
    """按优先级把启用的段拼成一条 system 文本。"""

    def __init__(self) -> None:
        self._sections: dict[str, SystemSection] = {}

    # ---------- 注册 ----------

    def register(self, section: SystemSection) -> SystemSection:
        if section.name in self._sections:
            raise ValueError(f"section '{section.name}' 已注册")
        self._sections[section.name] = section
        return section

    def unregister(self, name: str) -> bool:
        return self._sections.pop(name, None) is not None

    def set_enabled(self, name: str, enabled: bool) -> bool:
        sec = self._sections.get(name)
        if sec is None:
            return False
        sec.enabled = enabled
        return True

    def sections(self) -> list[SystemSection]:
        """按优先级升序(小的靠前)返回启用的段。"""
        return sorted(
            (s for s in self._sections.values() if s.enabled),
            key=lambda s: s.priority,
        )

    def names(self) -> list[str]:
        return [s.name for s in self.sections()]

    # ---------- 拼装 ----------

    def compose(self, values: dict[str, Any] | None = None) -> str:
        """把启用的段按优先级拼成一条 system 文本;空段(返回 None/空)跳过。"""
        values = values or {}
        parts: list[str] = []
        for sec in self.sections():
            text = sec.render(values)
            if text and text.strip():
                parts.append(text.strip())
        return "\n\n".join(parts)


def make_usage_section(
    registry=None, priority: int = 200, name: str = "tool_usages"
) -> SystemSection:
    """把注册表里所有工具的用法散文拼成一个段(通用,任何 ToolRegistry 可用)。

    从 usage 里提取的信息进 SYSTEM,教模型"怎么用得好";
    schema 进 tools[] 数组,教模型"调用格式"——两条通道各司其职。

    工具集一致性(P2 关键):producer 优先取 values["registry"](本轮实际开放的工具),
    没提供时退回构造时闭包的 registry。这样 run_turn(tools=子集) 时,
    SYSTEM 的用法段和 schema 数组永远指同一批工具,不会出现
    "SYSTEM 提到 change_outfit 但本轮 schema 里没有"的错位。
    """

    def _usage_text(values: dict[str, Any]) -> str | None:
        reg = values.get("registry") or registry
        if reg is None:
            return None
        lines = [f"- {name}: {usage}" for name, usage in reg.usage_entries()]
        if not lines:
            return None
        return "[可用工具用法]\n" + "\n".join(lines)

    return SystemSection(name=name, priority=priority, producer=_usage_text)


def make_timeline_section(
    log=None,
    now_epoch=None,
    priority: int = 16,
    name: str = "timeline",
) -> SystemSection:
    """会话时间线段:距上次真人互动多久(派生自日志,不是额外状态)。

    VISION 决策 8 的推论:世界 section 给"绝对时间"(当前时刻,时钟);
    本段给"相对时间"(距上次互动)——模型靠它区分"刚聊完"vs"久别"。
    数据从日志投影:最后一条 source=user 的 user/message 事件自带 time。
    没跟真人说过话(全新会话)则不出现本段。

    log / now_epoch 两个来源都可以**运行时现给**(引擎每轮日志不同):
      - 构造时可给 log / now_epoch(探针/单测,闭包);
      - 也可以构造时不给,compose 时经 values 传 "log"(SessionLog)
        与 "now_epoch"(秒级 float 或 callable)—— 引擎装配即此路径,
        同一只钟与世界 section 一致(注入"当前时间"时两段不打架)。
    now_epoch 缺省 = 系统时钟。
    """

    def _last_user_epoch(lg) -> float | None:
        t = None
        for e in lg.events:
            if e.type == "user/message" and e.data.get("source", "user") == "user":
                t = e.time
        return t

    def _fmt(seconds: float) -> str:
        if seconds < 60:
            return "刚刚"
        if seconds < 3600:
            return f"{max(1, round(seconds / 60))} 分钟前"
        if seconds < 86400:
            return f"{round(seconds / 3600)} 小时前"
        return f"{round(seconds / 86400)} 天前"

    def _timeline_text(values: dict[str, Any]) -> str | None:
        lg = values.get("log") or log
        if lg is None:
            return None  # 没日志可投影(构造/values 都没给)
        last = _last_user_epoch(lg)
        if last is None:
            return None  # 还没跟真人说过话
        now = values.get("now_epoch", now_epoch)
        if now is None:
            ts = time.time()
        elif callable(now):
            ts = now()
        else:
            ts = float(now)
        gap = max(0.0, ts - last)
        return f"[时间线] 距上次和主人说话: {_fmt(gap)}"

    return SystemSection(name=name, priority=priority, producer=_timeline_text)
