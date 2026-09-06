"""Yona 新内核 · 应用服务层(thin router)

只做协议适配:把 UI 的 HTTP 请求翻译成内核调用,不做业务逻辑。
业务 = 内核(core/) + 角色(character/);本层越薄越好。
"""
