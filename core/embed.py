"""Yona 内核 · 嵌入器(embed)

`MemoryIndex` 认的那份协议(`.passage(text)` / `.query(text)`)的**产品实现**。

## 它是可选依赖(用户 2026-09-21 拍板"甲")

产品**不强制**装 torch/transformers:拿不到就退化成"只有关键词那一路",
而不是装不上、也不是崩。`requirements.txt` 里只留一行说明,不进 `install_requires`。

直接依赖只有两个包,其余全是它们的传递依赖:

    transformers 4.50.0 → filelock, huggingface-hub, numpy, packaging, pyyaml,
                          regex, requests, tokenizers, safetensors, tqdm
    torch 2.13.0+cu130  → filelock, typing-extensions, setuptools, sympy,
                          networkx, jinja2, fsspec

**不需要 sentence-transformers** —— BGE 的官方用法就那几步,这里直接复刻
(与 test/recall_probe.py 的 `BgeEmbedder` 是同一套,那份是实验区的复制品)。

## 懒加载 = 三件事

1. `import torch` / `import transformers` **写在 `_ensure_loaded` 里面**,
   不进文件头 —— 不碰记忆检索的进程永远不加载它们;
2. 权重第一次真要用时才 `from_pretrained`,之后复用同一个编码闭包;
3. **`local_files_only=True`** —— 实测逼出来的:允许联网时,HF 连不上要
   **重试 176.9 秒**才回落到本地缓存;离线加载实测 **4.18 秒**。

## 暖机要**早**,不要"等到要用"(用户 2026-09-21 纠正)

实测这台机器上的暖机账(冷进程 → 能编码):

    import torch                 1.16 s
    + import transformers        1.39 s
    + tokenizer                  3.49 s   ← 2.1 秒的大头(读大词表)
    + 模型权重(CPU fp32)        4.18 s   权重 1242 MB,进程峰值 1894 MB
    之后每条编码                 43 ms

**关键:懒加载省不到这 1.9 GB。** 只要这张卡聊过一轮、后台补过一条向量,
模型就常驻了 —— 早晚都要付。所以"早暖 vs 晚暖"差的**只是这 4.2 秒花在什么时候**:

    晚暖 → 有可能落在"她刚问完、正在等回答"那一刻(她问"我上次是不是说过…"
            而索引还是空的 → 查询自己就得先把模型读进来)
    早暖 → 落在"引擎刚起来、UI 还在连"那段空白,她最不敏感

所以后台线程**第一件事**就是暖机(见 `core/memory_cache.py` 的 `warm()`)。
真正该省的"懒"是另一件事:**暖机挂在 server 启动(lifespan)上,不挂在模块导入上** ——
否则 turn_lab 和每次跑测试都会白付 4.2 秒 + 1.9 GB。

## 不需要 GPU(实测)

    CPU 逐条  50.3 ms / 批 16  28.7 ms     315 条 = 15.9 s / 9.0 s   ← 孤立编码循环
    GPU 逐条  10.9 ms / 批 16   2.5 ms     315 条 =  3.4 s / 0.8 s

BGE 是纯推理,CPU 完全够:一条新记忆有 **20 轮**(≈60 秒)的余裕等它。
**没有显卡的机器不需要为它装 CUDA。** 有显卡是加分(4.6~11 倍),不是前提 ——
所以 `device` 默认 `"cpu"`,要上卡得显式说。

⚠️ 上面那组是**孤立编码循环**的数;走真实路径(`MemoryCache.fill_one`,带 sqlite
   写和线程池冷启动)会慢到约 **147 ms/条**。所以报预算时用后者,别用前者。
"""

from __future__ import annotations

import threading
from typing import Any, Iterable

BGE_NAME = "BAAI/bge-large-zh-v1.5"
# BGE 官方用法:**query 侧加指令前缀,passage 侧不加**。
# ⚠️ 两侧不对称是设计,不是漏写 —— "统一一下"会同时弄坏两边。
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章:"


def available(modules: Iterable[str] = ("torch", "transformers")) -> bool:
    """两个包在不在。

    ⚠️ **只探路,不 import** —— `import torch` 本身要 1.16 秒,拿它来问
       "能不能用",等于把懒加载白做了(`importlib.util.find_spec` 只查文件)。
    """
    import importlib.util

    return all(importlib.util.find_spec(m) is not None for m in modules)


class BgeEmbedder:
    """本地 BGE。**不依赖 sentence-transformers** —— 直接用 transformers 复刻其用法。

    device: `"cpu"`(默认)| `"cuda"` | `"cuda:0"` … 上卡要显式说,理由见模块头。
    threads: 编码时的 torch 线程数。**默认 None = 不碰那个全局值**(= 用它自己的默认)。

        ⚠️ 这个参数**别拿来"让路"** —— 我第一版拍了 `threads=2`,理由是
           "别和前台抢 CPU",**实测两头都更糟**(2026-09-21,走完整的
           `MemoryCache.fill_one` 路径,不是孤立编码循环):

               threads= 2   →  605 ms/条   315 条 = 190.6 s
               threads= 8   →  324 ms/条   315 条 = 102.2 s
               threads=24   →  147 ms/条   315 条 =  46.4 s

           后台补向量只在引擎锁**空闲**时才跑(见 core/memory_cache.py 的 guard),
           所以它跑的时候根本没有前台轮在跑 —— 压线程让不开任何东西,只是把
           索引建成拖慢 4 倍。**前台唯一会等它的时刻**是"她正好在这条编码期间
           发消息"(那一刻要等它跑完,上限就是一次编码),所以线程越少,
           她反而等得越久。
           留着这个旋钮是给**真的会被占满的机器**用的(比如同时跑本地大模型)。
    """

    name = BGE_NAME
    dim = 1024          # bge-large-zh-v1.5;换模型时这里也要跟着换

    def __init__(
        self,
        name: str = BGE_NAME,
        *,
        device: str = "cpu",
        local_files_only: bool = True,
        threads: int | None = None,
        max_length: int = 512,
    ) -> None:
        self.name = name
        self.device = device
        self.local_files_only = local_files_only
        self.threads = threads
        self.max_length = max_length
        self._encode: Any = None            # 加载完才有:list[str] -> list[list[float]]
        self._device_used: str | None = None
        self._lock = threading.Lock()       # 后台线程与查询可能同时第一次用到它

    # ---------- 加载 ----------

    @property
    def loaded(self) -> bool:
        return self._encode is not None

    def warm(self) -> "BgeEmbedder":
        """把模型读进来(4.18 s)。**幂等**,重复调不重复加载。

        调用点:后台线程的第一件事 —— 见模块头"暖机要早,不要等到要用"。
        """
        self._ensure_loaded()
        return self

    def _ensure_loaded(self) -> None:
        if self._encode is not None:
            return
        with self._lock:                     # 双检:并发的第一个查询只加载一次
            if self._encode is not None:
                return
            import torch                                    # ← 懒:不进文件头
            from transformers import AutoModel, AutoTokenizer

            if self.threads is not None:
                torch.set_num_threads(int(self.threads))
            tok = AutoTokenizer.from_pretrained(
                self.name, local_files_only=self.local_files_only)
            model = AutoModel.from_pretrained(
                self.name, local_files_only=self.local_files_only).eval()
            if self.device != "cpu":
                model = model.to(self.device)
            self._device_used = str(model.device)

            def encode(texts: list[str]) -> list[list[float]]:
                batch = tok(list(texts), padding=True, truncation=True,
                            max_length=self.max_length, return_tensors="pt")
                batch = {k: v.to(model.device) for k, v in batch.items()}
                with torch.no_grad():
                    out = model(**batch)
                # BGE 取 CLS 位 + L2 归一化,之后余弦就是点积
                vecs = torch.nn.functional.normalize(
                    out.last_hidden_state[:, 0], dim=-1)
                return vecs.tolist()

            self._encode = encode

    # ---------- 编码(MemoryIndex 认的协议) ----------

    def _embed(self, texts: list[str]) -> list[list[float]]:
        self._ensure_loaded()
        return self._encode(texts)

    def passage(self, text: str) -> list[float]:
        """被索引的正文。**不加**前缀。"""
        return self._embed([text])[0]

    def query(self, text: str) -> list[float]:
        """她的问句。**加**官方指令前缀(与 passage 不对称是设计)。"""
        return self._embed([QUERY_PREFIX + text])[0]

    def info(self) -> dict:
        return {
            "name": self.name,
            "loaded": self.loaded,
            "device": self._device_used or self.device,
            "threads": self.threads,
            "local_files_only": self.local_files_only,
        }


def get_embedder(
    name: str = BGE_NAME, *, device: str = "cpu", threads: int | None = None
) -> BgeEmbedder | None:
    """拿一个嵌入器;**拿不到返回 None**(不抛)。

    返回 None = 这台机器没装 torch/transformers → 调用方退化成"只有关键词那一路"。
    注意它**不加载权重** —— 真正的加载在 `warm()` / 第一次编码时。
    """
    if not available():
        return None
    return BgeEmbedder(name, device=device, threads=threads)
