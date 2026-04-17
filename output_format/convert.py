import datetime
from typing import Optional, Callable

class Convert:
    """
    输出转化总控
    """
    def response_parser(self, response, callback=None):
        """"处理ai的回复:response"""
        return ResponseParser(response).parse(callback=callback)
    
    def context_builder(self, type, **kwargs):
        """各注入模块处理"""
        return ContextBuilder(type).parse(**kwargs)
    
    def embed_builder(self, embedding, **kwargs):
        return EmbedBuilder(embedding).parse(**kwargs)

class EmbedBuilder:
    def __init__(self, embedding):
        self.embedding = embedding

    def parse(self, **kwargs):
        result = {
            "embedding_id" : kwargs.get("embedding_id"),
            "content_id" : kwargs.get("content_id"),
            "embedding_time" : datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content": kwargs.get("content"),
            "embeding" : self.embedding
        }
        return result
        

class ContextBuilder:
    """包装需存入历史记录的数据"""
    def __init__(self, type):
        self.type = type

    def parse(self, **kwargs):
        """分发器"""
        if self.type == "system_prompt":
            return self._system_prompt(**kwargs)
        elif self.type == "dialogue":
            return self._dialogue(**kwargs)
        else:
            raise ValueError(f"Unknown context type: {type}, must in system_prompt, dialogue")

    def _system_prompt(self, **kwargs):
        """处理系统提示词""" 
        result=[{
            "id": 0,
            "type": "system_prompt",
            "message": [{
                "role": "system",
                "content":str(kwargs.get("system_prompt"))
            }]
        }]
        return result
    
    def _dialogue(self,**kwargs):
        """处理对话部分"""
        required_fields = ["id", "user_input", "ai_output", "meta"]
        missing = [field for field in required_fields if field not in kwargs]

        if missing:
            raise ValueError(f"dialogue missing required fields: {missing}")
    
        result=[{
            "id": kwargs.get("id"),
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
    非流式保留message和对应login元数据
    流式开放callback窗口接收外函数处理流式输出，同时依然保留login元数据
    """
    def __init__(self, response):
        self.response = response

        self.full_text = ""
        self.login_info = {}

    def parse(self, callback: Optional[Callable[[str], None]] = None):
        # ppline
        if self._is_stream():
            self._handle_stream(self.response, callback)
        else:
            self._handle_normal(self.response)

        return self.full_text, self.login_info

    def _is_stream(self):
        """自动判断是否 stream=True 的响应"""
        from openai import Stream
        return isinstance(self.response, Stream)

    def _handle_normal(self,response):
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
            self.full_text = "null"
        else:
            self.full_text = ""
        print(self.full_text)

    def _handle_stream(self, response, callback):
        
        model=""
        _object=""
        full_text=""

        for chunk in response:
            if not chunk.choices:
                if hasattr(chunk, "usage") and chunk.usage:
                    final_usage = chunk.usage
                    _object = chunk.object
                continue

            delta = chunk.choices[0].delta

            if not model and hasattr(chunk, "model"):
                model = chunk.model

            # 内容片段
            if hasattr(delta, "content") and delta.content:
                content = delta.content
                print(content, end="", flush=True)
                full_text += content
                if callback:
                    callback(content)

            # 结束标记
            if chunk.choices[0].finish_reason:
                final_finish = chunk.choices[0].finish_reason

        # 最终文本
        self.full_text = full_text

        self.login_info = {
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "model": model,
            "object": _object,
            "finish_reason": final_finish,
            "input_tokens": final_usage.prompt_tokens if final_usage else 0,
            "output_tokens": final_usage.completion_tokens if final_usage else 0,
            "total_tokens": final_usage.total_tokens if final_usage else 0
        }
