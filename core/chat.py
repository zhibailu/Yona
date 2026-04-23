from openai import OpenAI
from output_format.convert import Convert
from core.memory import Memory, EmbedMemory
from rag.embedding import Embedding
import os
from core.state import State
from config.loader import Config
from core.tool_call import ToolCallManager
from tools import ALL_TOOLS

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
        self.memory = Memory(self.system_prompt, self.memory_path, config.get("memory", "max_history_rounds"))
        self.memory_history = self.memory.memory_history
        self.tools = tools
        self.tool_choice = tool_choice
        self.tool_call_manager = ToolCallManager()

    def chat(self, user_input):
        try:
            # 准备对话 ID
            if self.memory_history and "id" in self.memory_history[-1][0]:
                last_id = self.memory_history[-1][0].get("id", 0)
                dialogue_id = last_id + 1
            else:
                dialogue_id = 1

            # 调用 OpenAI API，先过一边tool
            client = OpenAI(
                api_key=self.chat_config["api_key"],
                base_url=self.chat_config["base_url"]
            )
            response = client.chat.completions.create(
                model=self.chat_config["model"],
                messages=[{"role": "user", "content": user_input}],
                max_tokens=self.chat_config["max_tokens"],
                temperature=self.chat_config["temperature"],
                top_p=self.chat_config["top_p"],
                stream=self.chat_config["stream"],
                tools=ALL_TOOLS
            )

            result, meta = Convert().response_parser(response=response, _print=False)

            # message空值保护
            messages = self.build_context(user_input)

            # 检查是否触发 Tool Call，再次调用LLM
            if meta.get("finish_reason") == "tool_calls":
                tool_outputs = []
                # 遍历组装tool_call的结果
                for tool_call in result:
                    tool_name = tool_call["name"]
                    arguments = tool_call["arguments"]
                    
                    print("执行",tool_name)
                    # 统一交给工具管理器执行（不知道后面的tool还有没有要别的参数的，实在不行把所有参数拉进来）
                    res = self.tool_call_manager.execute_tool(
                        tool_name,
                        arguments,
                        dialogue_id=dialogue_id,
                        max_history=config.get("memory", "max_history_rounds")
                    )
                    if res:
                        tool_outputs.append(res)

                # 统一构建上下文
                messages = self.build_context(user_input, tool_outputs=tool_outputs)

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
                id=dialogue_id,
                user_input=user_input,
                ai_output=result,
                meta=meta
            ))
            self.memory.save_memory()

            # 存向量库
            try:
                embedder = Embedding()
                embed_memory = EmbedMemory(self.embedmemory_path)

                # 修改嵌入格式，去除无意义的结构符号，保留纯粹语义
                embed_content = f"{config.get('system', 'user')}: {user_input} {config.get('system', 'ai_name')}: {result}"

                # 生成 AI 回复的嵌入
                embedding = embedder.embed(embed_content)

                # 用于存档的格式
                embed_data = Convert().embed_builder(
                    embedding=embedding,
                    embedding_id=dialogue_id,
                    content_id=dialogue_id,
                    user_input=user_input,
                    result=result
                )
                embed_memory.save_memory(embed_data)  # 存储嵌入数据
                embed_memory.trim_memory(dialogue_id)  # 修剪对齐
                print("✅ 嵌入存储成功")

            except Exception as e:
                print(f"⚠️ 嵌入生成或存储失败: {e}")

            return result
        except Exception as e:
            print(f"❌ 服务异常：{e}")
        
    def build_context(self, user_input, tool_outputs=None):
        messages = []

        # 将系统提示词放在最前面
        if self.memory_history and self.memory_history[0][0]["type"] == "system_prompt":
            messages.extend(self.memory_history[0][0]["message"])

        # 添加对话历史记录（不包括系统提示词）
        final_history = self.memory.trim_history()
        for record in final_history:
            if record[0].get("type") != "system_prompt":
                messages.extend(record[0]["message"])

        # 拼接 Tool Call 结果和用户输入
        if tool_outputs:
            content = ""
            for output in tool_outputs:
                content += output + "\n"
            messages.append({"role": "user", "content": content+ "用户本轮输入为："+user_input})
        else:
            messages.append({"role": "user", "content": user_input})

        return messages