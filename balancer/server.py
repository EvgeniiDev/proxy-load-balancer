from http.server import ThreadingHTTPServer
from typing import Optional, TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .balancer import ProxyBalancer


class ProxyBalancerServer(ThreadingHTTPServer):
    """Многопоточный HTTP сервер для прокси-балансировщика"""
    
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.proxy_balancer = None
        self.allow_reuse_address = True
