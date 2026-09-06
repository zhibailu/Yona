# docs/protocols —— 模块协议 / 算法

> 放"某个模块或算法怎么接、语义边界是什么"的权威文档。当前原型 =
> `LIFE_BACKFILL.md`(生活补写协议,§10 连续概率判定)。渐进迁到本目录。
>
> 相关模块:life 事件采样(server/rhythm.py)、心跳闸门(gate 方案②)、
> SessionLog 投影/时间游标(core/session_log.py)、SYSTEM 段装配(core/composer.py)。
>
> 规则:
> - 协议文档描述**机制**(采样/兜底/锚推进),不写死文案(文案在 personas)。
> - 目标代码文件头注释链回这里的对应协议。
