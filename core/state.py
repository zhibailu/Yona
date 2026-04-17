from config.loader import Config

config=Config()

class State:
    def __init__(
        self,
        now_states=None,
        delta_tools=None,
    ):
        self.now_states = now_states or config.get("state","now_states")

        self.delta_tools = delta_tools or [
            {
                "type": "function",
                "function": {
                    "name": config.get("state","delta_tools_name"),
                    "description": "计算对话风格的变化量",
                    "parameters": {
                        "type": "object",
                        "properties": config.get("state","delta_tools_properties"),
                        "required": [
                            "assertiveness_delta",
                            "uncertainty_delta",
                            "warmth_delta"
                        ]
                    }
                }
            }
        ]
