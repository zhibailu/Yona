"""Yona 新内核 · 会话存储(本地单用户,多会话 = 多张角色卡)

2026-09 重构(任务6+每卡 life):
- **一个会话 = 一个目录(档案袋)**:`sessions/<sid>/{chat.log, meta.json, images/}`
  —— 聊天事件日志 + 档案袋(标题/快照/self 生活流标记) + 它自己的图片。
  生活流不再是匿名全局文件:**每张卡的日记就住在它自己的 chat.log 里**
  (source=self 的事件;消息视图已按 source 过滤,聊天画面不受影响)。
- **Yona = 常驻保底旗舰卡**:meta 带 flagship;列表永远第一顺位;
  删了立即重建空 Yona(删 = 归档整袋 + 重置她)。
- **删除 = 先进归档**:`archive/<ts>-<sid>/` 整袋移入,手动清 archive 才真删。
- 兼容:启动时一次性把旧平铺布局(根下 *.log / *.meta.json / images/<sid>/)
  搬进目录制;旧 `_life.log` 按 2026-09 拍板直接丢弃。

UI 需要的"消息列表"从日志投影:user/message、assistant/message 事件 → 消息行,
事件 seq = 消息 id。surface 遮蔽(shadow/replace)自动生效。本层只做
"日志 ↔ UI 消息形状 / 卡片目录"的翻译,不含任何模型/循环逻辑。
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path

from core.session_log import SessionLog

_SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
FLAGSHIP_TITLE = "Yona"


def _now_iso() -> str:
    return _fmt_time(time.time())


def _fmt_time(ts: float) -> str:
    """给 UI 的时间:日期 + 时:分(不带秒)。

    UI 显示用 (created_at).slice(-5) 切出 "HH:MM";若带秒会切成 "MM:SS",
    造成 "22:37" 看起来像 22 点(实际是 11:22:37 的分秒)。
    """
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


class SessionStore:
    """data/sessions/ 下的"卡片目录注册表 + 落盘读写"。"""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.sessions_dir = self.data_dir / "sessions"
        self.archive_dir = self.data_dir / "archive"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_layout()

    # ---------- 目录规范(一个会话 = 一个目录) ----------

    def _sid_dir(self, session_id: str) -> Path:
        """会话目录(sessions/<sid>/)。"""
        return self.sessions_dir / session_id

    def _log_path(self, session_id: str) -> Path:
        return self._sid_dir(session_id) / "chat.log"

    def _meta_path(self, session_id: str) -> Path:
        return self._sid_dir(session_id) / "meta.json"

    def images_dir(self, session_id: str) -> Path:
        """会话图片目录(sessions/<sid>/images/),媒体层用它存取。"""
        return self._sid_dir(session_id) / "images"

    def _migrate_legacy_layout(self) -> None:
        """旧平铺布局 → 目录制(一次性;只搬得动就搬,搬不动忽略)。"""
        # 1) 旧的根级 *.log / *.meta.json(平铺):_life.log 按拍板丢弃
        for f in sorted(self.sessions_dir.glob("*.log")):
            stem = f.stem
            if stem.startswith("_"):
                try:
                    f.unlink()  # 旧 _life 生活流(2026-09 拍板:清掉重来)
                except OSError:
                    pass
                continue
            if _SESSION_ID_RE.match(stem):
                d = self._sid_dir(stem)
                d.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(f), str(d / "chat.log"))
                except OSError:
                    pass
        for f in sorted(self.sessions_dir.glob("*.meta.json")):
            stem = f.stem.removesuffix(".meta")
            if _SESSION_ID_RE.match(stem):
                d = self._sid_dir(stem)
                d.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(f), str(d / "meta.json"))
                except OSError:
                    pass
        # 2) 旧 images/<sid>/ → sessions/<sid>/images/
        img_root = self.data_dir / "images"
        if img_root.is_dir():
            for d in sorted(p for p in img_root.iterdir() if p.is_dir()):
                if _SESSION_ID_RE.match(d.name):
                    target = self.images_dir(d.name)
                    target.mkdir(parents=True, exist_ok=True)
                    for f in d.iterdir():
                        try:
                            shutil.move(str(f), str(target / f.name))
                        except OSError:
                            pass
            try:
                img_root.rmdir()  # 空了就摘掉(非空忽略,不拦)
            except OSError:
                pass

    # ---------- 会话生命周期 ----------

    def create_session(self, title: str | None = None, *, flagship: bool = False) -> str:
        sid = uuid.uuid4().hex
        now = _now_iso()
        if not title:
            title = f"会话 {time.strftime('%m-%d %H:%M', time.localtime())}"
        meta = {"id": sid, "title": title, "created_at": now, "updated_at": now}
        if flagship:
            meta["flagship"] = True  # Yona:常驻保底,列表第一顺位
        self._write_meta(sid, meta)
        return sid

    def list_sessions(self) -> list[dict]:
        """全部卡片目录;Yona(flagship)永远第一,其余按 updated_at 倒序。"""
        self.ensure_flagship()
        out = []
        for meta_file in sorted(self.sessions_dir.glob("*/meta.json")):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 -- 坏 meta 跳过不崩
                continue
            if not isinstance(meta, dict) or "id" not in meta:
                continue
            log = self._load_log(meta["id"])
            meta["message_count"] = len(log.derive_messages())
            out.append(meta)
        out.sort(key=lambda m: (not m.get("flagship", False),
                                _now_iso_sortable(m.get("updated_at", ""))),
                 reverse=False)
        # 上面按元组升序会让旗舰排最前(false<true? 不对) —— 换成显式稳定排序
        out.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
        out.sort(key=lambda m: 0 if m.get("flagship") else 1)
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

    # ---------- Yona 常驻旗舰 / 归档 / 自走目标 ----------

    def flagship_session_id(self) -> str | None:
        """当前 Yona 卡的 id(没有就建一个)。"""
        for meta_file in self.sessions_dir.glob("*/meta.json"):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if meta.get("flagship"):
                return meta["id"]
        return self.create_session(FLAGSHIP_TITLE, flagship=True)

    def ensure_flagship(self) -> str:
        return self.flagship_session_id()  # noqa: RET504 名字表意:没有就建

    def life_target_session_id(self) -> str:
        """心跳/补写/脉冲写给哪张卡 = 最近激活(最近有人聊过的卡),没有 = Yona。

        "最近激活" = updated_at 最新的、且日志里出现过真人 user/message 的卡
        (光点开没说话不算激活)。
        """
        best: str | None = None
        best_ts = ""
        for meta_file in self.sessions_dir.glob("*/meta.json"):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            sid = meta.get("id")
            if not isinstance(sid, str):
                continue
            ts = meta.get("updated_at", "")
            if ts < best_ts:
                continue
            if self._has_user_talk(sid):
                best, best_ts = sid, ts
        if best is None:
            return self.flagship_session_id()
        return best

    def _has_user_talk(self, session_id: str) -> bool:
        return any(
            e.type == "user/message" and e.data.get("source") == "user"
            for e in self._load_log(session_id).events
        )

    def delete_session(self, session_id: str) -> str | None:
        """删卡 = 归档整袋(archive/<ts>-<sid>/),再清当前位。

        Yona 被删 = 归档后立即重建空 Yona(重置她)。返回归档路径。
        """
        meta = self._read_meta(session_id)
        was_flagship = bool(meta and meta.get("flagship"))
        src = self._sid_dir(session_id)
        ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        dest = self.archive_dir / f"{ts}-{session_id}"
        if src.exists():
            shutil.move(str(src), str(dest))
        if was_flagship:
            self.flagship_session_id()  # 保底:重建空 Yona
        return str(dest)

    # ---------- 会话快照(档案袋 meta.json 的 settings 键) ----------

    def get_session_settings(self, session_id: str) -> dict:
        meta = self._read_meta(session_id)
        if not meta or not isinstance(meta.get("settings"), dict):
            return {}
        return dict(meta["settings"])

    def set_session_settings(self, session_id: str, settings: dict) -> bool:
        meta = self._read_meta(session_id)
        if meta is None:
            return False
        meta["settings"] = settings
        meta["updated_at"] = _now_iso()
        self._write_meta(session_id, meta)
        return True

    # ---------- 消息操作(转译成日志投影/遮蔽) ----------

    def _messages_view(self, session_id: str) -> list[dict]:
        """SessionLog → UI 聊天流消息行(自走/self 内容一律不进聊天流)。"""
        log = self._load_log(session_id)
        shadowed = log.shadowed_seqs()
        self_turns = {
            e.data["turn"] for e in log.events if e.type == "turn/start"
            and e.data.get("source") == "self"
        }
        anchored: list[tuple[int, int, int, str, str]] = []
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
                    continue
                if turn in self_turns:
                    continue
                text = _blocks_text(data.get("content"))
                if not text.strip():
                    continue
                order += 1
                anchored.append((anchor, order, e.seq, "user", text))
            elif e.type == "assistant/message":
                if turn in self_turns:
                    continue  # 卡片独处自语不进聊天流(内心面板看)
                text = _blocks_text(data.get("content"))
                if not text.strip():
                    continue
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
        """级联删除:从 from_id 起的可见消息 = fork tail-cut(遮蔽)。"""
        log = self._load_log(session_id)
        if from_id > log.last_seq():
            return 0
        before = len(self._messages_view(session_id))
        log.shadow(from_id, log.last_seq(), reason="user-delete-from")
        self._save_log(session_id, log)
        after = len(self._messages_view(session_id))
        return before - after

    def update_message_content(self, session_id: str, msg_id: int, content: str) -> bool:
        """编辑单条消息:遮蔽该条 + append 同 role 修正消息(replaces)。"""
        log = self._load_log(session_id)
        target = None
        for e in log.events:
            if e.seq == msg_id and e.type in ("user/message", "assistant/message"):
                target = e
                break
        if target is None:
            return False
        log.shadow(msg_id, msg_id, reason="user-edit")
        role = target.type
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
        return self._load_log(session_id)

    def save_log(self, session_id: str, log: SessionLog) -> None:
        self._save_log(session_id, log)

    def _load_log(self, session_id: str) -> SessionLog:
        p = self._log_path(session_id)
        if p.exists():
            lines = p.read_text(encoding="utf-8").splitlines()
            return SessionLog.from_lines(session_id, [l for l in lines if l.strip()])
        return SessionLog(session_id)

    def _save_log(self, session_id: str, log: SessionLog) -> None:
        p = self._log_path(session_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(log.to_lines()), encoding="utf-8")
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
        p = self._meta_path(session_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    def touch_session(self, session_id: str) -> None:
        meta = self._read_meta(session_id)
        if meta is not None:
            meta["updated_at"] = _now_iso()
            self._write_meta(session_id, meta)


def _now_iso_sortable(updated_at: str) -> str:
    """留作排序备用(实际排序见 list_sessions 的稳定双排序)。"""
    return updated_at


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
