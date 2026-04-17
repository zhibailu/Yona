from openai import OpenAI
from output_format.convert import Convert
from core.memory import Memory,EmbedMemory
from rag.embedding import Embedding
import os
from core.state import State
from config.loader import Config

DATA_DIR = os.path.abspath(os.path.join(os.getcwd()))

state = State()
config = Config()
class Chat():
    def __init__(self,
                chat_config = {
                    "api_key": config.get("chat","api_key"),
                    "base_url": config.get("chat","base_url"),
                    "model": config.get("chat","model"),
                    "max_tokens":config.get("chat","max_tokens"),
                    "temperature":config.get("chat","temperature"),
                    "top_p":config.get("chat","top_p"),
                    "stop":config.get("chat","stop"),
                    "frequency_penalty":config.get("chat","frequency_penalty"),
                    "presence_penalty":config.get("chat","presence_penalty"),
                    "n":config.get("chat","n"),
                    "response_format":config.get("chat","response_format"),
                    "stream":config.get("chat","stream")
                },
                memory_path = os.path.join(DATA_DIR, "history\\conversation_memory.json"),
                embedmemory_path = os.path.join(DATA_DIR, "history\\embedding_memory.json"),
                system_prompt="",
                # 测试部分
                tools=state.delta_tools,
                tool_choice={"type": "function", "function": {"name": config.get("state","delta_tools_name")}} # 强制调用
                 ):
        
        self.chat_config = chat_config
        self.memory_path = memory_path
        self.embedmemory_path = embedmemory_path
        self.system_prompt = system_prompt
        self.memory_history=[]  # 文件里的所有东西
        self.memory = Memory(self.system_prompt, self.memory_path)
    
        # 加载历史记录
        self.memory_history = self.memory.memory_history

        # tool部分设计state，测试
        self.tools = tools
        self.tool_choice = tool_choice

    def chat(self, user_input):
        
        # 准备meta集里的id
        if self.memory_history and "id" in self.memory_history[-1][0]:  # 有记录就加
            last_id = self.memory_history[-1][0].get("id")
            id = last_id + 1
        else:  # 剩下三种情况都从新开始
            id = 1

        client = OpenAI(
            api_key=self.chat_config["api_key"],
            base_url=self.chat_config["base_url"],
        )
        try:
            # 全是记忆结构，后面要打包成一个convert才行，得扩展的
            message = []

            # 轮次保护，以此为界，前面的是完整的，后面的只为输出效果服务
            self.memory.trim_history()
            self.memory_history = self.memory.memory_history

            for i in self.memory_history:
                message.extend(i[0]["message"])

            message.append({"role": "user", "content": user_input})

            # 添加历史记录和当前输入
            a=self.chat_config["max_tokens"]

            response = client.chat.completions.create(
                model=self.chat_config["model"],
                messages=message,
                max_tokens=self.chat_config["max_tokens"],
                temperature=self.chat_config["temperature"],
                top_p=self.chat_config["top_p"],
                stop=self.chat_config["stop"],
                frequency_penalty=self.chat_config["frequency_penalty"],
                presence_penalty=self.chat_config["presence_penalty"],
                n=self.chat_config["n"],
                response_format=self.chat_config["response_format"],
                stream=self.chat_config["stream"],

                # tools测试
                # tools=tools,
                # tool_choice="auto"
            )

            print("ai: ", end="")
            result, login = Convert().response_parser(response=response)  # 有问题，本来到这里就该输出了，为什么会等很久呢，前面也没进程啊
            print()

            # 把AI回复加入记忆，这里要加几个finish_reason的判断
            if login.get("finish_reason") in ["stop", "length"]:
                self.memory_history.append(Convert().context_builder(
                    type="dialogue",
                    id=id,
                    user_input=user_input,
                    ai_output=result,
                    meta=login
                ))

                # 保存历史记录
            
            self.memory.save_memory()

            # 把回复存入向量库
            embed=Embedding()
            embed_memory = EmbedMemory(self.embedmemory_path)

            # 有点久，后面做异步
            try:
                embedding = embed.embed(result)
                embedding_id=id
                if self.system_prompt:
                    embedding_id = embedding_id-1
                embed_history = Convert().embed_builder(
                    embedding,
                    embedding_id=embedding_id,
                    content_id=id,
                    content=[
                        {"role": "user", "content": user_input},
                        {"role": "assistant", "content": result}
                    ]
                )

                # 存储并矫正向量库
                embed_memory.save_memory(embed_history)
            except Exception as e:
                print("⚠️ embedding失败，已跳过")

            embed_memory.trim_memory(id)


            return result
    
        except Exception as e:
            print(f"\n\n❌ 服务异常：{str(e)}")