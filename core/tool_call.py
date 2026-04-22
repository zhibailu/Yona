from rag.retrieve import Retrieve
import os
import json

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

    def execute_tool(self, tool_name, arguments):
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
        return self.tools[tool_name](arguments)

    def _handle_retrieve(self, arguments):
        """
        处理 RAG 检索工具。
        参数：
            - arguments: tool_call解析返回的json格式的字符串
        返回：
            - 检索结果列表。
        """
        #提取用户的输入，tool里有定义
        args_dict = json.loads(arguments)
        user_input = args_dict["query"]

        retriever = Retrieve(DATA_DIR+"\\history\\embedding_memory.json")
        return retriever.query_with_format(user_input, top_k=3)