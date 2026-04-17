import json
import os
from output_format.convert import Convert

# response_parser = Convert.()

class Memory:
    def __init__(self, system_prompt, memory_path, max_history_rounds=5):
        self.system_prompt= system_prompt
        self.memory_path = memory_path
        self.max_history_rounds = max_history_rounds
        self.memory_history = []
        self._load_memory()

    def _load_memory(self):
        """初始化，加载历史记录存入类，并且执行空文件保护"""
        try:
            if os.path.exists(self.memory_path):
                with open(self.memory_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()

                # 为空时初始化
                if not content:
                    self.memory_history = []
                    if self.system_prompt:
                        self.memory_history = [Convert().context_builder(
                            type="system_prompt",
                            system_prompt=self.system_prompt
                        )]
                    self.save_memory()
                    return

                # 加载现有的记忆历史
                data = json.loads(content)

                # 更新最新的system_prompt，保证实时
                if isinstance(data, list) and len(data) > 0 and data[0][0].get("type") == "system_prompt":
                    data[0][0]["message"][0]["content"]= self.system_prompt
                    self.memory_history = data
            else:
                self.memory_history = []
                self.save_memory()

        except json.JSONDecodeError:
            self.memory_history = []
            self.save_memory()
        
    def save_memory(self):
        """保存聊天记录文件至指定文件夹"""
        try:
            with open(self.memory_path, "w", encoding="utf-8") as f:
                json.dump(self.memory_history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"\n[保存失败] {e}")

    def trim_history(self):
        """限制对话轮数"""
        keep = 1 + self.max_history_rounds
        if len(self.memory_history) > keep:
            final_history=[]
            if self.system_prompt:
                final_history.append(self.memory_history[0])
            final_history= final_history + self.memory_history[-(self.max_history_rounds):]
            self.memory_history=final_history

class EmbedMemory:
    def __init__(self, embedmemory_path):
        self.embedmemory_path = embedmemory_path
        self.memory_history = []

    def _load_memory(self):
        """读取embedding文件，返回记录的json格式"""
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
        """追加新的向量内容并保存向量库文件"""
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
        """容量保护，有利于删除部分历史记录时同时更新向量库"""
        # 和对话的id数保持一致
        try:
            data = self._load_memory()
            new_data = []
            changed = False

            for item in data:
                if item.get("embedding_id", 0) <= dialogue_id:
                    new_data.append(item)
                else:
                    changed = True  # 说明有溢出

            # 只有真的变了才写文件
            if changed:
                with open(self.embedmemory_path, "w", encoding="utf-8") as f:
                    json.dump(new_data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            print(f"\n[删除失败] {e}")
