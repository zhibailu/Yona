"""Yona 新内核 · 会话存储(本地单用户,多会话)

每个 UI 会话 = 一个 SessionLog(事件源),落盘 data/sessions/<id>.log。
UI 需要的"消息列表"从日志投影:user/message、assistant/message 事件 → 消息行,
事件 seq = 消息 id(UI 契约,原 Yona 就是整数自增 id)。
surface 遮蔽(shadow/replace)自动生效:被遮消息不进视图 = UI 删除/撤回。

本层只做"日志 ↔ UI 消息形状"的翻译,不含任何模型/循环逻辑。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from core.session_log import SessionLog

_SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _now_iso() -> str:
    return _fmt_time(time.time())


def _fmt_time(ts: float) -> str:
    """给 UI 的时间:日期 + 时:分(不带秒)。

    UI 显示用 (created_at).slice(-5) 切出 "HH:MM";若带秒会切成 "MM:SS",
    造成 "22:37" 看起来像 22 点(实际是 11:22:37 的分秒)。
    """
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


class SessionStore:
    """data/sessions/ 目录的会话注册表 + 落盘读写。"""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.sessions_dir = self.data_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 会话生命周期 ----------

    def create_session(self, title: str | None = None) -> str:
        sid = uuid.uuid4().hex
        now = _now_iso()
        if not title:
            title = f"会话 {time.strftime('%m-%d %H:%M', time.localtime())}"
        meta = {"id": sid, "title": title, "created_at": now, "updated_at": now}
        self._write_meta(sid, meta)
        return sid

    def list_sessions(self) -> list[dict]:
        out = []
        for meta_file in sorted(self.sessions_dir.glob("*.meta.json")):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 -- 坏 meta 跳过不崩
                continue
            log = self._load_log(meta["id"])
            # message_count 从投影算(遮蔽后的可见条数)
            meta["message_count"] = len(log.derive_messages())
            out.append(meta)
        # 按 updated_at 倒序
        out.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
        return out

    def get_session(self, session_id: str) -> dict | None:
        meta = self._read_meta(session_id)
        if meta is None:
            return None
        return {**meta, "messages": self._messages_view(session_id)}

    def rename_session(self, session_id: str, title: str) -> bool:
        meta = self._read_meta(session_id)
        if meta is None:
            return False
        meta["title"] = title
        meta["updated_at"] = _now_iso()
        self._write_meta(session_id, meta)
        return True

    def delete_session(self, session_id: str) -> bool:
        for f in self.sessions_dir.glob(f"{session_id}.*"):
            f.unlink(missing_ok=True)
        return True

    # ---------- 消息操作(转译成日志投影/遮蔽) ----------

    def _messages_view(self, session_id: str) -> list[dict]:
        """SessionLog → UI 聊天流消息行。

        视图规则(与内核 derive_messages 同哲学,UI 侧再收一层):
        - shadowed seq 一律跳过(删除/撤回已生效)
        - replace 摘要消息按 anchor(=replaces.start)顶在遮蔽段位置
        - 自走轮(source=self)的内容不进聊天流:占位 user 串是系统协议说明,
          自语走内心活动面板(agent-feed),聊天流只显示"你和她"的对话
        - assistant 消息只取文本块(工具痕迹 UI 不显示为消息行)
        事件 seq = 消息 id(UI 契约)。
        """
        log = self._load_log(session_id)
        shadowed = log.shadowed_seqs()
        # 哪些轮是自走轮:turn/start 的 source=self
        self_turns = {
            e.data["turn"] for e in log.events if e.type == "turn/start"
            and e.data.get("source") == "self"
        }
        anchored: list[tuple[int, int, int, str, str]] = []  # anchor, order, seq, role, text
        order = 0
        for e in log.events:
            if e.seq in shadowed:
                continue
            data = e.data
            anchor = e.seq
            replaces = data.get("replaces") if e.type == "user/message" else None
            if isinstance(replaces, dict) and "start" in replaces:
                anchor = replaces["start"]
            turn = data.get("turn")
            if e.type == "user/message":
                if data.get("source") == "self":
                    continue  # 自走占位串不进聊天流
                if turn in self_turns:
                    continue  # 兜底:自走轮的 user 槽不显示
                text = _blocks_text(data.get("content"))
                if not text.strip():
                    continue
                order += 1
                anchored.append((anchor, order, e.seq, "user", text))
            elif e.type == "assistant/message":
                if turn in self_turns:
                    continue  # 自语走内心活动面板,不进聊天流
                text = _blocks_text(data.get("content"))
                if not text.strip():
                    continue  # 纯工具调用/空消息不成行
                order += 1
                anchored.append((anchor, order, e.seq, "assistant", text))
        anchored.sort(key=lambda item: (item[0], item[1]))
        return [
            {
                "id": seq,
                "role": role,
                "content": text,
                "created_at": self._created_at_of(session_id, seq),
                "session_id": session_id,
            }
            for _, _, seq, role, text in anchored
        ]

    def _created_at_of(self, session_id: str, seq: int) -> str:
        for e in self._load_log(session_id).events:
            if e.seq == seq:
                return _fmt_time(e.time)
        return _now_iso()

    def get_messages(self, session_id: str, id_from: int | None = None) -> list[dict]:
        msgs = self._messages_view(session_id)
        if id_from is not None:
            msgs = [m for m in msgs if m["id"] >= id_from]
        return msgs

    def get_message(self, session_id: str, msg_id: int) -> dict | None:
        for m in self._messages_view(session_id):
            if m["id"] == msg_id:
                return m
        return None

    def delete_messages_from(self, session_id: str, from_id: int) -> int:
        """级联删除:从 from_id 起的所有可见消息 = fork tail-cut。

        UI 语义"删这条及之后"→ 内核 shadow(遮蔽)从该消息到日志末尾。
        日志原文保留(可审计);投影后 UI 刷新自然看不到。
        返回实际遮蔽了多少条可见消息(UI 可显示)。
        """
        log = self._load_log(session_id)
        # 找到 from_id 对应的事件 seq:from_id 就是事件 seq(user/assistant/message)
        # 但 UI 的 msg id 只覆盖可见消息;from_id 直接当日志 seq 定位。
        if from_id > log.last_seq():
            return 0
        # 遮蔽起点取 from_id;终点 = 当前日志末尾(整段作废)
        before = len(self._messages_view(session_id))
        log.shadow(from_id, log.last_seq(), reason="user-delete-from")
        self._save_log(session_id, log)
        after = len(self._messages_view(session_id))
        return before - after

    def update_message_content(self, session_id: str, msg_id: int, content: str) -> bool:
        """编辑单条消息内容 —— 事件源下=遮蔽该消息 + 追加修正消息(replace)。

        旧 Yona 直接 UPDATE 数据库;这里日志不可变:把 msg_id 那一条遮蔽,
        再以一条新的 user/assistant 消息顶替(replaces 声明),投影时顶在原位。
        简单实现:遮蔽该条 + append 一条同 role 的修正消息(replaces=该 seq)。
        """
        log = self._load_log(session_id)
        target = None
        for e in log.events:
            if e.seq == msg_id and e.type in ("user/message", "assistant/message"):
                target = e
                break
        if target is None:
            return False
        log.shadow(msg_id, msg_id, reason="user-edit")
        role = target.type  # user/message | assistant/message
        log.append(
            role,
            content=[{"type": "text", "text": content}],
            source="user-edit",
            replaces={"start": msg_id, "end": msg_id},
        )
        self._save_log(session_id, log)
        return True

    # ---------- 日志落盘 ----------

    def load_log(self, session_id: str) -> SessionLog:
        """读(或新建)一个会话的日志。"""
        return self._load_log(session_id)

    def save_log(self, session_id: str, log: SessionLog) -> None:
        """写日志并刷新会话 updated_at。"""
        self._save_log(session_id, log)

    def _log_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.log"

    def _meta_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.meta.json"

    def _load_log(self, session_id: str) -> SessionLog:
        p = self._log_path(session_id)
        if p.exists():
            lines = p.read_text(encoding="utf-8").splitlines()
            return SessionLog.from_lines(session_id, [l for l in lines if l.strip()])
        return SessionLog(session_id)

    def _save_log(self, session_id: str, log: SessionLog) -> None:
        self._log_path(session_id).write_text(
            "\n".join(log.to_lines()), encoding="utf-8"
        )
        meta = self._read_meta(session_id)
        if meta is not None:
            meta["updated_at"] = _now_iso()
            self._write_meta(session_id, meta)

    def _read_meta(self, session_id: str) -> dict | None:
        p = self._meta_path(session_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def _write_meta(self, session_id: str, meta: dict) -> None:
        self._meta_path(session_id).write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )

    def touch_session(self, session_id: str) -> None:
        """聊完一轮后刷新 updated_at(会话列表排序)。"""
        meta = self._read_meta(session_id)
        if meta is not None:
            meta["updated_at"] = _now_iso()
            self._write_meta(session_id, meta)


def _blocks_text(content) -> str:
    """消息块列表 → 纯文本(UI 契约是字符串 content)。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(parts)
    return ""
