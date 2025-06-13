from http.server import ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .balancer import ProxyBalancer


class ProxyBalancerServer(ThreadingHTTPServer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.proxy_balancer: Optional["ProxyBalancer"] = None
        self.allow_reuse_address = True
