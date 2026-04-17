import yaml

class Config:
    def __init__(self, path="config/config.yaml"):
        with open(path, "r", encoding="utf-8") as f:
            self.data = yaml.safe_load(f)

    def get(self, *keys):
        data = self.data
        for k in keys:
            data = data[k]
        return data