import json
import os
from output_format.convert import Convert

class Memory:
    """
    Memory 类用于管理对话记忆，支持加载、保存和修剪历史记录。
    """
    def __init__(self, system_prompt, memory_path, max_history_rounds=5):
        """
        初始化 Memory 类。
        参数：
            - system_prompt: 系统提示词，用于初始化记忆。
            - memory_path: 记忆文件路径，用于存储历史记录。
            - max_history_rounds: 最大保存的历史轮数，超过此轮数会自动修剪。
        """
        self.system_prompt = system_prompt
        self.memory_path = memory_path
        self.max_history_rounds = max_history_rounds
        self.memory_history = []
        self._load_memory()

    def _load_memory(self):
        """
        加载历史记录。如果文件不存在或内容为空，则初始化记忆。
        """
        try:
            if os.path.exists(self.memory_path):
                with open(self.memory_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()

                if not content:
                    self.memory_history = self._initialize_memory()
                    self.save_memory()
                    return

                data = json.loads(content)

                if isinstance(data, list) and len(data) > 0 and data[0][0].get("type") == "system_prompt":
                    data[0][0]["message"][0]["content"] = self.system_prompt
                    self.memory_history = data
            else:
                self.memory_history = self._initialize_memory()
                self.save_memory()
        except json.JSONDecodeError:
            self.memory_history = self._initialize_memory()
            self.save_memory()

    def _initialize_memory(self):
        """
        初始化记忆内容，包括系统提示词。
        """
        if self.system_prompt:
            return [Convert().context_builder(
                type="system_prompt",
                system_prompt=self.system_prompt
            )]
        return []

    def save_memory(self):
        """
        保存记忆到文件。
        """
        try:
            with open(self.memory_path, "w", encoding="utf-8") as f:
                json.dump(self.memory_history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"\n[保存失败] {e}")

    def trim_history(self):
        """
        修剪历史记录以限制对话轮数，保留最近的记录。
        """
        keep = 1 + self.max_history_rounds
        if len(self.memory_history) > keep:
            final_history = []
            if self.system_prompt:
                final_history.append(self.memory_history[0])
            final_history.extend(self.memory_history[-self.max_history_rounds:])
            self.memory_history = final_history


class EmbedMemory:
    """
    EmbedMemory 类用于管理嵌入记忆，支持加载、保存和修剪向量库。
    """
    def __init__(self, embedmemory_path):
        """
        初始化 EmbedMemory 类。
        参数：
            - embedmemory_path: 嵌入记忆文件路径，用于存储向量化内容。
        """
        self.embedmemory_path = embedmemory_path
        self.memory_history = []

    def _load_memory(self):
        """
        加载嵌入记忆内容。如果文件不存在或内容为空，则返回空列表。
        """
        if not os.path.exists(self.embedmemory_path):
            return []

        try:
            with open(self.embedmemory_path, "r", encoding="utf-8") as f:
                content = f.read().strip()

                if not content:
                    return []

                data = json.loads(content)

                if isinstance(data, dict):
                    return [data]

                return data
        except Exception:
            return []

    def save_memory(self, embed_history):
        """
        保存嵌入记忆到文件。支持追加新内容。
        参数：
            - embed_history: 新的嵌入内容。
        """
        try:
            os.makedirs(os.path.dirname(self.embedmemory_path), exist_ok=True)

            data = self._load_memory()
            data.append(embed_history)

            with open(self.embedmemory_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            print("保存向量成功")
        except Exception as e:
            print(f"\n[保存失败] {e}")

    def trim_memory(self, dialogue_id):
        """
        修剪嵌入记忆以限制容量，保留最近的记录。
        参数：
            - dialogue_id: 当前对话的 ID，用于修剪逻辑。
        """
        try:
            data = self._load_memory()
            new_data = []
            changed = False

            for item in data:
                if item.get("embedding_id", 0) <= dialogue_id:
                    new_data.append(item)
                else:
                    changed = True

            if changed:
                with open(self.embedmemory_path, "w", encoding="utf-8") as f:
                    json.dump(new_data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            print(f"\n[删除失败] {e}")