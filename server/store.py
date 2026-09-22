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

    ⚠️ 同一份契约还有第二份逐字实现:`server/app/api/view.py:_fmt_time`。
    两份都没对外导出(下划线私有),跨模块 import 私有名在本仓没有先例,
    所以没做复用 —— **改这里必须改那边**(改法是并成一处,见该处注释)。
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

    def subruns_dir(self, session_id: str) -> Path:
        """工人(子运行)轨迹目录(sessions/<sid>/subruns/),一跑一个 jsonl。

        2026-09-22 用户拍板:**日志隔离,同族的就放一起** ——
        「不要同目录等级下有不同会话的主日志又有各自的 subagent,管理和回看会很乱」。

        → 一张卡的全部日志都在**它自己的目录**里,同一个层级:
            sessions/<sid>/chat.log          主日志(她和你)
            sessions/<sid>/subruns/<run>.jsonl  工人轨迹(她派出去的那次活)
            sessions/<sid>/meta.json         这张卡的快照(人设/预设)
            sessions/<sid>/images/           图片

        ⚠️ 三个连带结论,别漏:
          ① **不要**另开 `data/subruns/` 那种全局平铺目录 —— 那正是用户说的"乱":
             所有卡的工人日志挤在一层,只能靠 run_id 猜是哪张卡的。
          ② **清理策略不需要另立**:工人日志跟卡同生共死 —— `delete_session()`
             把 `sessions/<sid>/` 整袋搬进 `archive/<ts>-<sid>/`,它们自然跟着走。
             (这是它跟 `cache/` 里那些"派生可重建"产物不同的地方:轨迹是**证据**,
             不可重建,所以既不进 `cache/` 也不该被随手清。)
          ③ 目录**按需创建**:`SubRunStore.save()` 自己 `mkdir(parents=True)`,
             这里只算路径 —— 跟 `images_dir()` 同款(读路径不产生写副作用)。
        """
        return self._sid_dir(session_id) / "subruns"

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
        # 保底:没有旗舰就地建一张空 Yona(原来包在 ensure_flagship 里,那层
        # 只是 return self.flagship_session_id() 的转发,已删)
        self.flagship_session_id()
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
        # 顺序 = 旗舰第一,其余 updated_at 降序。**就这两段稳定排序,别再加第三段**
        # (原来这三段里的第一段按 (not flagship, _now_iso_sortable(updated_at)) 升序,
        #  结果被下面两段完全覆盖 —— 是死排序;它调的 _now_iso_sortable 是返回入参
        #  原值的恒等函数,已一并删)
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
        """这张卡上**还看得见**的真人消息,一条都没有吗?

        2026-09-22 10:45 用户拍板(口径原话):
        「如果是说当前会话 0 消息了的话,**应该是整个对应日志归档,会被补写等过滤掉,
          当然不继续补东西,就是死掉了**。」

        → 判据 = **可见消息**(排 shadow),**不是原始事件**。
        原来扫的是原始事件 → 你把某张卡的消息全删了,它仍被判"有真人说过话",
        于是继续被选为自走目标(`life_target()`)与补写目标(`life_backfill_order()`),
        她继续往一张你已经清空的卡里写生活。
        而其它三处投影**全都跳 shadow**(`core/session_log.py` 的 `derive_messages`、
        `store._messages_view`、`server/app/api/view.py` 的两个投影函数)——
        同一件事四个地方三个口径,这里收齐。

        ⚠️ 两种"清空"不是一回事,别混:
          · `delete_messages_from()` = **tail-cut shadow** —— 日志原文一字不动,只追加
            一条 `surface/shadow` 注解。全删光 → 所有 seq 被盖住 → 这里返 False
            → 这张卡**死掉**(不再被补写/自走选中),但**日志还在盘上**。
          · `delete_session()` = **整袋归档**(`archive/<ts>-<sid>/`)—— 连日志一起搬走。
        用户要的终局是归档那份;这里只管**判据**:"看不见真人说过话的卡 = 死"。"""
        log = self._load_log(session_id)
        shadowed = log.shadowed_seqs()
        return any(
            e.type == "user/message"
            and e.data.get("source") == "user"
            and e.seq not in shadowed
            for e in log.events
        )

    def life_backfill_order(self) -> list[str]:
        """离线补写的**处理顺序**:离线间隔升序 = `updated_at` 降序。

        2026-09-17 拍板(取方案"乙"):补写启动时**遍历所有有历史的卡**,
        按这个顺序一张一张来 —— 所以**当前卡天然排第一**(它最近有人聊过,
        离线间隔最短),补完就能立刻正常对话,其余在后台接着补。

        只收 `_has_user_talk` 的卡(光点开没说话不算;空卡没有"离线"可言)。
        """
        out: list[tuple[str, str]] = []
        for meta_file in self.sessions_dir.glob("*/meta.json"):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            sid = meta.get("id")
            if not isinstance(sid, str) or not self._has_user_talk(sid):
                continue
            out.append((meta.get("updated_at", ""), sid))
        # updated_at 是 ISO 串:大的 = 更近 = 离线间隔更短 → 排前面
        out.sort(reverse=True)
        return [sid for _ts, sid in out]

    def archive_log(self, session_id: str) -> str | None:
        """把这张卡的**主日志**搬进 `archive/`(它上面已经一条可见消息都没有了)。

        2026-09-22 11:20 用户拍板(口径原话):
        「如果是说当前会话 0 消息了的话,**应该是整个对应日志归档**,会被补写等过滤掉,
          当然不继续补东西,就是死掉了。」用户随后确认:"2 要"。

        ⚠️ **只搬日志,不搬卡**(与 `delete_session()` 的"整袋归档"区分开,别混):
        用户说的是「当前会话 **0 消息**了」—— 这句话本身就假设**会话还在**,
        只是里面的消息没了。所以这里做的是:把 `sessions/<sid>/chat.log` 移走,
        **留下 meta.json / images / subruns** —— 卡还在列表上(是个空卡),
        但那段对话**真的离开了活路径**(不是只被打上 shadow 注解)。

        为什么不做成"整袋归档"(把整个卡也搬走)—— 两条都是实测出来的坑:
          ① UI 上"重新生成 / 重试"走的是**同一个** `DELETE /messages/from/{id}`
             (static/app-messages.js 的 `regenerateMessage` / `retryMessage`:
             先删掉那条用户消息再重发)。如果删到第一条就把**整卡**搬走,
             紧接着的重发会写进一个**没有 meta.json 的目录** ——
             而 `list_sessions()` 是按 `*/meta.json` 枚举的,结果是
             会话**从侧边栏消失、聊天还在往里写**。这属于静默的数据错位。
          ② 用户的原话是"日志归档",不是"卡归档";卡归档本来就有别的入口
             (`DELETE /sessions/{id}` = `delete_session()`)。

        与 `_has_user_talk()` 的分工(两条一起才凑成"死掉"):
          · `_has_user_talk()` 判据 → 卡退出自走目标 / 补写名单(**不继续补东西**);
          · 本函数 → 内容离开活路径(**归档**);
          · 所以"死"是这两条合起来的结果,不是任何单独一条。

        幂等 + **自带护栏**:没有 `chat.log`(已被归档过)时返回 None、不抛异常 ——
        调用方(`server/main.py` 的删除端点)可能因为并发/重复请求再调一次。
        ⚠️ **还有可见消息时也返回 None(什么都不做)** —— 这个方法的名字就是
        "日志空了才归档",把一张**还有对话的卡**的日志搬走是数据事故,不该靠调用方
        自觉。护栏放在这里,是因为这里是唯一能一眼看出"该不该搬"的地方
        (`_messages_view()` 就是判据本身)。真要在还有消息时归档,
        那是别的语义了,请显式用 `delete_session()`(整袋归档)或另写方法。
        """
        if self._messages_view(session_id):
            return None  # 还有可见消息 → 不搬(见上面那条护栏)
        p = self._log_path(session_id)
        if not p.exists():
            return None
        ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        dest = self.archive_dir / f"{ts}-{session_id}"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(dest / "chat.log"))
        return str(dest)

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
        # 中间元组的末位带上 e.time(**不是**再回查日志):
        # 原来 created_at 靠 self._created_at_of(session_id, seq),那条每调一次
        # 就 self._load_log(session_id) 一次 —— 整份 chat.log 重读 + 重解析。
        # 一条 200 消息的卡 = 201 次全量读盘(O(N²));而时间就在这条事件身上。
        # 见 docs/pitfalls/HISTORY.md(load_log 无缓存)。_created_at_of 已删。
        anchored: list[tuple[int, int, int, str, str, float]] = []
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
                anchored.append((anchor, order, e.seq, "user", text, e.time))
            elif e.type == "assistant/message":
                if turn in self_turns:
                    continue  # 卡片独处的生活事件不进聊天流(内心面板看)
                text = _blocks_text(data.get("content"))
                if not text.strip():
                    continue
                order += 1
                anchored.append((anchor, order, e.seq, "assistant", text, e.time))
        anchored.sort(key=lambda item: (item[0], item[1]))
        return [
            {
                "id": seq,
                "role": role,
                "content": text,
                "created_at": _fmt_time(ts),
                "session_id": session_id,
            }
            for _, _, seq, role, text, ts in anchored
        ]

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
        """把这张卡的 `updated_at` 推进到当前时刻。

        ⏸ **占位:产品零调用(2026-09-22 清理时如实标注,不拆不删)。**

        ① 现状:`grep` 全仓 —— 调用点**一个都没有**。唯一曾经调它的地方是
           `server/app/api/chat.py`,而那里现在**自己写 meta**(理由写在那儿:
           `touch_session` 做的是"读一遍 meta 再整体写回",与它前后那笔写盘
           **重复读+重复写**),所以那处已删。
        ② 为什么留着:它是 `updated_at` 的**唯一语义出口** —— `updated_at` 是
           产品行为(自走目标 `life_target()` 与补写顺序 `life_backfill_order()`
           都按它排序,分钟精度)。将来任何"会话被碰过就该刷新时间"的新入口,
           都该用它,而不是各自再写一遍 `_read_meta` + `_write_meta`
           (那正是它当初长出来的原因)。
        ③ 什么时候动:如果再没有任何调用点,而 `updated_at` 的写法已在别处
           稳定成一条(`chat.py` 与 `set_session_settings` 都在自己写),
           就可以删 —— 但**删之前先把 `updated_at` 的写入收口到一处**,
           否则会把"两个地方各写一半"变成"三个地方"。
        ④ 将来手术要删哪几行:本注释块 + 下面 `touch_session` 整个函数
           (签名 + 4 行函数体);连带看 `test/test_recall_wiring.py` 里那条
           注释("直接改 meta 而不是 `touch_session()`" —— 它解释了为什么
           探针不调它,删函数时把这句话也一起改掉)。
        """
        meta = self._read_meta(session_id)
        if meta is not None:
            meta["updated_at"] = _now_iso()
            self._write_meta(session_id, meta)


def _blocks_text(content) -> str:
    """消息块列表 → 纯文本(UI 契约是字符串 content)。

    ⚠️ 同一语义还有第二份**逐字相同**的实现:`server/app/api/view.py:_text_of`
    (另外 core/memory.py、core/openai_compat.py、turn_lab.py、
    prompt_lab/tool_recall.py 各有细节不同的变体)。上面 _fmt_time 的理由在这里
    同样成立 —— **改一处要改另一处**。
    """
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
