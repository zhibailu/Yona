import json
from rag.embedding import Embedding

class Retrieve:
    """
    Retrieve 类用于实现向量检索功能，从嵌入记忆中查询与输入最匹配的结果。
    """
    def __init__(self, embedding_memory_path):
        """
        初始化 Retrieve 类。
        参数：
            - embedding_memory_path: 嵌入记忆文件路径，用于加载向量数据。
        """
        self.embedding_memory_path = embedding_memory_path
        self.embedding_data = self._load_embedding_memory()

    def _load_embedding_memory(self):
        """
        加载嵌入记忆文件内容。
        返回：
            - 嵌入数据列表（如果文件存在且内容有效），否则返回空列表。
        """
        try:
            with open(self.embedding_memory_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            print(f"⚠️ 未找到嵌入记忆文件或文件内容无效: {self.embedding_memory_path}")
            return []

    def query_with_format(self, input_text, top_k=3):
        """
        查询嵌入记忆中与输入文本最匹配的结果，并格式化输出。
        参数：
            - input_text: 用户输入文本。
            - top_k: 返回结果的数量（默认为 3）。
        返回：
            - 格式化的检索结果列表。
        """
        embedder = Embedding()
        # 生成输入文本的嵌入
        input_embedding = embedder.embed(input_text)

        if not input_embedding or not self.embedding_data:
            print("❌ 无法查询，嵌入数据或输入嵌入无效")
            return []

        results = []
        for record in self.embedding_data:
            embedding = record.get("embedding")
            content = record.get("content")  # 提取语义内容
            if embedding and content:
                similarity = self._calculate_similarity(input_embedding, embedding)
                results.append({"content": content, "similarity": similarity})

        # 排序结果并格式化
        results = sorted(results, key=lambda x: x["similarity"], reverse=True)
        return [result["content"] for result in results[:top_k]]

    def _calculate_similarity(self, embedding_a, embedding_b):
        """
        计算两个嵌入向量的余弦相似度。
        参数：
            - embedding_a: 第一个嵌入向量。
            - embedding_b: 第二个嵌入向量。
        返回：
            - 余弦相似度分值。
        """
        import numpy as np
        a = np.array(embedding_a)
        b = np.array(embedding_b)
        return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))