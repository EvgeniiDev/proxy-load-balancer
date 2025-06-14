import threading
import time
from typing import Any, Dict
from .balancer import ProxyBalancer


class ProxyMonitor:
    def __init__(self, balancer: ProxyBalancer):
        self.balancer = balancer
        self.is_monitoring = False
        self.monitor_thread = None

    def start_monitoring(self):
        if self.is_monitoring:
            return

        self.is_monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def stop_monitoring(self):
        self.is_monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)

    def _monitor_loop(self):
        while self.is_monitoring:
            try:
                self._collect_stats()
                time.sleep(10)
            except Exception:
                pass

    def _collect_stats(self):
        pass

    def get_monitoring_report(self) -> Dict[str, Any]:
        balancer_stats = self.balancer.get_stats()

        return {"timestamp": time.time(), "balancer": balancer_stats, "proxies": []}
