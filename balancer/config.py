import json
from typing import Any, Dict


def load_config(config_file: str = "config.json") -> Dict[str, Any]:
    """Загрузка конфигурационного файла"""
    with open(config_file, "r") as f:
        return json.load(f)
