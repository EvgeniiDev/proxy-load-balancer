import concurrent.futures
import socketserver
import threading
from http.server import HTTPServer
from typing import TYPE_CHECKING, Any, Optional, Tuple
if TYPE_CHECKING:
    from .proxy_balancer import ProxyBalancer

class ThreadPoolMixin(socketserver.ThreadingMixIn):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=500,
            thread_name_prefix="proxy_server_worker"
        )
    
    def _ensure_thread_pool(self):
        """Ensure thread pool is initialized."""
        if not hasattr(self, '_thread_pool') or self._thread_pool is None:
            self._thread_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=500,
                thread_name_prefix="proxy_server_worker"
            )
    
    def process_request(self, request, client_address):
        self._ensure_thread_pool()
        self._thread_pool.submit(self.process_request_thread, request, client_address)
    
    def server_close(self):
        if hasattr(self, '_thread_pool') and self._thread_pool is not None:
            self._thread_pool.shutdown(wait=False)
        super().server_close()

class ProxyBalancerServer(ThreadPoolMixin, HTTPServer):
    def __init__(self, server_address: Tuple[str, int], RequestHandlerClass, **kwargs):
        self.proxy_balancer: Optional["ProxyBalancer"] = None
        self.allow_reuse_address = True
        self.daemon_threads = True
        super().__init__(server_address, RequestHandlerClass, **kwargs)
