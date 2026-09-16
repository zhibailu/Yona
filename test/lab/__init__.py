"""Yona 实验台(lab)—— 还没收口的东西,不属于内核

这里放**正在验证、语义未定**的东西。规矩:
  - core/ 只收**已经想清楚、契约成立**的原语;实验一律先住这里;
  - lab/ 可以 import core/,core/ 永远不许 import lab/(单向,和 server 一样);
  - 实验跑出结论 → 拍板 → 才谈"毕业进 core";跑不出,就删掉,不留债。

现状:
  subrun.py    子运行(会死的隔离工作单元)—— 失败契约未定,真机已暴露问题
  scheduler.py 子运行队列(编排)—— 并发上限/状态/取消
  看效果: py test/subrun_probe.py [--real]
"""
