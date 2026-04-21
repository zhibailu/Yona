from openai import OpenAI
from output_format.convert import Convert
from core.memory import Memory, EmbedMemory
from rag.embedding import Embedding
import os
from core.state import State
from config.loader import Config
from core.tool_call import ToolCallManager

DATA_DIR = os.path.abspath(os.path.join(os.getcwd()))

state = State()
config = Config()

class Chat:
    """
    Chat 类负责处理对话逻辑，包括记忆管理、工具调用以及嵌入生成。
    """
    def __init__(self,
                chat_config=None,
                memory_path=None,
                embedmemory_path=None,
                system_prompt="",
                tools=None,
                tool_choice=None):
        """
        初始化 Chat 类。
        参数：
            - chat_config: 对话配置字典，从配置文件加载。
            - memory_path: 记忆文件路径。
            - embedmemory_path: 嵌入记忆文件路径。
            - system_prompt: 系统提示词，用于引导对话风格。
            - tools: 工具列表，用于扩展对话功能。
            - tool_choice: 当前工具选择配置。
        """
        self.chat_config = chat_config or {
            "api_key": config.get("chat", "api_key"),
            "base_url": config.get("chat", "base_url"),
            "model": config.get("chat", "model"),
            "max_tokens": config.get("chat", "max_tokens"),
            "temperature": config.get("chat", "temperature"),
            "top_p": config.get("chat", "top_p"),
            "stop": config.get("chat", "stop"),
            "frequency_penalty": config.get("chat", "frequency_penalty"),
            "presence_penalty": config.get("chat", "presence_penalty"),
            "n": config.get("chat", "n"),
            "response_format": config.get("chat", "response_format"),
            "stream": config.get("chat", "stream")
        }
        self.memory_path = memory_path or os.path.join(DATA_DIR, "history/conversation_memory.json")
        self.embedmemory_path = embedmemory_path or os.path.join(DATA_DIR, "history/embedding_memory.json")
        self.system_prompt = system_prompt
        self.memory = Memory(self.system_prompt, self.memory_path)
        self.memory_history = self.memory.memory_history
        self.tools = tools
        self.tool_choice = tool_choice
        self.tool_call_manager = ToolCallManager()

    def chat(self, user_input):
        # try:
        # 构建消息上下文
        messages = self.build_context(user_input)

        # 调用 OpenAI API
        client = OpenAI(
            api_key=self.chat_config["api_key"],
            base_url=self.chat_config["base_url"]
        )
        response = client.chat.completions.create(
            model=self.chat_config["model"],
            messages=messages,
            max_tokens=self.chat_config["max_tokens"],
            temperature=self.chat_config["temperature"],
            top_p=self.chat_config["top_p"],
            stream=self.chat_config["stream"],
            tools=[
                {
                "type": "function",
                "function": {
                        "name": "trigger_rag",
                        "description": "当用户需要回忆历史对话、过往内容、上下文时调用，必须把用户的问题原封不动填入query",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "必须是用户当前的问题，原样复制，不要修改"
                                }
                            },
                            "required": ["query"]
                        }
                    }
                }
            ]
        )


        # 检查是否触发 Tool Call
        tool_calls, _ = Convert().response_parser(response=response)

        # 执行 Tool Call
        rag_results = None
        for tool_call in tool_calls:
            if tool_call["name"] == "trigger_rag":
                arguments = tool_call["arguments"]
                print(f"🔧 Tool Call 触发: {tool_call['name']}, 参数: {arguments}")
                rag_results = self.tool_call_manager.execute_tool(tool_call["name"], arguments)
                break
        
        # 构建上下文（包含 Tool Call 的结果）
        messages = self.build_context(user_input, rag_results=rag_results)

        # 再次调用 OpenAI API，生成最终回复
        response = client.chat.completions.create(
            model=self.chat_config["model"],
            messages=messages,
            max_tokens=self.chat_config["max_tokens"],
            temperature=self.chat_config["temperature"],
            top_p=self.chat_config["top_p"],
            stream=self.chat_config["stream"]
        )

        # 解析最终回复
        result, meta = Convert().response_parser(response=response)

        # 将当前对话存储到历史记录
        self.memory_history.append(Convert().context_builder(
            type="dialogue",
            id=len(self.memory_history) + 1,
            user_input=user_input,
            ai_output=result,
            meta=meta
        ))
        self.memory.save_memory()

        return result
        # except Exception as e:
        #     print(f"❌ 服务异常：{e}")
        #     return "抱歉，我无法处理您的请求。"
        
    def build_context(self, user_input, rag_results=None):
        messages = []

        # 将系统提示词放在最前面
        if self.memory_history and self.memory_history[0][0]["type"] == "system_prompt":
            messages.extend(self.memory_history[0][0]["message"])

        # 添加对话历史记录（不包括系统提示词）
        for record in self.memory_history[1:]:
            messages.extend(record[0]["message"])

        # 拼接 Tool Call 结果和用户输入
        if rag_results:
            rag_content = "以下是检索到的相关信息：\n"
            for idx, result in enumerate(rag_results, start=1):  # 直接使用 query_with_format 返回的结果
                rag_content += f"{idx}. {result}\n"
            rag_content += f"用户的实际问题：{user_input}"
            messages.append({"role": "user", "content": rag_content})
        else:
            messages.append({"role": "user", "content": user_input})

        return messages