import time
import unittest
import json
from base_test import BaseLoadBalancerTest


class TestProxyLoadBalancerIntegration(BaseLoadBalancerTest):
    """Комплексные интеграционные тесты системы балансировки прокси"""
    
    def test_complete_system_stress(self):
        """Стресс-тест полной системы с множественными запросами"""
        
        servers = self.server_manager.create_servers(4)
        
        proxies = [
            {"host": "127.0.0.1", "port": server.port} 
            for server in servers
        ]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="random",
            health_check_interval=5
        )
        
        balancer = self.start_balancer_with_config(config_path)
        balancer_port = balancer.config['server']['port']
        
        for i in range(20):
            try:
                self.make_request_through_proxy(
                    balancer_port=balancer_port,
                    target_url="http://httpbin.org/status/200",
                    timeout=5
                )
            except:
                pass
        
        stats = self.server_manager.get_server_stats()
        
        for server in servers:
            self.assertGreater(stats.get(server.port, 0), 0)
            
        print(f"Stats after stress test: {stats}")
        self.assertGreaterEqual(sum(stats.values()), 20)
