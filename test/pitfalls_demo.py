"""坑复现:原始数据取证 + 正常版对照。

每个场景两段:
  A) 踩坑版:剧本(含掐断点)+ 逐事件日志 + 下一轮输入
  B) 正常版:同一件事没发生坑 → 逐事件日志 + 下一轮输入
跑法:  py test/pitfalls_demo.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.loop import AgentLoop, StreamInterrupted
from core.session_log import SessionLog
from core.tools import Tool, ToolRegistry


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


class ScriptedLLM:
    """按"每轮剧本列表"流式:每次 stream() 消费下一轮;耗尽则直接 stop。"""

    def __init__(self, rounds: list[list]) -> None:
        self._rounds = list(rounds)

    def stream(self, messages: list, tools: list | None = None):
        items = (
            self._rounds.pop(0)
            if self._rounds
            else [{"kind": "finish", "reason": "stop"}]
        )
        for item in items:
            if isinstance(item, BaseException):
                raise item
            yield item


def make_env(rounds: list[list]):
    log = SessionLog("pitfall")

    def probe(args: dict) -> str:
        return f"ran probe with {args}"

    tools = ToolRegistry(
        [Tool(name="probe", description="探针", parameters={"type": "object"}, func=probe)]
    )
    loop = AgentLoop(log, ScriptedLLM(rounds), tools, system_prompt="you are a test")
    return log, loop


def run(title: str, rounds: list[list], user_input: str):
    log, loop = make_env(rounds)
    print(f"\n{'='*72}\n{title}\n{'='*72}")
    print("\n[剧本] 本次模型流(原始):")
    for r_i, rnd in enumerate(rounds):
        for item in rnd:
            if isinstance(item, BaseException):
                print(f"    round{r_i} · {type(item).__name__} ← 掐断/出错点")
            else:
                print(f"    round{r_i} · {dump(item)}")
    try:
        result = loop.run_turn(user_input)
        print(f"\n[turn 结果] reason = {dump(result.reason)}   steps = {result.steps}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n⚠ 异常抛给调用方: {type(exc).__name__}: {exc}")
    print("\n[日志] 逐事件原样:")
    for e in log.events:
        print(f"    #{e.seq:>2} {e.type:<18} {dump(e.data)}")
    print("\n[下一轮输入] derive_messages()(下一次调用会看到):")
    msgs = log.derive_messages()
    if not msgs:
        print("    (空)")
    for m in msgs:
        print(f"    {dump(m)}")
    return log


def scenario1_interrupted():
    pitfall = [
        [
            {"kind": "text", "text": "我先查一下"},
            {"kind": "tool_call", "index": 0, "id": "call_1", "name": "probe", "arguments_delta": '{"a":"partial'},
            StreamInterrupted(),
        ]
    ]
    normal = [
        [  # 同一件事,流没被掐:工具参数吐完 + 执行 + 回答
            {"kind": "text", "text": "我先查一下"},
            {"kind": "tool_call", "index": 0, "id": "call_1", "name": "probe", "arguments_delta": '{"a":"ok"}'},
            {"kind": "finish", "reason": "tool_calls"},
        ],
        [{"kind": "text", "text": "查到了: ok"}, {"kind": "finish", "reason": "stop"}],
    ]
    log = run("坑1: 工具参数吐到一半被掐断(用户取消)", pitfall, "帮我查一下")
    log = run("—— 正常版对照:同一句,流没被掐 ——", normal, "帮我查一下")
    msgs = [e for e in log.events if e.type == "assistant/message"]
    assert msgs and not any(e.data.get("interrupted") for e in msgs)


def scenario2_max_tokens():
    pitfall = [
        [
            {"kind": "tool_call", "index": 0, "id": "call_1", "name": "probe", "arguments_delta": '{"a":"b"}'},
            {"kind": "finish", "reason": "length"},
        ]
    ]
    normal = [
        [
            {"kind": "tool_call", "index": 0, "id": "call_1", "name": "probe", "arguments_delta": '{"a":"b"}'},
            {"kind": "finish", "reason": "tool_calls"},
        ],
        [{"kind": "text", "text": "跑完了"}, {"kind": "finish", "reason": "stop"}],
    ]
    run("坑2: 正在吐工具参数时 max-tokens 截断(finish=length)", pitfall, "继续")
    run("—— 正常版对照:同样吐工具,finish=tool_calls ——", normal, "继续")


def scenario3_interleaved():
    interleaved = [
        [
            {"kind": "tool_call", "index": 0, "id": "c0", "name": "probe", "arguments_delta": '{"a":"0a"'},
            {"kind": "tool_call", "index": 1, "id": "c1", "name": "probe", "arguments_delta": '{"a":"1a"'},
            {"kind": "tool_call", "index": 0, "id": "", "name": "", "arguments_delta": ',"b":"0b"'},
            {"kind": "tool_call", "index": 1, "id": "", "name": "", "arguments_delta": ',"b":"1b"'},
            {"kind": "tool_call", "index": 0, "id": "", "name": "", "arguments_delta": "}"},
            {"kind": "tool_call", "index": 1, "id": "", "name": "", "arguments_delta": "}"},
            {"kind": "finish", "reason": "tool_calls"},
        ],
        [{"kind": "text", "text": "都跑完了"}, {"kind": "finish", "reason": "stop"}],
    ]
    contiguous = [
        [  # 正常版:两个调用顺序吐完(最常见的真实形态),不交错
            {"kind": "tool_call", "index": 0, "id": "c0", "name": "probe", "arguments_delta": '{"a":"0a","b":"0b"}'},
            {"kind": "tool_call", "index": 1, "id": "c1", "name": "probe", "arguments_delta": '{"a":"1a","b":"1b"}'},
            {"kind": "finish", "reason": "tool_calls"},
        ],
        [{"kind": "text", "text": "都跑完了"}, {"kind": "finish", "reason": "stop"}],
    ]
    run("坑3: 一条流里两个工具调用交错到达(index 路由)", interleaved, "两个都跑")
    run("—— 正常版对照:同样两个调用,顺序吐完(不交错)——", contiguous, "两个都跑")


def scenario4_bad_json():
    pitfall = [
        [
            {"kind": "tool_call", "index": 0, "id": "call_1", "name": "probe", "arguments_delta": '{"a":'},
            {"kind": "finish", "reason": "tool_calls"},
        ],
        [{"kind": "text", "text": "done"}, {"kind": "finish", "reason": "stop"}],
    ]
    normal = [
        [
            {"kind": "tool_call", "index": 0, "id": "call_1", "name": "probe", "arguments_delta": '{"a":"real"}'},
            {"kind": "finish", "reason": "tool_calls"},
        ],
        [{"kind": "text", "text": "done"}, {"kind": "finish", "reason": "stop"}],
    ]
    run("坑4: 模型吐了残缺 JSON 参数(非截断,就是坏)", pitfall, "跑一下")
    run("—— 正常版对照:同样的参数,JSON 是好的 ——", normal, "跑一下")


def scenario5_network_error():
    pitfall = [
        [
            {"kind": "text", "text": "部分回答已经出来"},
            RuntimeError("connection reset"),
        ]
    ]
    normal = [
        [
            {"kind": "text", "text": "部分回答已经出来,这是完整结尾"},
            {"kind": "finish", "reason": "stop"},
        ]
    ]
    run("坑5: 流中途网络断开(异常)", pitfall, "测试网络")
    run("—— 正常版对照:同一句,流完整走完 ——", normal, "测试网络")


if __name__ == "__main__":
    scenario1_interrupted()
    scenario2_max_tokens()
    scenario3_interleaved()
    scenario4_bad_json()
    scenario5_network_error()
    print("\n===== 全部跑完 =====")
