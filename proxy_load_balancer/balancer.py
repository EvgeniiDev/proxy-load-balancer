import asyncio
import aiohttp
import logging
import time
from typing import Any, Dict, List, Optional, Set

from .proxy_selector_algo import AlgorithmFactory, LoadBalancingAlgorithm
from .server import ProxyBalancerServer
from .utils import ProxyManager
from .performance import PerformanceMonitor, create_performance_middleware, start_performance_monitoring


class ProxyStatsManager:
    def __init__(self):
        self.success_count = 0
        self.failure_count = 0
        self.last_success_time = 0
        self.last_failure_time = 0
        self.is_healthy = True
    
    def mark_success(self):
        self.success_count += 1
        self.last_success_time = time.time()
        self.is_healthy = True
    
    def mark_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        # Mark unhealthy if failure rate is too high
        if self.failure_count > 5:
            self.is_healthy = False
    
    def get_health_status(self):
        return {
            'success_count': self.success_count,
            'failure_count': self.failure_count,
            'is_healthy': self.is_healthy,
            'last_success_time': self.last_success_time,
            'last_failure_time': self.last_failure_time
        }


class ProxyBalancer:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.proxies = config.get("proxies", [])
        
        # Initialize algorithm
        algorithm_name = config.get("load_balancing_algorithm", "round_robin")
        self.algorithm = AlgorithmFactory.create_algorithm(algorithm_name)
        
        # Performance settings
        perf_config = config.get("performance", {})
        self.max_connections = perf_config.get("max_concurrent_connections", 1000)
        self.connection_timeout = config.get("connection_timeout", 10)
        self.read_timeout = config.get("read_timeout", 30)
        self.request_timeout = config.get("request_timeout", 60)
        
        # Proxy management with optimized data structures
        self.proxy_managers: Dict[str, ProxyStatsManager] = {}
        self._healthy_proxies_cache = []
        self._cache_expiry = 0
        self._cache_ttl = 1.0  # Cache healthy proxies for 1 second
        self._initialize_proxies()
        
        # Performance monitoring
        self.performance_monitor = PerformanceMonitor()
        
        # Server with performance config
        server_config = config.get("server", {})
        self.host = server_config.get("host", "127.0.0.1")
        self.port = server_config.get("port", 8080)
        self.server = ProxyBalancerServer(self.host, self.port, config)
        self.server.set_proxy_balancer(self)
        
        # Add performance middleware
        perf_middleware = create_performance_middleware(self.performance_monitor)
        self.server.app.middlewares.append(perf_middleware)
        
        # Config manager
        self.config_manager = None
        self.config_change_callback = None
        
        # Monitoring
        self.monitor_task = None
        self.cleanup_task = None
        
        self.logger = logging.getLogger("proxy_balancer")
        
        # Server runner
        self._runner = None
        self._running = False
    
    def _initialize_proxies(self):
        for proxy in self.proxies:
            key = ProxyManager.get_proxy_key(proxy)
            self.proxy_managers[key] = ProxyStatsManager()
    
    def get_next_proxy(self) -> Optional[Dict[str, Any]]:
        # Use cached healthy proxies for better performance
        current_time = time.time()
        if current_time > self._cache_expiry:
            self._healthy_proxies_cache = [
                proxy for proxy in self.proxies 
                if self._is_proxy_healthy(proxy)
            ]
            self._cache_expiry = current_time + self._cache_ttl
        
        # Get proxy from healthy list
        healthy_proxies = self._healthy_proxies_cache if self._healthy_proxies_cache else self.proxies
        return self.algorithm.select_proxy(healthy_proxies)
    
    def _is_proxy_healthy(self, proxy: Dict[str, Any]) -> bool:
        proxy_key = ProxyManager.get_proxy_key(proxy)
        if proxy_key in self.proxy_managers:
            return self.proxy_managers[proxy_key].is_healthy
        return True
    
    def mark_success(self, proxy: Dict[str, Any]):
        proxy_key = ProxyManager.get_proxy_key(proxy)
        if proxy_key in self.proxy_managers:
            self.proxy_managers[proxy_key].mark_success()
            # Add to healthy cache if not already present
            if proxy not in self._healthy_proxies_cache:
                self._healthy_proxies_cache.append(proxy)
    
    def mark_failure(self, proxy: Dict[str, Any]):
        proxy_key = ProxyManager.get_proxy_key(proxy)
        if proxy_key in self.proxy_managers:
            self.proxy_managers[proxy_key].mark_failure()
            # Remove from healthy cache if unhealthy
            if not self.proxy_managers[proxy_key].is_healthy and proxy in self._healthy_proxies_cache:
                self._healthy_proxies_cache.remove(proxy)
    
    def update_proxies(self, new_config: Dict[str, Any]):
        self.config = new_config
        old_proxies = self.proxies
        self.proxies = new_config.get("proxies", [])
        
        # Update algorithm with new proxies
        algorithm_name = new_config.get("load_balancing_algorithm", "round_robin")
        self.algorithm = AlgorithmFactory.create_algorithm(algorithm_name)
        
        # Clean up old proxy managers and sessions
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._cleanup_old_proxies(old_proxies))
        except RuntimeError:
            # No running event loop, skip cleanup
            pass
        
        # Initialize new proxy managers
        self._initialize_proxies()
        
        self.logger.info(f"Updated proxies: {len(self.proxies)} proxies loaded")
    
    async def _cleanup_old_proxies(self, old_proxies: List[Dict[str, Any]]):
        old_keys = {ProxyManager.get_proxy_key(proxy) for proxy in old_proxies}
        new_keys = {ProxyManager.get_proxy_key(proxy) for proxy in self.proxies}
        
        keys_to_remove = old_keys - new_keys
        for key in keys_to_remove:
            # Remove proxy manager
            if key in self.proxy_managers:
                del self.proxy_managers[key]
    
    def reload_algorithm(self):
        algorithm_name = self.config.get("load_balancing_algorithm", "round_robin")
        self.algorithm = AlgorithmFactory.create_algorithm(algorithm_name)
        self.logger.info(f"Reloaded algorithm: {algorithm_name}")
    
    def set_config_manager(self, config_manager, callback):
        self.config_manager = config_manager
        self.config_change_callback = callback
    
    async def _monitor_proxies(self):
        while self._running:
            try:
                # Monitor proxy health
                await asyncio.sleep(60)  # Check every minute
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in proxy monitoring: {e}")
                await asyncio.sleep(5)
    
    async def start(self):
        self._running = True
        
        # Start the server
        self._runner = await self.server.start()
        
        # Start monitoring tasks
        self.monitor_task = asyncio.create_task(self._monitor_proxies())
        
        # Start performance monitoring
        monitor_interval = self.config.get('stats_log_interval', 60)
        self.performance_task = asyncio.create_task(
            start_performance_monitoring(self.performance_monitor, monitor_interval)
        )
        
        self.logger.info(f"Proxy balancer started on {self.host}:{self.port}")
        self.logger.info(f"Loaded {len(self.proxies)} proxies")
        print(f"✅ Proxy balancer server started successfully on {self.host}:{self.port}")
        
        return self._runner
    
    async def stop(self):
        self._running = False
        
        # Cancel monitoring tasks
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        
        # Cancel performance monitoring
        if hasattr(self, 'performance_task') and self.performance_task:
            self.performance_task.cancel()
            try:
                await self.performance_task
            except asyncio.CancelledError:
                pass
        
        # Log final performance stats
        final_stats = self.performance_monitor.get_stats()
        self.logger.info(f"Final stats: {final_stats['total_requests']} requests processed, "
                        f"avg response time: {final_stats['response_time'].get('avg', 0):.3f}s")
        
        # Close all sessions
        # Nothing to close as we're not managing sessions now
        
        # Stop the server
        if self._runner:
            await self.server.stop(self._runner)
        
        self.logger.info("Proxy balancer stopped")
