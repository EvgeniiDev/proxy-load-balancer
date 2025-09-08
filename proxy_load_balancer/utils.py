"""
DEPRECATED: Use ProxyHandler from base.py instead.
This file is kept for backward compatibility.
"""
from .base import ProxyHandler


class ProxyManager:
    """DEPRECATED: Use ProxyHandler instead."""
    
    @staticmethod
    def get_proxy_key(proxy):
        return ProxyHandler.get_proxy_key(proxy)
