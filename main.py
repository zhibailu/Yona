from core.chat import Chat

SYSTEM_PROMPT = "你的主要职责是对话，是一个叫做小夜子的少女，温柔乖巧为主，有一点轻轻的活泼。不要长篇大论，像日常聊天一样中短回应就好。"

chat=Chat(system_prompt=SYSTEM_PROMPT)
# chat=Chat()

if __name__ == "__main__":
    print("=== 小夜子（带记忆+错误保护）===")
    # 后面在程序UI的时候不能用while了，会加载滞后，预期应该是一次input就是一个完整的程序周期
    while True:
        msg = input("你: ")
        print()
        if msg.lower() in ["exit", "quit", "退出"]:
            print("✅ 记忆已保存")
            break
        result=chat.chat(msg)
        print("====================")