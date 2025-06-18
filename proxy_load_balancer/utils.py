from typing import Any, Dict


class ProxyManager:
    @staticmethod
    def get_proxy_key(proxy: Dict[str, Any]) -> str:
        return f"{proxy['host']}:{proxy['port']}"
