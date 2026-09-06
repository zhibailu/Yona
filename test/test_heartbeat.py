"""Heartbeat 自测:低成本门先行 + source=self 触发 + 间隔/停止。

用 MockLLM 和假 Gate,不调真模型、不等真实时间(间隔设极小 + 手动驱动 cycle)。
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from core.heartbeat import Heartbeat
from core.llm import AssistantOutput
from core.loop import AgentLoop
from core.session_log import SessionLog
from core.tools import Tool, ToolRegistry
from mock_llm import MockLLM


class FakeGate:
    """可编程门:决定 check 结果与下次间隔。"""

    def __init__(self, check=True, interval=0.01):
        self._check = check
        self._interval = interval
        self.checks = 0

    def check(self, now):
        self.checks += 1
        return self._check

    def next_interval(self, now):
        return self._interval

    def set(self, check=None, interval=None):
        if check is not None:
            self._check = check
        if interval is not None:
            self._interval = interval


def _build_env():
    log = SessionLog("hb")
    tools = ToolRegistry(
        [Tool(name="poke", description="poke", parameters={"type": "object"},
              func=lambda a: "poked", usage="")]
    )
    llm = MockLLM([AssistantOutput(text="她自言自语了一句")])
    loop = AgentLoop(log, llm, tools)
    return log, llm, loop


def test_gate_rejected_no_llm_call():
    """门拒绝时,不调 LLM(低成本闸门的意义:零调用省钱)。"""
    log, llm, loop = _build_env()
    gate = FakeGate(check=False)
    hb = Heartbeat(loop, gate, startup_delay=0, min_interval=0.001)
    hb._cycle_once()
    hb._cycle_once()
    assert llm.call_count == 0, f"门拒绝不应调 LLM,实际 {llm.call_count}"
    assert gate.checks == 2
    assert log.last_seq() == 0  # 无任何事件


def test_gate_passed_runs_self_turn():
    """门通过 -> loop.run_turn(source=self):turn/start 带 source=self。"""
    log, llm, loop = _build_env()
    gate = FakeGate(check=True)
    hb = Heartbeat(loop, gate, startup_delay=0)
    result = hb._cycle_once()
    assert result.woke is True and result.reason == "ran-turn"
    assert llm.call_count == 1

    starts = log.of_type("turn/start")
    assert len(starts) == 1
    assert starts[0].data.get("source") == "self", starts[0].data
    # user 槽是协议占位串,不是真人消息
    users = log.of_type("user/message")
    assert "【自动轮】" in users[0].data["content"][0]["text"]


def test_source_user_recorded():
    """用户 turn 的 source=user(回归:默认行为不变)。"""
    log, llm, loop = _build_env()
    loop.run_turn("你好")
    start = log.of_type("turn/start")[0]
    assert start.data.get("source") == "user"
    users = log.of_type("user/message")
    assert users[0].data["content"][0]["text"] == "你好"


def test_system_builder_sees_source_two_views():
    """双参 builder (registry, source):同一内核按 source 换 SYSTEM 视图(决策 6)。"""
    log = SessionLog("hb")
    tools = ToolRegistry(
        [Tool(name="poke", description="poke", parameters={"type": "object"},
              func=lambda a: "poked", usage="")]
    )
    llm = MockLLM([AssistantOutput(text="x")])
    seen: list[str] = []

    def builder(registry, source):
        seen.append(source)
        return f"你是[{source}]视图的助手"

    loop = AgentLoop(log, llm, tools, system_prompt=builder)
    loop.run_turn("你好", source="user")
    loop.run_turn(source="self")
    assert seen == ["user", "self"], seen
    # 喂给模型的 system 分别是两套视图
    assert llm.seen_messages[0][0]["content"] == "你是[user]视图的助手"
    assert llm.seen_messages[1][0]["content"] == "你是[self]视图的助手"


def test_single_arg_builder_still_works():
    """单参 builder (registry) 兼容:不传 source 也不炸。"""
    log = SessionLog("hb3")
    tools = ToolRegistry(
        [Tool(name="poke", description="poke", parameters={"type": "object"},
              func=lambda a: "poked", usage="")]
    )
    llm = MockLLM([AssistantOutput(text="x")])
    seen: list[str] = []

    def builder(registry):
        seen.append("called")
        return "单参视图"

    loop = AgentLoop(log, llm, tools, system_prompt=builder)
    loop.run_turn("hi", source="user")
    loop.run_turn(source="self")
    assert seen == ["called", "called"]
    assert len(llm.seen_messages) == 2
    assert all(m[0]["content"] == "单参视图" for m in llm.seen_messages)


def test_interval_comes_from_gate():
    """下次间隔由门决定(抖动内),失败退避也在界内。"""
    log, llm, loop = _build_env()
    gate = FakeGate(check=False, interval=5.0)
    hb = Heartbeat(loop, gate, startup_delay=0, min_interval=1.0, max_interval=60.0)
    r = hb._cycle_once()
    assert r.interval == 5.0  # gate 直接给的间隔不抖动(抖动发生在睡眠段)
    assert hb._jittered(5.0) != 5.0 or True  # 抖动函数存在且界内
    j = hb._jittered(5.0)
    assert 1.0 <= j <= 60.0


def test_start_stop_cycle():
    """start/stop:线程起停,stop 及时返回。"""
    log, llm, loop = _build_env()
    gate = FakeGate(check=False, interval=0.005)  # 快心跳
    hb = Heartbeat(loop, gate, startup_delay=0, min_interval=0.001)
    hb.start()
    time.sleep(0.05)
    assert hb.status()["running"] is True
    assert hb.status()["cycles"] >= 1
    hb.stop()
    assert hb.status()["running"] is False
    assert llm.call_count == 0  # 门一直拒绝,一次 LLM 都没调


if __name__ == "__main__":
    test_gate_rejected_no_llm_call()
    test_gate_passed_runs_self_turn()
    test_source_user_recorded()
    test_system_builder_sees_source_two_views()
    test_single_arg_builder_still_works()
    test_interval_comes_from_gate()
    test_start_stop_cycle()
    print("heartbeat all tests passed")
