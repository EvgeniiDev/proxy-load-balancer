from typing import Dict, Any


class ProxyManager:
    """Утилиты для управления прокси серверами"""
    
    @staticmethod
    def get_proxy_key(proxy: Dict[str, Any]) -> str:
        """Генерация уникального ключа для прокси"""
        return f"{proxy['host']}:{proxy['port']}"
