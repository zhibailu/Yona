"""子运行(SubRun)与队列(RunQueue)自测:
- 隔离:子运行有自己的日志,主日志只留 tool/call + tool/result(带 run_id)
- 不带人格:SYSTEM 由调用方给,core 里没有文案(core/subrun.py 无默认人格串)
- 会死:正常收尾=completed;撞上限/异常=failed,且**不外抛**(不拖垮父那一轮)
- 血缘:record.parent / run_id 可从主日志追到 run store
- 队列:并发上限、排队、终态、排队中取消、runner 抛异常不弄死 worker

跑: py test/test_subrun.py
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from core.llm import AssistantOutput, ToolCall
from core.loop import AgentLoop
from core.session_log import SessionLog
from core.tools import Tool, ToolRegistry
from lab.scheduler import QUEUED, RUNNING, RunQueue
from core.subrun import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_KILLED,
    SubRunSpec,
    SubRunStore,
    execute,
)
from mock_llm import MockLLM

SPEC_SYSTEM = "测试用任务说明"  # 内容层文案,测试自带(core 不提供默认)


def _spec(task: str = "干活", **kw) -> SubRunSpec:
    kw.setdefault("system", SPEC_SYSTEM)
    return SubRunSpec(task=task, **kw)


class _BoomLLM:
    """流式立刻炸:验证"子运行死了也不能拖垮父"。"""

    def stream(self, messages, tools=None, **kwargs):
        raise RuntimeError("boom")

    def invoke(self, messages, tools=None, **kwargs):  # pragma: no cover
        raise RuntimeError("boom")


class _SlowLLM:
    def __init__(self, delay: float = 0.15, text: str = "慢结论") -> None:
        self.delay = delay
        self.text = text

    def stream(self, messages, tools=None, **kwargs):
        time.sleep(self.delay)
        yield {"kind": "text", "text": self.text}
        yield {"kind": "finish", "reason": "stop"}

    def invoke(self, messages, tools=None, **kwargs):  # pragma: no cover
        time.sleep(self.delay)
        return AssistantOutput(text=self.text)


class _TruncatingLLM:
    """复现实测踩到的坑:推理模型的 reasoning_tokens 计入 output ——
    输出预算被"想"光,只有 finish=length + usage,一个正文字符都没有。"""

    def stream(self, messages, tools=None, **kwargs):
        yield {"kind": "finish", "reason": "length"}
        yield {"kind": "usage", "usage": {"input_tokens": 332, "cache_read_tokens": 0,
                                          "output_tokens": 1024, "reasoning_tokens": 1024,
                                          "total_tokens": 1356}}

    def invoke(self, messages, tools=None, **kwargs):  # pragma: no cover
        raise AssertionError("不该走到这里")


class _UsageStreamingLLM:
    """真客户端形状:正文 + 独立 usage chunk。验证 usage 不被重复计。"""

    def stream(self, messages, tools=None, **kwargs):
        yield {"kind": "text", "text": "结论在此"}
        yield {"kind": "finish", "reason": "stop"}
        yield {"kind": "usage", "usage": {"input_tokens": 100, "output_tokens": 20,
                                          "total_tokens": 120}}

    def invoke(self, messages, tools=None, **kwargs):  # pragma: no cover
        raise AssertionError("不该走到这里")


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# ---------- execute ----------


def test_execute_completes_and_distills_output() -> None:
    llm = MockLLM([AssistantOutput(text="整理完毕:三条要点。")])
    rec = execute(_spec("整理这段笔记"), llm)
    assert rec.status == STATUS_COMPLETED
    assert rec.detail == "completed"
    assert rec.output == "整理完毕:三条要点。"
    assert rec.steps == 1
    assert rec.ok
    assert rec.finished_at >= rec.started_at


def test_execute_maps_hitting_the_step_cap_to_failed() -> None:
    """撞上限 = 没干完,老实记 failed;不假装 completed。

    注意:模型调**白名单外**工具会被静默剔除(与父循环同哲学:不给就当不存在),
    于是那一轮会正常收尾 —— 所以这里必须调一个真给它的工具,才谈得上"停不下来"。
    """
    poke = Tool(name="poke", description="探针", parameters={"type": "object"},
                func=lambda args: "ok")
    script = [AssistantOutput(tool_calls=[ToolCall(id="c", name="poke", arguments="{}")])]
    rec = execute(_spec("停不下来的活", max_steps=2, tools=[poke]), MockLLM(script))
    assert rec.status == STATUS_FAILED
    assert rec.detail == "max-steps"
    assert rec.steps == 2


def test_subrun_tool_whitelist_is_isolating() -> None:
    """子运行只拿得到 spec 给的工具;没给的调了也不执行(静默剔除)。"""
    seen: list[dict] = []
    poke = Tool(name="poke", description="探针", parameters={"type": "object"},
                func=lambda args: seen.append(args) or "ok")
    llm = MockLLM([
        AssistantOutput(tool_calls=[ToolCall(id="c1", name="poke", arguments='{"n":1}')]),
        AssistantOutput(text="好了"),
    ])
    rec = execute(_spec("用一次工具", tools=[poke]), llm)
    assert rec.status == STATUS_COMPLETED
    assert len(seen) == 1
    assert rec.output == "好了"
    # 子运行的工具 schema 就是它自己那份,不含别的
    assert [t["function"]["name"] for t in (llm.seen_tools[0] or [])] == ["poke"]


def test_execute_never_raises_on_llm_error() -> None:
    rec = execute(_spec("会炸的活"), _BoomLLM())
    assert rec.status == STATUS_FAILED
    assert rec.detail.startswith("error: boom")


def test_failed_run_still_reports_usage_from_chunk_layer() -> None:
    """失败轮(撞输出上限、无正文)没写出 assistant/message ——
    用量只在 chunk 层留痕,成本账不能因此丢掉这活。"""
    rec = execute(_spec("被预算吃光的活"), _TruncatingLLM())
    assert rec.status == STATUS_FAILED
    assert rec.detail == "max-tokens"
    assert rec.output == ""
    assert rec.usage is not None
    assert rec.usage["output_tokens"] == 1024
    assert rec.usage["reasoning_tokens"] == 1024


def test_usage_is_not_double_counted_when_both_layers_carry_it() -> None:
    rec = execute(_spec("正常的活"), _UsageStreamingLLM())
    assert rec.status == STATUS_COMPLETED
    assert rec.output == "结论在此"
    assert rec.usage == {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}


def test_subrun_log_is_its_own_and_events_are_captured() -> None:
    rec = execute(_spec("干活"), MockLLM([AssistantOutput(text="好了")]))
    types = [e["type"] for e in rec.events]
    assert types[0] == "turn/start"
    assert "user/message" in types
    assert types[-1] == "turn/end"
    # 子运行的事件里没有父的任何痕迹(父会话 id 不出现在它的日志)
    assert all(e["data"].get("turn") is not None for e in rec.events if e["type"].startswith("step"))


def test_parent_log_stays_clean_and_keeps_only_the_lineage() -> None:
    """父日志里只有 call 与 result 两行 —— 子运行的脑内过程一条都不进来。"""
    store = SubRunStore(_tmpdir("clean"))
    sub_records: list = []

    def launch(args: dict) -> str:
        rec = execute(_spec(str(args.get("task", ""))), MockLLM([AssistantOutput(text="结论X")]),
                      store=store, run_id="sub-fixed-1")
        sub_records.append(rec)
        return f"run_id={rec.run_id} status={rec.status} output={rec.output}"

    tools = ToolRegistry([
        Tool(name="launch_subagent", description="派活", parameters={"type": "object"},
             func=launch, retain_result=True)
    ])
    parent_log = SessionLog("parent")
    llm = MockLLM([
        AssistantOutput(text="你等我看看", tool_calls=[
            ToolCall(id="c1", name="launch_subagent", arguments='{"task":"查一下"}')
        ]),
        AssistantOutput(text="查到了"),
    ])
    AgentLoop(parent_log, llm, tools, system_prompt="父 SYSTEM", max_steps=4).run_turn(
        "帮我查一下", source="user"
    )

    names = [e.type for e in parent_log.events]
    assert "tool/call" in names and "tool/result" in names
    # 父日志里没有子运行自己的事件类型计数(它就是普通工具痕迹)
    assert len(parent_log.of_type("assistant/message")) == 2
    # 血缘:run_id 出现在父的 tool/result 里,且能追到 store
    result_text = parent_log.of_type("tool/result")[0].data["content"][0]["text"]
    assert "sub-fixed-1" in result_text
    assert store.load("sub-fixed-1") is not None
    # 注入证据:父第 2 步真的看到了子运行的结论
    assert "结论X" in str(llm.seen_messages[-1])
    # 子运行的事件没有混进父日志
    assert all(e.data.get("turn") in (None, 1) for e in parent_log.events)
    assert parent_log.of_type("turn/start")[0].data["source"] == "user"


def test_store_roundtrip_keeps_record_and_trajectory() -> None:
    store = SubRunStore(_tmpdir("roundtrip"))
    rec = execute(_spec("写提纲", label="提纲", parent="session:1"),
                  MockLLM([AssistantOutput(text="提纲如下")]), store=store)
    loaded = store.load(rec.run_id)
    assert loaded is not None
    assert loaded.run_id == rec.run_id
    assert loaded.status == STATUS_COMPLETED
    assert loaded.output == "提纲如下"
    assert loaded.label == "提纲"
    assert loaded.parent == "session:1"
    assert [e["type"] for e in loaded.events] == [e["type"] for e in rec.events]
    assert rec.run_id in store.list_ids()


# ---------- RunQueue ----------


def test_queue_runs_every_job_and_respects_worker_cap() -> None:
    store = SubRunStore(_tmpdir("queue"))
    peak = {"now": 0, "max": 0}

    def runner(spec: SubRunSpec, run_id: str):
        peak["now"] += 1
        peak["max"] = max(peak["max"], peak["now"])
        try:
            return execute(spec, _SlowLLM(0.12), run_id=run_id, store=store)
        finally:
            peak["now"] -= 1

    queue = RunQueue(runner, workers=2)
    ids = [queue.submit(_spec(f"活{i}", label=f"活{i}")) for i in range(4)]
    jobs = queue.wait_all(timeout=10)
    try:
        assert all(j["status"] == STATUS_COMPLETED for j in jobs)
        assert peak["max"] <= 2, f"并发超过上限: {peak['max']}"
        assert queue.status()["completed"] == 4
        assert len(queue.list_jobs()) == 4
        for rid in ids:
            assert queue.record(rid) is not None
    finally:
        queue.stop()


def test_queue_returns_none_when_timing_out() -> None:
    queue = RunQueue(lambda spec, rid: execute(spec, _SlowLLM(0.5), run_id=rid), workers=1)
    rid = queue.submit(_spec("慢活"))
    try:
        assert queue.wait(rid, timeout=0.02) is None  # 没等到就是没等到,不抛
        assert queue.snapshot(rid)["status"] in (QUEUED, RUNNING)
    finally:
        queue.stop()
    assert queue.wait(rid, timeout=5) is not None or queue.snapshot(rid)["status"] == STATUS_KILLED


def test_queue_cancels_only_queued_jobs() -> None:
    queue = RunQueue(lambda spec, rid: execute(spec, _SlowLLM(0.3), run_id=rid), workers=1)
    try:
        first = queue.submit(_spec("在跑的"))
        assert _wait_until(lambda: queue.snapshot(first)["status"] == RUNNING), "第一件没跑起来"
        second = queue.submit(_spec("排队的"))
        assert queue.cancel(second) is True
        assert queue.snapshot(second)["status"] == STATUS_KILLED
        assert queue.snapshot(second)["detail"] == "cancelled"
        assert queue.record(second) is None  # 没跑过就是没跑过
        # 正在跑的不可取消 —— 诚实返回 False,不谎称已停
        assert queue.cancel(first) is False
        assert queue.wait(first, timeout=5) is not None
    finally:
        queue.stop()


def test_queue_survives_a_runner_that_raises() -> None:
    def runner(spec: SubRunSpec, run_id: str):
        if spec.task == "会炸":
            raise RuntimeError("runner boom")
        return execute(spec, MockLLM([AssistantOutput(text="没事")]), run_id=run_id)

    queue = RunQueue(runner, workers=1)
    bad = queue.submit(_spec("会炸"))
    good = queue.submit(_spec("正常的"))
    try:
        assert queue.wait(bad, timeout=5) is not None
        assert queue.snapshot(bad)["status"] == STATUS_FAILED
        assert "runner boom" in queue.snapshot(bad)["detail"]
        # worker 没被弄死:后一件照样跑完
        assert queue.wait(good, timeout=5) is not None
        assert queue.snapshot(good)["status"] == STATUS_COMPLETED
    finally:
        queue.stop()


def test_queue_stop_kills_pending_honestly() -> None:
    queue = RunQueue(lambda spec, rid: execute(spec, _SlowLLM(0.4), run_id=rid), workers=1)
    first = queue.submit(_spec("在跑的"))
    assert _wait_until(lambda: queue.snapshot(first)["status"] == RUNNING)
    pending = queue.submit(_spec("还在排队的"))
    queue.stop()
    assert queue.snapshot(pending)["status"] == STATUS_KILLED
    assert queue.snapshot(pending)["detail"] == "queue-stopped"
    assert queue.record(pending) is None


# ---------- 工具 ----------


def _tmpdir(name: str) -> Path:
    """测试用临时目录 —— 在系统 temp 下,不往仓库里写任何东西。

    (曾经写在 test/_tmp_subrun/,那会逼着 .gitignore 加一行 ——
     测试不该要求仓库为它改结构。)
    """
    path = Path(tempfile.gettempdir()) / "yona-subrun-tests" / name
    if path.exists():
        for old in path.glob("*.jsonl"):
            old.unlink()
    path.mkdir(parents=True, exist_ok=True)
    return path


if __name__ == "__main__":
    test_execute_completes_and_distills_output()
    test_execute_maps_hitting_the_step_cap_to_failed()
    test_subrun_tool_whitelist_is_isolating()
    test_execute_never_raises_on_llm_error()
    test_failed_run_still_reports_usage_from_chunk_layer()
    test_usage_is_not_double_counted_when_both_layers_carry_it()
    test_subrun_log_is_its_own_and_events_are_captured()
    test_parent_log_stays_clean_and_keeps_only_the_lineage()
    test_store_roundtrip_keeps_record_and_trajectory()
    test_queue_runs_every_job_and_respects_worker_cap()
    test_queue_returns_none_when_timing_out()
    test_queue_cancels_only_queued_jobs()
    test_queue_survives_a_runner_that_raises()
    test_queue_stop_kills_pending_honestly()
    print("subrun + queue all tests passed")
