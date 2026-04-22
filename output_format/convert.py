<<<<<<< HEAD
import datetime
from typing import Optional, Callable

class Convert:
    """
    输出转化总控
    """
    def response_parser(self, response, callback=None):
        """"处理 AI 的回复: response"""
        return ResponseParser(response).parse(callback=callback)
    
    def context_builder(self, type, **kwargs):
        """各注入模块处理"""
        return ContextBuilder(type).parse(**kwargs)
    
    def embed_builder(self, embedding, **kwargs):
        """构建嵌入数据"""
        return EmbedBuilder(embedding).parse(**kwargs)

class EmbedBuilder:
    """
    嵌入构建类，用于生成嵌入记忆的记录。
    """
    def __init__(self, embedding):
        self.embedding = embedding

    def parse(self, **kwargs):
        """
        构建嵌入记录。
        参数：
            - embedding_id: 嵌入 ID。
            - content_id: 对应内容的 ID。
            - content: 对话内容，包括用户输入和 AI 输出。
        返回：
            - 构建的嵌入记录对象。
        """
        result = {
            "embedding_id": kwargs.get("embedding_id"),
            "content_id": kwargs.get("content_id"),
            "embedding_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content": kwargs.get("content"),
            "embedding": self.embedding
        }
        return result
        

class ContextBuilder:
    """
    包装需存入历史记录的数据。
    """
    def __init__(self, type):
        self.type = type

    def parse(self, **kwargs):
        """
        分发器，根据上下文类型调用不同的处理方法。
        """
        if self.type == "system_prompt":
            return self._system_prompt(**kwargs)
        elif self.type == "dialogue":
            return self._dialogue(**kwargs)
        else:
            raise ValueError(f"Unknown context type: {self.type}, must be 'system_prompt' or 'dialogue'.")

    def _system_prompt(self, **kwargs):
        """
        处理系统提示词。
        """
        result = [{
            "id": 0,
            "type": "system_prompt",
            "message": [{
                "role": "system",
                "content": str(kwargs.get("system_prompt"))
            }]
        }]
        return result
    
    def _dialogue(self, **kwargs):
        """
        处理对话部分。
        确保所有必需字段（id、user_input、ai_output、meta）都存在。
        """
        required_fields = ["user_input", "ai_output", "meta"]
        missing = [field for field in required_fields if field not in kwargs]
        
        # 自动补全 id，如果未提供则从 1 开始
        dialogue_id = kwargs.get("id", 1)

        if missing:
            raise ValueError(f"dialogue missing required fields: {missing}")
    
        result = [{
            "id": dialogue_id,
            "type": "dialogue",
            "message": [
                {"role": "user", "content": str(kwargs.get("user_input"))},
                {"role": "assistant", "content": str(kwargs.get("ai_output"))}
            ],
            "meta": kwargs.get("meta")
        }]
        return result


class ResponseParser:
    """
    解析 AI 的响应。
    支持非流式和流式响应的处理。
    """
    def __init__(self, response):
        self.response = response
        self.full_text = ""
        self.login_info = {}

    def parse(self, callback: Optional[Callable[[str], None]] = None):
        """
        解析响应。
        如果是流式响应，则通过回调函数处理内容。
        """
        if self._is_stream():
            self._handle_stream(self.response, callback)
        else:
            self._handle_normal(self.response)

        return self.full_text, self.login_info

    def _is_stream(self):
        """
        自动判断是否是流式响应。
        """
        from openai import Stream
        return isinstance(self.response, Stream)

    def _handle_normal(self, response):
        """
        处理非流式响应。
        """
        self.login_info = {
            "time": datetime.datetime.fromtimestamp(response.created).strftime("%Y-%m-%d %H:%M:%S"),
            "model": response.model,
            "object": response.object,
            "finish_reason": response.choices[0].finish_reason,
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens
        }

        finish_reason = response.choices[0].finish_reason

        if finish_reason == "stop":
            self.full_text = response.choices[0].message.content
        elif finish_reason == "tool_calls":
            self.full_text = response.choices[0].message.tool_calls[0].function.arguments
        elif finish_reason == "content_filter":
            self.full_text = "filter"
        elif finish_reason == "length":
            self.full_text = "out length"
        else:
            self.full_text = ""
        # print(self.full_text)

    def _handle_stream(self, response, callback):
        """
        处理流式响应，提取 Tool Call 信息和其他元数据。
        """
        model = ""
        _object = ""
        full_text = ""
        final_usage = None
        final_finish = None

        tool_calls = []  # 初始化 Tool Call 信息
        _tool_name = ""
        _tool_arguments = ""

        for chunk in response:
            if not chunk.choices:
                if hasattr(chunk, "usage") and chunk.usage:
                    final_usage = chunk.usage
                    _object = chunk.object
                continue

            delta = chunk.choices[0].delta

            if not model and hasattr(chunk, "model"):
                model = chunk.model

            # dialogue的内容片段
            if hasattr(delta, "content") and delta.content:
                content = delta.content
                print(content, end="", flush=True)
                full_text += content
                if callback:
                    callback(content)

            # 检测 Tool Call 信息
            if hasattr(delta, "tool_calls") and delta.tool_calls:
                tool_chunk = delta.tool_calls[0]

                if tool_chunk.function.name:
                    _tool_name = tool_chunk.function.name
                    # if _tool_name == "trigger_rag":
                    #     # 若是单纯的触发器，则提前结束，避免多余操作
                    #     break

                # 累加 arguments
                if tool_chunk.function.arguments:
                    _tool_arguments += tool_chunk.function.arguments

            # 结束标记
            if chunk.choices[0].finish_reason:
                final_finish = chunk.choices[0].finish_reason

        # 最终文本
        self.full_text = full_text

        # 生成拼接完整 tool_calls
        if _tool_name:
            tool_calls = [
                {
                    "name": _tool_name,
                    "arguments": _tool_arguments
                }
            ]
            self.full_text = tool_calls
        else:
            self.tool_calls = []

        self.login_info = {
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "model": model,
            "object": _object,
            "finish_reason": final_finish,
            "input_tokens": final_usage.prompt_tokens if final_usage else 0,
            "output_tokens": final_usage.completion_tokens if final_usage else 0,
            "total_tokens": final_usage.total_tokens if final_usage else 0
=======
import datetime
from typing import Optional, Callable

class Convert:
    """
    输出转化总控
    """
    def response_parser(self, response, callback=None):
        """"处理 AI 的回复: response"""
        return ResponseParser(response).parse(callback=callback)
    
    def context_builder(self, type, **kwargs):
        """各注入模块处理"""
        return ContextBuilder(type).parse(**kwargs)
    
    def embed_builder(self, embedding, **kwargs):
        """构建嵌入数据"""
        return EmbedBuilder(embedding).parse(**kwargs)

class EmbedBuilder:
    """
    嵌入构建类，用于生成嵌入记忆的记录。
    """
    def __init__(self, embedding):
        self.embedding = embedding

    def parse(self, **kwargs):
        """
        构建嵌入记录。
        参数：
            - embedding_id: 嵌入 ID。
            - content_id: 对应内容的 ID。
            - content: 对话内容，包括用户输入和 AI 输出。
        返回：
            - 构建的嵌入记录对象。
        """
        result = {
            "embedding_id": kwargs.get("embedding_id"),
            "content_id": kwargs.get("content_id"),
            "embedding_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content": kwargs.get("content"),
            "embedding": self.embedding
        }
        return result
        

class ContextBuilder:
    """
    包装需存入历史记录的数据。
    """
    def __init__(self, type):
        self.type = type

    def parse(self, **kwargs):
        """
        分发器，根据上下文类型调用不同的处理方法。
        """
        if self.type == "system_prompt":
            return self._system_prompt(**kwargs)
        elif self.type == "dialogue":
            return self._dialogue(**kwargs)
        else:
            raise ValueError(f"Unknown context type: {self.type}, must be 'system_prompt' or 'dialogue'.")

    def _system_prompt(self, **kwargs):
        """
        处理系统提示词。
        """
        result = [{
            "id": 0,
            "type": "system_prompt",
            "message": [{
                "role": "system",
                "content": str(kwargs.get("system_prompt"))
            }]
        }]
        return result
    
    def _dialogue(self, **kwargs):
        """
        处理对话部分。
        确保所有必需字段（id、user_input、ai_output、meta）都存在。
        """
        required_fields = ["user_input", "ai_output", "meta"]
        missing = [field for field in required_fields if field not in kwargs]
        
        # 自动补全 id，如果未提供则从 1 开始
        dialogue_id = kwargs.get("id", 1)

        if missing:
            raise ValueError(f"dialogue missing required fields: {missing}")
    
        result = [{
            "id": dialogue_id,
            "type": "dialogue",
            "message": [
                {"role": "user", "content": str(kwargs.get("user_input"))},
                {"role": "assistant", "content": str(kwargs.get("ai_output"))}
            ],
            "meta": kwargs.get("meta")
        }]
        return result


class ResponseParser:
    """
    解析 AI 的响应。
    支持非流式和流式响应的处理。
    """
    def __init__(self, response):
        self.response = response
        self.full_text = ""
        self.login_info = {}

    def parse(self, callback: Optional[Callable[[str], None]] = None):
        """
        解析响应。
        如果是流式响应，则通过回调函数处理内容。
        """
        if self._is_stream():
            self._handle_stream(self.response, callback)
        else:
            self._handle_normal(self.response)

        return self.full_text, self.login_info

    def _is_stream(self):
        """
        自动判断是否是流式响应。
        """
        from openai import Stream
        return isinstance(self.response, Stream)

    def _handle_normal(self, response):
        """
        处理非流式响应。
        """
        self.login_info = {
            "time": datetime.datetime.fromtimestamp(response.created).strftime("%Y-%m-%d %H:%M:%S"),
            "model": response.model,
            "object": response.object,
            "finish_reason": response.choices[0].finish_reason,
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens
        }

        finish_reason = response.choices[0].finish_reason

        if finish_reason == "stop":
            self.full_text = response.choices[0].message.content
        elif finish_reason == "tool_calls":
            self.full_text = response.choices[0].message.tool_calls[0].function.arguments
        elif finish_reason == "content_filter":
            self.full_text = "filter"
        elif finish_reason == "length":
            self.full_text = "out length"
        else:
            self.full_text = ""
        # print(self.full_text)

    def _handle_stream(self, response, callback):
        """
        处理流式响应，提取 Tool Call 信息和其他元数据。
        """
        model = ""
        _object = ""
        full_text = ""
        final_usage = None
        final_finish = None

        tool_calls = []  # 初始化 Tool Call 信息
        _tool_name = ""
        _tool_arguments = ""

        for chunk in response:
            if not chunk.choices:
                if hasattr(chunk, "usage") and chunk.usage:
                    final_usage = chunk.usage
                    _object = chunk.object
                continue

            delta = chunk.choices[0].delta

            if not model and hasattr(chunk, "model"):
                model = chunk.model

            # dialogue的内容片段
            if hasattr(delta, "content") and delta.content:
                content = delta.content
                print(content, end="", flush=True)
                full_text += content
                if callback:
                    callback(content)

            # 检测 Tool Call 信息
            if hasattr(delta, "tool_calls") and delta.tool_calls:
                tool_chunk = delta.tool_calls[0]

                if tool_chunk.function.name:
                    _tool_name = tool_chunk.function.name
                    # if _tool_name == "trigger_rag":
                    #     # 若是单纯的触发器，则提前结束，避免多余操作
                    #     break

                # 累加 arguments
                if tool_chunk.function.arguments:
                    _tool_arguments += tool_chunk.function.arguments

            # 结束标记
            if chunk.choices[0].finish_reason:
                final_finish = chunk.choices[0].finish_reason

        # 最终文本
        self.full_text = full_text

        # 生成拼接完整 tool_calls
        if _tool_name:
            tool_calls = [
                {
                    "name": _tool_name,
                    "arguments": _tool_arguments
                }
            ]
            self.full_text = tool_calls
        else:
            self.tool_calls = []

        self.login_info = {
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "model": model,
            "object": _object,
            "finish_reason": final_finish,
            "input_tokens": final_usage.prompt_tokens if final_usage else 0,
            "output_tokens": final_usage.completion_tokens if final_usage else 0,
            "total_tokens": final_usage.total_tokens if final_usage else 0
>>>>>>> 6639851a3a6813774391e09f907b7401da2316bd
        }