# Yona 2.0(rewrite)

一个跑在本地、有「生活时间感」的 AI 陪伴角色内核重写。Yona 是一个住在你电脑里的 18 岁女大学生角色;新内核让她按自己的作息/生活节奏醒来、说话、离线时会自己补写这段时间的生活轨迹,而不是每次都被你拉起来才应答。

> 架构细节、参数拍板与历史决策见 `docs/README.md`(文档地图)。本 README 是给使用者的入口。

---

## 它是什么

- **自走生活**:心跳/脉冲让角色在生活窗口里自行醒来活动;离线时补写(backfill)按同一套事件算法把时间线填上 —— 有时间感,不冷场。
- **聊天 + 独处**:你找她聊天是「网友」关系的对话;她自己独处时按 `SELF_SITUATION` 过自己的生活。
- **Web UI**:零构建的浏览器界面(会话 / 聊天流式 / 观测内心活动 / 预设与模型连接配置)。

## 快速开始

```bash
# 1. 配置(密钥只进 .env,不入版本库)
copy .env.example .env      # 然后填入 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL

# 2. 跑起来(FastAPI;需在项目根目录)
py -m uvicorn server.main:app --port 8000
```

- 启动后在浏览器打开 `http://127.0.0.1:8000`。
- 首次需在 UI 的连接向导里填 LLM 端点并实测拉通模型列表(配置落 `data/llm.local.json`,运行时可热重配)。
- 未配置可用 LLM 时,聊天/自走被禁用,但静态 UI 与结构仍可浏览。

> 依赖:`pip install -r requirements.txt`(fastapi / uvicorn / pydantic / python-dotenv / requests)。

## 目录一览

```
core/       引擎内核(纯逻辑,不 import server/character)
server/     产品服务层(FastAPI thin router;params.py = 参数唯一来源)
character/  角色层(人设 persona / 状态 state / 工具)
static/     Web UI(零构建)
test/       测试 + 探针脚本
data/       运行数据(会话日志 / 生活 / 图片;gitignore,不入库)
config.py   .env 加载
docs/       文档地图与分层说明
```

## 给贡献者的提醒

- 参数 / 语义拍板以 `docs/` 与 `server/params.py` 为唯一真值;别把实验台的展示现象悄悄推进到产品执行路径。
- 修改产品执行路径(心跳/脉冲/聊天)前先看 `docs/` 的守则,并跑对应测试。

## 许可

本项目由 zhibailu 维护;源码公开供个人学习与使用。
