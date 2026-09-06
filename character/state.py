"""小夜子 · 角色状态

设计点(来自 VISION 讨论):
- 状态 = 数据,不是工具
- 只允许"注册过的可变字段"被工具修改;没注册的字段(如内衣内裤)改不了
- 想要更多可变字段 → 先注册(未来做成插件机制)
- 状态投影成文本,组装进人设上下文(SYSTEM)
"""

from __future__ import annotations


class CharacterState:
    def __init__(self, initial: dict[str, str]) -> None:
        # 注册表:可变字段 -> 当前值。构造时传入的字段即"已注册"。
        self._fields: dict[str, str] = dict(initial)
        self._mutable: set[str] = set(initial)

    @property
    def mutable_fields(self) -> list[str]:
        return sorted(self._mutable)

    def register(self, field: str, value: str) -> None:
        """(未来插件用)注册一个新的可变字段。"""
        self._mutable.add(field)
        self._fields[field] = value

    def set(self, field: str, value: str) -> tuple[bool, str]:
        """改状态:字段必须注册过。返回 (成功?, 说明)。"""
        if field not in self._mutable:
            return False, (
                f"字段 '{field}' 未注册,不可修改"
                f"(可改字段: {', '.join(self.mutable_fields)})"
            )
        old = self._fields.get(field)
        self._fields[field] = value
        return True, f"{field}: {old} -> {value}"

    def get(self, field: str) -> str | None:
        return self._fields.get(field)

    def project(self) -> str:
        """状态投影成文本,组装进人设上下文。"""
        return "、".join(f"{k}={v}" for k, v in self._fields.items())
