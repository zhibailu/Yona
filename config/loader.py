<<<<<<< HEAD
import yaml
import os

class Config:
    """
    配置管理类，用于加载和获取项目配置。
    支持 YAML 格式的配置文件，并提供动态更新接口。
    """
    def __init__(self, config_path="config/config.yaml"):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self):
        """
        加载配置文件内容。若文件不存在或加载失败，则抛出异常。
        """
        try:
            if not os.path.exists(self.config_path):
                raise FileNotFoundError(f"配置文件未找到: {self.config_path}")

            with open(self.config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            raise RuntimeError(f"加载配置失败: {e}")

    def get(self, section, key):
        """
        根据指定的 section 和 key 获取配置值。
        如果 section 或 key 不存在，则返回 None。
        """
        return self.config.get(section, {}).get(key)

    def update(self, section, key, value):
        """
        动态更新配置值，并保存到配置文件。
        """
        if section not in self.config:
            self.config[section] = {}
        self.config[section][key] = value
        self._save_config()

    def _save_config(self):
        """
        保存当前配置到文件。
        """
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(self.config, f, allow_unicode=True)
        except Exception as e:
=======
import yaml
import os

class Config:
    """
    配置管理类，用于加载和获取项目配置。
    支持 YAML 格式的配置文件，并提供动态更新接口。
    """
    def __init__(self, config_path="config/config.yaml"):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self):
        """
        加载配置文件内容。若文件不存在或加载失败，则抛出异常。
        """
        try:
            if not os.path.exists(self.config_path):
                raise FileNotFoundError(f"配置文件未找到: {self.config_path}")

            with open(self.config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            raise RuntimeError(f"加载配置失败: {e}")

    def get(self, section, key):
        """
        根据指定的 section 和 key 获取配置值。
        如果 section 或 key 不存在，则返回 None。
        """
        return self.config.get(section, {}).get(key)

    def update(self, section, key, value):
        """
        动态更新配置值，并保存到配置文件。
        """
        if section not in self.config:
            self.config[section] = {}
        self.config[section][key] = value
        self._save_config()

    def _save_config(self):
        """
        保存当前配置到文件。
        """
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(self.config, f, allow_unicode=True)
        except Exception as e:
>>>>>>> 6639851a3a6813774391e09f907b7401da2316bd
            raise RuntimeError(f"保存配置失败: {e}")