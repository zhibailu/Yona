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
        """摘掉一个段。⏸ 占位(见下面整块标注)。"""
        return self._sections.pop(name, None) is not None

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """开/关一个段(不摘除,只让它不进 compose)。⏸ 占位(见下面整块标注)。"""
        sec = self._sections.get(name)
        if sec is None:
            return False
        sec.enabled = enabled
        return True

    # ⏸ **占位:上面 `unregister` / `set_enabled` 两个方法,产品零调用。**
    # (2026-09 清理时如实标注,不拆不删。)
    #
    # ① 现状:`grep` 全仓 —— 产品路径**一次都没调**
    #    (`server/` 与 `character/` 里没有 `.set_enabled(` / `.unregister(`)。
    #    ⚠️ `server/main.py` 的 `get_context_sources()` 里那个 `"enabled": True` **不是**这两个方法:
    #    那是 `/context/sources` 静态响应体里的一个 JSON 字段,连类型都不是
    #    `SystemSection.enabled`,别把它当调用点。)
    #    真正在用的是实验/测试侧:`prompt_lab/tool_recall.py` 里
    #    `composer.unregister("memory")`(拆掉记忆段做对照),以及
    #    `test/test_composer.py`(两个用例:`set_enabled` 两处、
    #    `test_unregister_removes_section` 两处)。
    #
    #    那**产品怎么停用一个段**?靠"**不注册不发**":
    #    `character/persona.py` 的 `build_small_night_composer()` 里是条件注册 ——
    #    `if situation and situation.strip(): composer.register(...)`,
    #    `extra_sections` 也是循环注册。装配期不注册,段就不存在,
    #    根本走不到 `enabled` 那一层。所以这两个方法是**另一条路子**
    #    (注册完再关),产品选了"压根不给它注册"这条更早、更干净的路。
    #
    # ② 为什么留着:**运行期改装配**是真需求 —— UI 上"关掉某段"必须是
    #    不重建 composer 就能生效的(重建 composer 要重跑整个 persona 装配)。
    #    `enabled` 字段本身也**仍在使用**(`sections()` 的过滤、`SystemSection`
    #    的构造默认),删这两个方法并不能顺带删掉那个字段。
    #    而且 `prompt_lab` 现在依赖 `unregister` 做段级对照实验 ——
    #    拆掉它等于拆掉一条已有的实验手法。
    #
    # ③ 什么条件才启用:出现**产品级的"运行期关段"需求**时 ——
    #    最可能是 UI 上给上下文源做勾选(`/context/sources` 那个端点已经
    #    在回"哪些源生效",但目前是硬编码的一行、没有写入口)。
    #    那一刻 `set_enabled` 就是现成的写入口,`sections()` 的过滤已经接好了。
    #
    # ④ 将来手术要删哪几行:本注释块 + `unregister`(签名 + 函数体,2 行)
    #    + `set_enabled`(签名 + 函数体,5 行)。
    #    连带改:`test/test_composer.py` 的 `test_unregister_removes_section`
    #    与另外两个 `set_enabled` 调用点(test/test_composer.py 里那两处 `c.set_enabled(...)`)、文件末尾的用例清单;
    #    `prompt_lab/tool_recall.py` 的 `composer.unregister("memory")` 那处(实验台就断在那里,得换个对照手法)。
    #    ⚠️ `SystemSection.enabled` 字段与 `sections()` 里的 `if s.enabled`
    #    **别一起删** —— 那是段级启停的存储,与这两个方法不是一回事。

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

    ⏸ **越界遗留(第 1/3 处):`"[可用工具用法]"` 这句是模型可见的文案,
    却写在内核里。**(2026-09 清理时如实标注,**不搬** —— 搬会改模型可见文本。)

    ① 现状:`_usage_text` 末尾那行 `return "[可用工具用法]\n" + "\n".join(lines)`
       —— 段标题字符串在 core;每个工具的 usage **正文**本来就在内容层
       (`character/tools.py` 的 `RECALL_USAGE` 等,经 `Tool.usage` 进来),
       只有这个标题漏在核心里。同类的 `WAKE_BUDGET_TEMPLATE` **已经归位**
       (`character/personas.py`),判例原文就在那里:
       「这句话曾写死在 engine 的 producer 里(文案混进装配代码) → 归位到这里:
        引擎只算时长填进 {gap},句子怎么说是**内容层的事**,想改只改这一处」。
       本条与它是**同一类**问题(内核里出现了模型会读到的句子),只是漏网。
       ⚠️ **不要自己搬**:搬会改模型可见文本(哪怕只差一个换行),而那要重跑
       对模型的评测 —— 属用户拍板范围。
    ② 将来要搬,动哪几行、内容层加什么常量:
       - `composer.py`:`make_usage_section` 里 `return "[可用工具用法]\n" + ...`
         这一行 —— 改成 `return USAGE_SECTION_TITLE + "\n" + ...`,
         并从内容层取值(见下);顺带决定 `- {name}: {usage}` 这个**条目格式**
         要不要一起归位(它也是内核拼的,见同一行的列表推导)。
       - `character/personas.py`:新增一个常量,如
         `USAGE_SECTION_TITLE = "[可用工具用法]"`(放系统口吻区,与
         `LIFE_EVENT_PREFIX` / `WAKE_BUDGET_TEMPLATE` 同一片);
         段标题不属于 persona,别塞进 `PERSONA`。
       - 接线:`make_usage_section(...)` 的调用处(`character/tools.py` 一侧的
         装配)把它传进来,或给本函数加一个 `title: str = <现串>` 形参。
       - 连带:`core/composer.py` 模块头那句"内核不写文案"的分层说法、
         `docs/public/ARCHITECTURE.md:175` 那条"工具侧文案住在 tools.py"的更正要复核。
    ③ **默认值必须与现串逐字相同**:`"[可用工具用法]"` —— 含方括号、无空格。
       写成别的(加空格 / 换全角括号 / 改字)会**改掉所有轮次的 SYSTEM**,
       而这是每次调用都进上下文的段,改动会静默影响全部对话。
       搬完必须能证明输出逐字节不变(用同一份注册表对拍)。
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

    ⏸ **越界遗留(第 2/3 与 3/3 处):本段的两处模型可见文案也写在内核里。**
    (2026-09 清理时如实标注,**不搬** —— 搬会改模型可见文本。)

    ① 现状,两处:
       (a) `_fmt()` 里那四个时间说法 —— `"刚刚"` / `f"{...} 分钟前"` /
           `f"{...} 小时前"` / `f"{...} 天前"`(含它们各自的取整规则);
       (b) `_timeline_text` 末尾那行 `f"[时间线] 距上次和主人说话: {_fmt(gap)}"`
           —— 整句都在 core,**连换行/空格/冒号都算**。
       判例同上(第 1 处标注里引的 `character/personas.py` 的
       `WAKE_BUDGET_TEMPLATE`「曾写死在 producer 里 → 归位」那段):
       同类的句子已经归位,这两处是**漏网的例外**。
       ⚠️ **`"主人"` 是把 `VALUES["owner"]` 抄死了。** 关键差别在于:
       **producer 通道不插值** —— `interpolate()` 只作用在 `template` 上,
       `producer` 的返回值是**原样出段**的。所以这里写死"主人"之后,
       内容层改 `VALUES["owner"]`(改称呼)**不会**影响这一句 ——
       改称呼必须**回来改 core**。这正是"文案住在内容层"这条边界要防的事
       (`character/personas.py` 的 `VALUES = {"owner": "主人"}` 才是它该取值的地方)。
    ② 将来要搬,动哪几行、内容层加什么常量:
       - `composer.py`:(a) `_fmt` 整个函数(四个分支的字符串);
         (b) `_timeline_text` 的 return 一行。
       - `character/personas.py`:新增常量,建议两个 ——
         `TIMELINE_TEMPLATE = "[时间线] 距上次和主人说话: {gap}"`(**字符串里把
         "主人"替换成 `{owner}`,让它走 `VALUES["owner"]`**)与一组时长说法
         (如 `TIMELINE_JUST_NOW` / `_MINUTES` / `_HOURS` / `_DAYS` 四个模板,
         或一个 `(上限秒数, 模板)` 的表)。放系统口吻区。
       - 接线:与第 1 处同款 —— 经 `make_timeline_section(...)` 传入,
         或给本函数加带现串默认值的形参。⚠️ 若走 `{owner}` 插值,
         **`_timeline_text` 是 producer,不会自动插值** —— 必须自己调
         `interpolate(TIMELINE_TEMPLATE, values)`,否则 `{owner}` 会原样
         出现在 SYSTEM 里(那比抄死更坏)。
       - 连带:`docs/pitfalls/HISTORY.md` 那条「[时间预算] 句子写死在 engine」
         的同类条目可以补一条;`docs/public/ARCHITECTURE.md:175` 的更正也要改。
    ③ **默认值必须与现串逐字相同**:
       `"[时间线] 距上次和主人说话: {gap}"`(方括号、全角冒号 `:`,
       逗号后**一个**空格),以及 `"刚刚"` / `" 分钟前"` / `" 小时前"` /
       `" 天前"`(数字与单位之间**一个**空格)。
       差一个空格都会改掉所有轮次的 SYSTEM —— 本段每轮都进上下文。
       搬完必须逐字节对拍。
    """

    def _last_user_epoch(lg) -> float | None:
        """最后一条**真人**消息的时刻;还没跟真人说过话则 None。

        ⚠️ **每次 compose 现算,没有缓存,是 O(n) 扫描。**
        本段每个 step 都要出一次(`compose` 由 system builder 每 step 现取,
        见 `core/loop.py` 的 `_system_text`),所以这是**每 step 一遍全日志扫描**,
        而且落在她那一轮里。**量级未测** —— 现在按"日志量级还小"处理
        (一张卡的实测量级是几百到几千条事件)。
        真变慢时要收口的是**日志侧**(维护一个"最后真人时刻"的游标或索引),
        **不是在这里加缓存** —— 缓存会跟日志脱节,而本段的意义就是"现算的真相"。

        2026-09 清理(等价重构):原来扫的是 `lg.events` —— 那是个**返回副本的
        property**(`core/session_log.py`: `return list(self._events)`),
        于是每 step 先付一次**整份事件表的复制**,再线性扫。
        改成扫 `lg.of_type("user/message")`,语义**完全等价**:`of_type` 内部
        直接遍历 `_events`(不复制整表)、同样保序,只是在过滤时就把
        **根本不会命中**的事件(turn/step/assistant/chunk —— 日志的绝大多数)丢掉。

        ⚠️ 判据一个字没动,别"顺手"改宽:自走占位(`source="self"`)、
        compact(`source="compact"`)、编辑替身(`source="user-edit"`)
        **都不算**真人消息 —— 靠的仍是下面那句 `== "user"`;
        缺 source 的老消息按 `"user"` 处理(老日志不掉标,与
        `derive_messages` 的 `user_time_prefix` 同一口径)。
        """
        t = None
        for e in lg.of_type("user/message"):
            if e.data.get("source", "user") == "user":
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
