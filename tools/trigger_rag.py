from rag.retrieve import Retrieve
import os

def trigger_rag():
    return {
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

# 留操作接口
def execute(arguments: dict, dialogue_id: int, max_history: int):
    # 只有满足条件才执行检索
    if dialogue_id > max_history:
        DATA_DIR = os.getcwd()
        retriever = Retrieve(os.path.join(DATA_DIR, "history/embedding_memory.json"))
        rag_list = retriever.query_with_format(arguments["query"], top_k=3)

        # 拼接检索结果
        rag_content = "声明，触发了RAG功能，以下是检索到的相关信息：\n"
        for idx, result in enumerate(rag_list, start=1):
            rag_content += f"{idx}. {result}\n"

        return rag_content
    return None