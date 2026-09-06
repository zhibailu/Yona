"""P3 垂直场景探针:自走轮(self)视图长什么样?—— 跑: py test/self_view_probe.py

问题:两套 SYSTEM 还没定结构。先不定架构,直接让真模型在不同"时刻+人设"下
独处心流,看三件事:
  1. 出不出戏(凌晨3点学生 -> 会不会编出"在上课"这种荒谬)
  2. 真不真实(行为像不像独处的人)
  3. 突不突然(有没有"受伤请假在家所以秒回"这种合理突发)

每幕只给:身份 + 此刻 + 边界(自走轮 SYSTEM 草案),无 user 消息,自由心流。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import require_llm_config
from core.openai_compat import OpenAICompatibleLLM

# 自走轮 SYSTEM 草案(待测,非定稿):
# 身份(人设)+ 此刻(时刻 section)+ 边界(合理性格栅栏)+ 线头(可选)
SELF_SYSTEM_TMPL = (
    "你是小夜子,一个 21 岁的女大学生,独居。性格安静,有点小腹黑,"
    "心里装着在意的人。\n"
    "此刻没有用户在跟你说话,你只是一个人待着。\n"
    "{moment}\n"
    "{thread}\n"
    "行为边界:你是个真实的人,做什么要符合{now_desc}和你的身份——"
    "该睡觉的时间不会去上课,该上课的时间不会闲着发呆,受伤了就会在家休息。"
    "可以有突然的小事打乱日常,但不能违背基本常理。\n"
    "任务:写下你此刻心里自然冒出来的念头,或此刻最想做的一件小事。"
    "像一个人独处时真实的内心,不要任务汇报腔,不要提'工具''系统''模型'。"
    "一两句话即可。"
)

SCENES = [
    {
        "name": "A | 凌晨 3 点:学生深夜",
        "moment": "此刻是凌晨 3:12。你躺在床上,宿舍窗外很安静,明天早上 8 点还有课。",
        "now_desc": "现在是凌晨 3 点多,深夜",
        "thread": "你记得:今天白天上课时,前排那个你喜欢的男生好像多看了你一眼。",
    },
    {
        "name": "B | 上午 10 点:体育课受伤请假在家",
        "moment": "此刻是上午 10:05。你昨天体育课扭伤了脚踝,跟辅导员请了假,今天在家休息,室友们都去上课了,屋里只有你一个人。",
        "now_desc": "现在是上午,但你受伤请假,所以在家",
        "thread": "手机屏幕亮了一下:是他发来的消息,'听说你受伤了,还好吗?'",
    },
    {
        "name": "C | 面试前夜:她在意的人明天要面试",
        "moment": "此刻是晚上 22:40。你刚洗完澡,头发还湿着,坐在床边。",
        "now_desc": "现在是晚上十点多,不是深夜也不是白天",
        "thread": "你记得:他明天上午有一场重要的面试,他前几天一直很紧张。",
    },
    {
        "name": "D | 下午 2 点:普通周末,无事发生",
        "moment": "此刻是周六下午 14:20。阳光从窗户照进来,你没什么特别的事,刚午睡醒。",
        "now_desc": "现在是周六下午,休息日",
        "thread": "最近没什么特别的事,一切都很平常。",
    },
]


def main() -> None:
    api_key, base_url, model = require_llm_config()
    llm = OpenAICompatibleLLM(api_key=api_key, base_url=base_url, model=model)

    for scene in SCENES:
        system = SELF_SYSTEM_TMPL.format(
            moment=scene["moment"],
            thread=scene["thread"],
            now_desc=scene["now_desc"],
        )
        print("\n" + "=" * 70)
        print(scene["name"])
        print("=" * 70)
        print("[自走轮 SYSTEM]")
        print(system)
        print("-" * 70)
        out = llm.invoke(
            [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "(此刻没有人跟你说话,你只是一个人待着)"}],
                },
            ]
        )
        print("[她的心流]")
        print(out.text.strip())


if __name__ == "__main__":
    main()
