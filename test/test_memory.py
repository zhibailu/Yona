import unittest
from core.memory import Memory

class TestMemory(unittest.TestCase):
    """
    测试 Memory 类的功能，包括加载、保存和修剪历史记录。
    """
    def setUp(self):
        """
        初始化测试环境，在每个测试方法前运行。
        """
        self.memory = Memory(system_prompt="测试提示词", memory_path="tests/test_memory.json")

    def test_initialize_memory(self):
        """
        测试记忆初始化功能。
        """
        self.assertEqual(len(self.memory.memory_history), 1)
        self.assertEqual(self.memory.memory_history[0][0]["type"], "system_prompt")

    def test_save_and_load_memory(self):
        """
        测试记忆的保存和加载功能。
        """
        self.memory.memory_history.append({"test_key": "test_value"})
        self.memory.save_memory()

        new_memory = Memory(system_prompt="测试提示词", memory_path="tests/test_memory.json")
        self.assertEqual(len(new_memory.memory_history), 2)
        self.assertEqual(new_memory.memory_history[1]["test_key"], "test_value")

    def test_trim_history(self):
        """
        测试记忆修剪功能。
        """
        for i in range(10):
            self.memory.memory_history.append({"round": i})
        self.memory.trim_history()
        self.assertEqual(len(self.memory.memory_history), 6)  # 默认最大保存 5 轮 + 提示词

if __name__ == "__main__":
    unittest.main()