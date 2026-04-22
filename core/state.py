<<<<<<< HEAD
from config.loader import Config

class State:
    """
    State 类用于管理项目的动态状态和工具配置。
    """
    def __init__(self, now_states=None, delta_tools=None):
        """
        初始化 State 类。
        参数：
            - now_states: 当前状态信息（可选）。
            - delta_tools: 工具配置列表（可选）。
        """
        config = Config()
        self.now_states = now_states or config.get("state", "now_states")
        self.delta_tools = delta_tools or self._initialize_delta_tools(config)

    def _initialize_delta_tools(self, config):
        """
        初始化工具配置。
        参数：
            - config: 配置对象。
        返回：
            - 工具配置列表。
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": config.get("state", "delta_tools_name"),
                    "description": "计算对话风格的变化量",
                    "parameters": {
                        "type": "object",
                        "properties": config.get("state", "delta_tools_properties"),
                        "required": [
                            "assertiveness_delta",
                            "uncertainty_delta",
                            "warmth_delta"
                        ]
                    }
                }
            }
        ]

    def update_state(self, key, value):
        """
        动态更新状态信息。
        参数：
            - key: 状态键。
            - value: 状态值。
        """
        self.now_states[key] = value

    def add_tool(self, tool_config):
        """
        动态添加工具配置。
        参数：
            - tool_config: 工具配置。
        """
=======
from config.loader import Config

class State:
    """
    State 类用于管理项目的动态状态和工具配置。
    """
    def __init__(self, now_states=None, delta_tools=None):
        """
        初始化 State 类。
        参数：
            - now_states: 当前状态信息（可选）。
            - delta_tools: 工具配置列表（可选）。
        """
        config = Config()
        self.now_states = now_states or config.get("state", "now_states")
        self.delta_tools = delta_tools or self._initialize_delta_tools(config)

    def _initialize_delta_tools(self, config):
        """
        初始化工具配置。
        参数：
            - config: 配置对象。
        返回：
            - 工具配置列表。
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": config.get("state", "delta_tools_name"),
                    "description": "计算对话风格的变化量",
                    "parameters": {
                        "type": "object",
                        "properties": config.get("state", "delta_tools_properties"),
                        "required": [
                            "assertiveness_delta",
                            "uncertainty_delta",
                            "warmth_delta"
                        ]
                    }
                }
            }
        ]

    def update_state(self, key, value):
        """
        动态更新状态信息。
        参数：
            - key: 状态键。
            - value: 状态值。
        """
        self.now_states[key] = value

    def add_tool(self, tool_config):
        """
        动态添加工具配置。
        参数：
            - tool_config: 工具配置。
        """
>>>>>>> 6639851a3a6813774391e09f907b7401da2316bd
        self.delta_tools.append(tool_config)