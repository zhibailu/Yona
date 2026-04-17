from sentence_transformers import SentenceTransformer
from huggingface_hub import snapshot_download
import torch

class Embedding:
    def __init__(self, model_name='BAAI/bge-large-zh-v1.5'):
        self.model_name = model_name
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = None

    def _is_cached(self):
        """检验本地是否已经下载有模型"""
        try:
            snapshot_download(self.model_name, local_files_only=True)
            return True
        except:
            return False
        
    def _load_model(self):
        """加载模型"""
        if self.model is not None:
            return

        if not self._is_cached():
            print("⏳ 首次加载：正在从 Hugging Face 下载模型（可能较慢）")
        else:
            print("⚡ 使用本地缓存模型（快速加载）")

        self.model = SentenceTransformer(
            self.model_name,
            device=self.device,
            local_files_only=self._is_cached()  # 🔥关键
        )

    def embed(self, content, batch_size=32):
        """向量化"""
        # 加载向量模型
        try:
            self._load_model()
        except Exception as e:
            print(f"❌ 错误：加载模型失败！")
            print(f"   - 错误信息: {e}")
            print("   - 请检查网络连接（首次下载时需要）以及 'sentence-transformers' 和 'torch' 库是否已正确安装。")
            return
        
        embedding = self.model.encode(
            content,
            batch_size=batch_size,
            show_progress_bar=True, # 显示一个漂亮的进度条
            normalize_embeddings=True # 推荐对bge模型进行归一化，以获得更好的相似度计算效果
        )

        return embedding.tolist()
