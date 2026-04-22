from core.chat import Chat
import logging

SYSTEM_PROMPT = "你是一个叫做小夜子的少女，温柔乖巧为主，有一点轻轻的活泼。不要长篇大论，像日常聊天一样中短回应就好。"

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def start_chat():
    """
    启动对话主循环。
    """
    chat = Chat(system_prompt=SYSTEM_PROMPT)
    logging.info("=== 小夜子（带记忆+错误保护）===")
    while True:
        msg = input("你: ")
        if msg.lower() in ["exit", "quit", "退出"]:
            logging.info("✅ 记忆已保存")
            break

        try:
            result = chat.chat(msg)
        except Exception as e:
            logging.error(f"对话出错: {e}")

if __name__ == "__main__":
    start_chat()