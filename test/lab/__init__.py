"""Yona 实验台(lab)—— 还没收口的东西,不属于内核

这里放**正在验证、语义未定**的东西。规矩:
  - core/ 只收**已经想清楚、契约成立**的原语;实验一律先住这里;
  - lab/ 可以 import core/,core/ 永远不许 import lab/(单向,和 server 一样);
  - 实验跑出结论 → 拍板 → 才谈"毕业进 core";跑不出,就删掉,不留债。

现状(2026-09-16 更新):
  scheduler.py 子运行队列(编排)—— 并发上限/状态/取消 —— **仍未收口,暂不毕业**
               (产品 v1 是阻塞式派活,不需要队列;要异步了再谈)

已毕业(不再住这里):
  subrun.py → core/subrun.py          子运行执行器(用户 2026-09-16 批准)
  tools.py  → server/app/worker_tools.py  工人的只读工具集(同上)

  看效果: py test/subrun_probe.py [--real] / py test/worker_tools_probe.py
"""
