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
        # Reduce max workers significantly to prevent thread explosion
        self._thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=250,
            thread_name_prefix="ProxyWorker"
        )
        self._shutdown_event = threading.Event()
    
    def _ensure_thread_pool(self):
        """Ensure thread pool is initialized."""
        if not hasattr(self, '_thread_pool') or self._thread_pool is None:
            self._thread_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=250,
                thread_name_prefix="ProxyWorker"
            )
    
    def process_request(self, request, client_address):
        if self._shutdown_event.is_set():
            self.close_request(request)
            return
            
        self._ensure_thread_pool()
        try:
            future = self._thread_pool.submit(self.process_request_thread, request, client_address)
            # Don't wait for completion, but handle exceptions properly
            future.add_done_callback(self._handle_request_completion)
        except Exception:
            # If we can't submit to thread pool, close the request
            self.close_request(request)
    
    def _handle_request_completion(self, future):
        """Handle completion or failure of request processing."""
        try:
            future.result()  # This will raise any exception that occurred
        except Exception:
            pass  # Log if needed, but don't let exceptions propagate
    
    def shutdown(self):
        """Properly shutdown the server."""
        self._shutdown_event.set()
        super().shutdown()
    
    def server_close(self):
        """Properly close the server and thread pool."""
        self._shutdown_event.set()
        if hasattr(self, '_thread_pool') and self._thread_pool is not None:
            # Shutdown and wait for threads to complete with timeout
            self._thread_pool.shutdown(wait=True, timeout=10)
            self._thread_pool = None
        super().server_close()

class ProxyBalancerServer(ThreadPoolMixin, HTTPServer):
    def __init__(self, server_address: Tuple[str, int], RequestHandlerClass, **kwargs):
        self.proxy_balancer: Optional["ProxyBalancer"] = None
        self.allow_reuse_address = True
        # Remove daemon_threads to ensure proper cleanup
        super().__init__(server_address, RequestHandlerClass, **kwargs)
