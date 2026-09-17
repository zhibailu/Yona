"""turn 队列自测(2026-09-17:四个来源不再各自抢锁,改为排队)

**为什么是队列而不是锁**:锁能排,**不能插队** —— 它是先到先得。而拍板规则是
"已经解绑的会话来了 user 请求,其余的都靠后被插队",这需要**优先级**。

钉住三条:
1. **单 worker 串行** —— 任何时刻只有一项在跑(永不并发);
2. **user 优先级** —— user 项排到所有 self 项前面;
3. **同优先级 FIFO** —— 按入队顺序。

以及两个结构常量:队列项粒度、`turn_is_busy()` 的语义。

跑: py test\\test_turn_queue.py
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from server.app import engine as eng  # noqa: E402


def test_priority_puts_user_ahead_of_self():
    """user 的优先级数值必须小于 self(PriorityQueue 小的先出)。"""
    assert eng._QUEUE_USER < eng._QUEUE_SELF, (
        f"user={eng._QUEUE_USER} self={eng._QUEUE_SELF} —— user 排不到前面了"
    )


def test_turns_never_run_concurrently():
    """单 worker:任何时刻只有一个 job 在跑。"""
    guard = threading.Lock()
    live: list[str] = []
    order: list[str] = []

    def mk(tag):
        def job():
            with guard:
                live.append(tag)
                assert len(live) == 1, f"并发跑了! live={live}"
            time.sleep(0.05)
            with guard:
                live.remove(tag)
                order.append(tag)
        return job

    ts = [
        threading.Thread(target=lambda t=t: eng._submit_turn(mk(t)))
        for t in "ABCD"
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5.0)
    assert sorted(order) == list("ABCD"), order


def test_user_jumps_the_queue():
    """USER 项插到所有已排队的 SELF 项前面(只让开正跑着的那一项)。"""
    ran: list[str] = []
    release = threading.Event()

    def blocker():
        release.wait(3.0)
        ran.append("blocker")

    tb = threading.Thread(
        target=lambda: eng._submit_turn(blocker, priority=eng._QUEUE_SELF)
    )
    tb.start()
    time.sleep(0.25)          # 让 blocker 先跑起来占住 worker

    # 三个 SELF 先入队,USER 最后入队 —— 但 USER 必须先跑
    ts = [
        threading.Thread(
            target=lambda t=tag: eng._submit_turn(
                lambda: ran.append(t), priority=eng._QUEUE_SELF
            )
        )
        for tag in ("s1", "s2", "s3")
    ]
    for t in ts:
        t.start()
    time.sleep(0.25)
    tu = threading.Thread(
        target=lambda: eng._submit_turn(
            lambda: ran.append("USER"), priority=eng._QUEUE_USER
        )
    )
    tu.start()
    time.sleep(0.25)

    release.set()
    for t in ts + [tu, tb]:
        t.join(5.0)

    assert ran[0] == "blocker", ran
    assert ran[1] == "USER", f"user 没插队: {ran}"
    assert set(ran[2:]) == {"s1", "s2", "s3"}, ran


def test_same_priority_is_fifo_by_enqueue_order():
    """同优先级按**入队顺序**跑(用 tick 保证入队次序确定)。"""
    ran: list[int] = []
    for i in range(6):
        eng._submit_turn(lambda i=i: ran.append(i), priority=eng._QUEUE_SELF)
    assert ran == [0, 1, 2, 3, 4, 5], ran


def test_job_exception_is_reraised_to_the_submitter():
    """job 抛异常 → 原位重抛给提交方,且不拖垮 worker(下一项照跑)。"""
    boom = RuntimeError("炸了")
    try:
        eng._submit_turn(lambda: (_ for _ in ()).throw(boom))
        raise AssertionError("异常没被重抛")
    except RuntimeError as exc:
        assert exc is boom, exc
    # worker 还活着
    assert eng._submit_turn(lambda: "ok") == "ok"


def test_turn_is_busy_reflects_pending_work():
    """队列上有东西(或 worker 正忙)→ turn_is_busy() 为真。"""
    release = threading.Event()
    tb = threading.Thread(
        target=lambda: eng._submit_turn(release.wait, priority=eng._QUEUE_SELF)
    )
    tb.start()
    try:
        deadline = time.time() + 2.0
        while not eng.turn_is_busy() and time.time() < deadline:
            time.sleep(0.02)
        assert eng.turn_is_busy(), "worker 在跑却没报忙"
    finally:
        release.set()
        tb.join(5.0)


def test_idle_queue_reports_not_busy():
    """空队列 + worker 闲着 → 不报忙。"""
    # 先保证前面塞的都跑完了
    eng._submit_turn(lambda: None)
    time.sleep(0.05)
    assert not eng.turn_is_busy(), "队列空了却还在报忙"


if __name__ == "__main__":
    test_priority_puts_user_ahead_of_self()
    test_turns_never_run_concurrently()
    test_user_jumps_the_queue()
    test_same_priority_is_fifo_by_enqueue_order()
    test_job_exception_is_reraised_to_the_submitter()
    test_turn_is_busy_reflects_pending_work()
    test_idle_queue_reports_not_busy()
    print("turn queue(单 worker 串行 · user 插队) all tests passed")
