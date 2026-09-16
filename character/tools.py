"""小夜子 · 工具集

两类工具,区别不在代码在**谁来描述它**:
  1. 动作类(change_outfit):有状态副作用 —— 工具只做"有语义的动作",
     状态作为副作用更新,模型不直接写状态。文案 = 她做这个动作时怎么回事。
  2. 委派类(launch_subagent):没有状态 —— 派出去一件活,拿回一个结论。
     文案 = 她**什么时候该派人、怎么派得好**。这一条尤其要紧:人设里禁止
     写死能力清单(character/persona.py:62),所以"会不会用这个工具"几乎
     全部由这里的文案决定。

工具文案有两条通道,缺一条它就瞎半只眼(core/tools.py:11-19):
  description -> 进 tools[] 的 schema,教"调用格式"
  usage       -> 经 make_usage_section 进 SYSTEM 的 [可用工具用法] 段,教"怎么用得好"
★ 但 usage **只在走 composer 的 SYSTEM 里才到达模型**;SYSTEM 若是一条静态串,
  它就是死文字(真跑踩过,见 test/subagent_prompt_view.py 幕5)。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Sequence

from core.tools import Tool

from .state import CharacterState

# 委派回执的字段白名单。
# ⏳ 照抄 core/subrun.py 的 SubRunRecord 字段名;子运行的失败契约
#    还没收口(docs/protocols/SUBAGENT.md §8),收口后这里跟着定。
_RECEIPT_KEYS = ("run_id", "status", "detail", "steps", "duration", "usage", "output")


def _launch_usage(capabilities: Sequence[str]) -> str:
    """生成用法散文 —— **能力那句从工人的工具集来,不手抄**。

    规矩出处:`character/persona.py:62`「能力唯一来源 = 本轮 schema + 工具用法段;
    人设写死能力 → 工具子集变化时,模型仍以为有这工具」。这句话是"她能派出去
    做什么",和"她有什么工具"是同一类事实,所以同样不许写死 —— 写死就会在
    工具集变化后变成陈旧信息,而陈旧的能力描述会让她**凭假前提做决定**。

    capabilities 留空 = 不列举具体能力(只说"你做不了的事"),宁可不说不编。
    """
    if capabilities:
        head = (f"你自己做不了的事({'、'.join(capabilities)})一律派给它 —— "
                "你手上没这些工具,别凭印象编,派它去查。")
    else:
        head = "你自己做不了的事一律派给它 —— 你手上没这些工具,别凭印象编,派它去查。"
    # 以下三句是三轮真模型实验调出来的,改之前先看 test/subagent_prompt_lab.py
    # 的结论(长活那句是**降级成附赠**的:实测它推不动委派,留着只是因为无害)。
    return (
        head
        + "又长又乱的活也可以派,好处是中间过程不会留在对话里。"
        "它看不到你和用户的对话,派活时把要做的事写完整;"
        "拿回结论后,用自己的话说给用户听。"
    )


def make_launch_subagent_tool(
    runner: Callable[[str, str], dict[str, Any]],
    *,
    capabilities: Sequence[str] = (),
    name: str = "launch_subagent",
) -> Tool:
    """委派工具:把一件活派给一次性执行单元,拿回它的结论。

    runner(task, label) -> 结构化回执(dict)。**执行器不住在这里** ——
    工具只约定"给它任务、拿回事实"这一个形状。生产用的执行器是
    `core/subrun.py`,由装配处(server/app/engine.py)注入。这也是
    "拿本地弱模型当工人"的接口:换 runner 就换工人。

    capabilities: 工人手里那批工具的**短说法**(如 "上网查"、"翻本地文件")。
    由**装配处**给 —— 那是唯一同时知道工人注册表和这句话的地方。
    工具的完整文案(description/usage/schema)在能力句里只说用途、不列清单:
    她看不见包里有哪些工具名与参数,只看见"这包能干什么"。

    文案两段的读法(定稿时按这个次序读):
      description = 是什么 + 它看不见什么(格式)
      usage       = 什么时候派 + 怎么派好 + 拿到结论之后怎么办(用法)
    """

    def launch(args: dict[str, Any]) -> str:
        facts = runner(str(args.get("task", "")), str(args.get("label", "")))
        # 值为 None 的字段不写进回执(模型读 "usage": null 是纯噪音);
        # 0 保留 —— out 了 0 步和"没说"是两件事。
        receipt = {k: facts[k] for k in _RECEIPT_KEYS
                   if k in facts and facts[k] is not None}
        # 不加任何拟人化前缀:这里只给结构化事实(终态/耗时/血缘),
        # "怎么称呼这件事"是内容层的事,由 usage 散文交代。
        return json.dumps(receipt, ensure_ascii=False)

    return Tool(
        name=name,
        description=(
            "把一个费时或琐碎的活派给一个一次性执行单元,拿回它的结论。"
            "它看不到你和用户的对话,所以任务要写完整;它的中间过程不会进入对话。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "要它做什么。写完整 —— 它看不到你和用户的对话。",
                },
                "label": {
                    "type": "string",
                    "description": "一行标签,用来认出这次派的是哪件活(可省略)。",
                },
            },
            "required": ["task"],
        },
        func=launch,
        # ↓ 这段是**试出来的**,不是想出来的。三轮真模型实验(test/subagent_prompt_lab.py,
        #   共 126 次真调用,flash 档,温度 0.8~0.9)的结论:
        #
        #     她做不了的活(要上网查、要翻文件)  : 64/64 派
        #     她做得完的长活(1200 字原料整理)   :  6/52 派  <- 换任何措辞都推不动
        #     她做得完的短活                      :  0/10 派  <- 对照组,本该如此
        #
        #   所以**触发条件是"她没别的办法",不是"活很长"**。第一轮实验里
        #   五种"劝她派长活"的写法(命令式 / 划能力边界 / 情境段补一句 / 长短描述)
        #   全部无效,长活委派率仍趴在地上。所以能力那句写在最前面,长活降级成附赠。
        #
        #   ⏳ "你手上没这些工具"目前对**她**成立(产品给她的只有本工具与
        #      change_outfit)。哪天她自己拿到上网工具,这句话要跟着改 ——
        #      它描述的是"她手上的工具集",那是装配处的事实,不是文案的自由。
        usage=_launch_usage(capabilities),
        # 派活的结果跨轮保真:它是一次性的,重查不了(core/tools.py:17-19)。
        retain_result=True,
    )


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
