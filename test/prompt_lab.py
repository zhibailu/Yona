"""提示词实验台(转发壳)—— 正式入口已迁到项目根目录 `prompt_lab.py`。

2026-09:战略调试工具放根目录(用户拍板,见 MAP)。本文件只转发,避免旧
肌肉记忆 `py test/prompt_lab.py` 断掉。新入口:

    py prompt_lab.py            交互式(聊天/自走/补写/预览)
    py prompt_lab.py --preview  只打印当前输入预览(零花费)
"""

import runpy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

if __name__ == "__main__":
    runpy.run_path(str(_ROOT / "prompt_lab.py"), run_name="__main__")
