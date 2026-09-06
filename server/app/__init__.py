"""Yona 应用层(server.app):FastAPI 应用的运行时与请求域。

布局(2026-09 用户拍板 B):
  engine.py   组合根 + 生活运行时(持有全局单例、start/stop、心跳/补写装配)
  gate.py     心跳闸门规则(与补写同一原语:概率形状继承 rhythm.DEFAULT_SHAPE)
  api/        请求域路由(chat 聊天 / view 观测 / media 图片)—— 同层收子目录

支撑库(server/rhythm.py 采样算法、server/store.py 持久化)留在 server 包根:
测试/探针大量直接引用,且它们是"服务私有库"而非请求面 —— 放子包反而要
留兼容 facade,得不偿失。分层规则见 STRUCTURE.md §1。
"""
