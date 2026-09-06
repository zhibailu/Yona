"""小夜子 · 工具集(动作 -> 状态副作用)

工具只做"有语义的动作",状态作为副作用更新;模型不直接写状态。
change_outfit 的 schema 只暴露"已注册的可变字段",没注册的字段模型根本看不到、也改不了。
"""

from __future__ import annotations

from typing import Any

from core.tools import Tool

from .state import CharacterState


def make_change_outfit_tool(state: CharacterState) -> Tool:
    fields = state.mutable_fields
    return Tool(
        name="change_outfit",
        description=(
            f"更换角色当前穿着的衣物。"
            f"可修改字段: {', '.join(fields)}。"
            "只改用户要求的字段,其余保持不动。"
        ),
        parameters={
            "type": "object",
            "properties": {f: {"type": "string"} for f in fields},
        },
        func=lambda args: _apply_outfit(state, args),
        usage=(
            f"用户让换衣服时用 change_outfit;只能改已注册字段: {', '.join(fields)};"
            "改完把当前的穿着告诉用户。"
        ),
    )


def _apply_outfit(state: CharacterState, args: dict[str, Any]) -> str:
    if not isinstance(args, dict) or not args:
        return "error: 请提供要更换的字段(如 {clothes: 卫衣})"
    changed: list[str] = []
    for field, value in args.items():
        ok, msg = state.set(field, str(value))
        if not ok:
            return msg
        changed.append(msg)
    return f"已更换穿着: {' | '.join(changed)}。当前: {state.project()}"
