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
        """⏸ 占位 —— **全仓零调用**,连测试都没有(2026-09-22 grep 核过:
        `register` 这个名字的其余命中全是 `ToolRegistry.register` 与
        `SystemComposer.register`,跟本方法不是一回事)。

        ① 现状:没有任何代码路径会调用它。产品里"哪些字段可改"完全由构造时
           传进来的 `initial` 决定(`__init__` 里 `self._mutable = set(initial)`),
           运行期从不新增字段。
        ② 为什么留:它是"可变字段注册表"这个概念唯一的落点 —— 设计点写着
           "想要更多可变字段 → 先注册"(见本文件头 `docstring` 第 6 行、
           也就是 `character/state.py` 开头那段)。删了名字,那条设计点就只剩注释了。
        ③ 什么条件才启用:真做"未来插件机制"(某个插件在运行期把新字段注册成
           可改字段,`change_outfit` 的 schema 跟着长出来)时 —— 那时它是入口,
           而且必须**同时**解决"工具 schema 构造时快照了 `fields`"的问题:
           `make_change_outfit_tool` 在 `character/tools.py` 里一次性取
           `state.mutable_fields`,之后新注册的字段**进不了 schema**,光调本方法
           是没用的。
        ④ 手术:删掉本方法(body 2 行 + docstring)即干净,无任何连带 ——
           没有调用方、没有测试引用它。
        """
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
        """⏸ 占位 —— **产品路径零调用**;它是"测试专用 API 住在产品类里"。

        ① 现状:产品侧读状态只走 `project()`(投影成文本进 SYSTEM)与
           `set()`(改)。`make_change_outfit_tool` / `_apply_outfit` 都不调 `get`。
           唯一调用方是 `test/test_character.py` 的两处断言(读刚改过的值、
           读未注册字段拿 `None`)。
        ② 为什么留:那两条断言要验的正是本类的核心不变量 —— "注册过的能改、
           没注册的改不了"。`project()` 是给人看的自然语言投影,**拿它做断言
           又脆又绕**;`get` 是唯一能干净断言"字段值现在是多少"的口子。
        ③ 什么条件才启用:状态字段真的需要被产品代码**读单个值**时(例如某个
           工具或段落要按 `clothes` 分叉,而不是整体投影)。在那之前产品不该用它 ——
           单字段读取会绕过"状态投影成一段文本"这条设计点。
        ④ 手术:删掉本方法(1 行)后,必须同步改 `test/test_character.py` 的两处
           断言(`test_registered_field_can_change` 与 `test_unregistered_field_rejected`,
           各一处 `state.get(...)`)—— 改成断言 `project()` 的文本,或改用
           `state.project()`/新开一个专供测试的口子。**不删的理由**:只有 1 行,
           删了反而把那两条测试逼成绕的写法。
        """
        return self._fields.get(field)

    def project(self) -> str:
        """状态投影成文本,组装进人设上下文。"""
        return "、".join(f"{k}={v}" for k, v in self._fields.items())
