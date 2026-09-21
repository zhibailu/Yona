# docs/protocols —— 模块协议 / 算法

> 放"某个模块或算法怎么接、语义边界是什么"的权威文档。当前原型 =
> `LIFE_BACKFILL.md`(生活补写协议;~~§10 连续概率判定~~ **协议权威 = `docs/decisions/DESIGN.md §10`**)。渐进迁到本目录。
>
> > 【2026-09-21 23:10 更正】`§10` 不在 `LIFE_BACKFILL.md` 里(该文件只有 §0–§9)——
> > 真相:`§10` 是 `docs/decisions/DESIGN.md` 的编号(那一节标题是"离线生活补写",
> > 里面才是"连续概率判定")。
>
> 相关模块:life 事件采样(server/rhythm.py)、心跳闸门(~~gate 方案②~~ `server/app/gate.py` `ServerGate`;"gate 方案②"出处见 `docs/decisions/TIMELINE.md` 的「gate 方案② ✅(2026-09 方向拍板)」那条)、
> SessionLog 投影/时间游标(core/session_log.py)、SYSTEM 段装配(core/composer.py)。
>
> > 【2026-09-21 23:10 更正】"gate 方案②"当时没有出处锚点 —— 真相:实现侧是
> > `server/app/gate.py` 的 `ServerGate`,参数在 `server/params.py` 的 `SELF_WAKES_PER_DAY` / `HEARTBEAT_COOLDOWN_SEC` / `HEARTBEAT_INTERVAL_SEC`;
> > 全仓库 grep `方案②` 只命中 `docs/decisions/TIMELINE.md` 那一条(以**关键字** `方案②` 搜得到)。
>
> 规则:
> - 协议文档描述**机制**(采样/兜底/锚推进),不写死文案(文案在 personas)。
> - 目标代码文件头注释链回这里的对应协议。
> - 状态行与"更正 / 作废"标注必须带 `YYYY-MM-DD HH:MM`(精确到分钟)—— 只有日期时,同一天多次拍板无法排序(见 `docs/decisions/TIMELINE.md` 2026-09-21 的两条)。
> - 协议文档描述**机制**;引擎内联拼出来的**动态当轮数据**不在此列,但引用时必须给出代码行号。
