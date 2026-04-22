<<<<<<< HEAD
from sentence_transformers import SentenceTransformer
from huggingface_hub import snapshot_download
import torch

class Embedding:
    """
    Embedding 类负责加载嵌入模型并生成内容的向量表示。
    """
    def __init__(self, model_name='BAAI/bge-large-zh-v1.5'):
        """
        初始化嵌入类。
        参数：
            - model_name: 嵌入模型名称，支持从 Hugging Face 加载。
        """
        self.model_name = model_name
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = None

    def _is_cached(self):
        """
        检查本地是否已缓存模型。
        返回：
            - 是否缓存: 布尔值。
        """
        try:
            snapshot_download(self.model_name, local_files_only=True)
            return True
        except Exception:
            return False

    def _load_model(self):
        """
        加载嵌入模型。
        如果首次加载模型，则从 Hugging Face 下载。
        """
        if self.model is not None:
            return

        if not self._is_cached():
            print("⏳ 首次加载：正在从 Hugging Face 下载模型（可能较慢）")
        else:
            print("⚡ 使用本地缓存模型（快速加载）")

        self.model = SentenceTransformer(
            self.model_name,
            device=self.device,
            local_files_only=self._is_cached()
        )

    def embed(self, content, batch_size=32):
        """
        生成内容的向量表示。
        参数：
            - content: 待嵌入的内容（可以是纯文本或包含 "content" 字段的字典）。
            - batch_size: 批量处理大小。
        返回：
            - embedding: 嵌入向量（列表）。
        """
        try:
            self._load_model()

            # 如果输入是字典结构，提取 "content" 字段
            if isinstance(content, dict) and "content" in content:
                content = content["content"]

            # 如果输入是列表，逐项提取文本
            if isinstance(content, list):
                content = [item["content"] if isinstance(item, dict) and "content" in item else item for item in content]

            # 调用模型生成嵌入
            embedding = self.model.encode(
                content,
                batch_size=batch_size,
                show_progress_bar=True,
                normalize_embeddings=True
            )
            return embedding.tolist()
        except Exception as e:
            print(f"❌ 嵌入生成失败：{e}")
=======
from sentence_transformers import SentenceTransformer
from huggingface_hub import snapshot_download
import torch

class Embedding:
    """
    Embedding 类负责加载嵌入模型并生成内容的向量表示。
    """
    def __init__(self, model_name='BAAI/bge-large-zh-v1.5'):
        """
        初始化嵌入类。
        参数：
            - model_name: 嵌入模型名称，支持从 Hugging Face 加载。
        """
        self.model_name = model_name
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = None

    def _is_cached(self):
        """
        检查本地是否已缓存模型。
        返回：
            - 是否缓存: 布尔值。
        """
        try:
            snapshot_download(self.model_name, local_files_only=True)
            return True
        except Exception:
            return False

    def _load_model(self):
        """
        加载嵌入模型。
        如果首次加载模型，则从 Hugging Face 下载。
        """
        if self.model is not None:
            return

        if not self._is_cached():
            print("⏳ 首次加载：正在从 Hugging Face 下载模型（可能较慢）")
        else:
            print("⚡ 使用本地缓存模型（快速加载）")

        self.model = SentenceTransformer(
            self.model_name,
            device=self.device,
            local_files_only=self._is_cached()
        )

    def embed(self, content, batch_size=32):
        """
        生成内容的向量表示。
        参数：
            - content: 待嵌入的内容（可以是纯文本或包含 "content" 字段的字典）。
            - batch_size: 批量处理大小。
        返回：
            - embedding: 嵌入向量（列表）。
        """
        try:
            self._load_model()

            # 如果输入是字典结构，提取 "content" 字段
            if isinstance(content, dict) and "content" in content:
                content = content["content"]

            # 如果输入是列表，逐项提取文本
            if isinstance(content, list):
                content = [item["content"] if isinstance(item, dict) and "content" in item else item for item in content]

            # 调用模型生成嵌入
            embedding = self.model.encode(
                content,
                batch_size=batch_size,
                show_progress_bar=True,
                normalize_embeddings=True
            )
            return embedding.tolist()
        except Exception as e:
            print(f"❌ 嵌入生成失败：{e}")
>>>>>>> 6639851a3a6813774391e09f907b7401da2316bd
            return []