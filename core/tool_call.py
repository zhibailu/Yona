import os
import json

from tools.trigger_rag import execute

DATA_DIR = os.path.abspath(os.path.join(os.getcwd()))

class ToolCallManager:
    """
    Tool Call 管理器，负责处理所有工具调用逻辑。
    """
    def __init__(self):
        # 注册可用的工具
        self.tools = {
            "trigger_rag": self._handle_retrieve
        }

    def execute_tool(self, tool_name, arguments, dialogue_id=None, max_history=None):
        """
        执行指定的工具。
        参数：
            - tool_name: 工具名称（如 "retrieve"）。
            - arguments: 工具所需的参数。
        返回：
            - 工具执行的结果。
        """
        if tool_name not in self.tools:
            raise ValueError(f"未知的工具: {tool_name}")
        return self.tools[tool_name](arguments, dialogue_id, max_history)

    def _handle_retrieve(self, arguments, dialogue_id, max_history):
        # 处理rag的检索功能
        if isinstance(arguments, str):
            args_dict = json.loads(arguments)
        else:
            args_dict = arguments
            
        # 调用rag的 execute
        return execute(args_dict, dialogue_id, max_history)