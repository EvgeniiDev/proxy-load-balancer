import time
import unittest
import json
from concurrent.futures import ThreadPoolExecutor
from base_test import BaseLoadBalancerTest


class TestSystemIntegration(BaseLoadBalancerTest):
    def test_startup_and_balancing(self):
        servers = self.server_manager.create_servers(4)
        proxies = [{"host": "127.0.0.1", "port": server.port} for server in servers]
        config_path = self.create_test_config(proxies=proxies, algorithm="round_robin", health_check_interval=1)
        balancer = self.start_balancer_with_config(config_path)
        balancer_port = balancer.config['server']['port']
        successful_requests = 0
        for _ in range(12):
            try:
                response = self.make_request_through_proxy(balancer_port=balancer_port, target_url="http://httpbin.org/status/200", timeout=10)
                if response and response.status_code == 200:
                    successful_requests += 1
            except:
                pass
        # Test that most initial requests are successful
        self.assertGreater(successful_requests, 5)
        stats = self.server_manager.get_server_stats()
        # Check that at least two servers are receiving traffic
        active_servers = sum(1 for count in stats.values() if count > 0)
        self.assertGreaterEqual(active_servers, 2)

    def test_failover(self):
        servers = self.server_manager.create_servers(4)
        proxies = [{"host": "127.0.0.1", "port": server.port} for server in servers]
        config_path = self.create_test_config(proxies=proxies, algorithm="round_robin", health_check_interval=1)
        balancer = self.start_balancer_with_config(config_path)
        balancer_port = balancer.config['server']['port']
        for _ in range(6):
            try:
                self.make_request_through_proxy(balancer_port=balancer_port, target_url="http://httpbin.org/status/200", timeout=10)
            except:
                pass
        # Stop two servers to test failover
        self.server_manager.mark_server_stopped(servers[0])
        servers[0].stop()
        self.server_manager.mark_server_stopped(servers[1])
        servers[1].stop()
        time.sleep(2)
        # Reset stats after server stoppage
        self.server_manager.reset_all_stats()
        successful_requests = 0
        for _ in range(8):
            try:
                response = self.make_request_through_proxy(balancer_port=balancer_port, target_url="http://httpbin.org/status/200", timeout=10)
                if response and response.status_code == 200:
                    successful_requests += 1
            except:
                pass
        # Test that requests are still successful after servers are down
        self.assertGreater(successful_requests, 4)
        # Get final stats
        final_stats = self.server_manager.get_server_stats()
        # Test that the stopped servers don't receive requests
        # and the remaining servers do
        self.assertEqual(final_stats[servers[0].port], 0)
        self.assertEqual(final_stats[servers[1].port], 0)
        active_remaining = sum(1 for i, server in enumerate(servers) if i >= 2 and final_stats[server.port] > 0)
        self.assertGreaterEqual(active_remaining, 1)

    def test_dynamic_config_reload(self):
        servers = self.server_manager.create_servers(2)
        proxies = [{"host": "127.0.0.1", "port": server.port} for server in servers]
        config_path = self.create_test_config(proxies=proxies, algorithm="round_robin", health_check_interval=1)
        balancer = self.start_balancer_with_config(config_path)
        balancer_port = balancer.config['server']['port']
        # Make initial requests
        successful_initial = 0
        for _ in range(6):
            try:
                response = self.make_request_through_proxy(balancer_port=balancer_port, target_url="http://httpbin.org/status/200", timeout=10)
                if response and response.status_code == 200:
                    successful_initial += 1
            except:
                pass
        # Create additional servers
        new_servers = self.server_manager.create_servers(2)
        all_servers = servers + new_servers
        updated_proxies = [{"host": "127.0.0.1", "port": server.port} for server in all_servers]
        # Update config with new servers
        with open(config_path, 'w') as f:
            json.dump({"proxies": updated_proxies, "algorithm": "round_robin", "server": {"port": balancer_port}, "health_check_interval": 1, "connection_timeout": 10, "max_retries": 3}, f, indent=2)
        time.sleep(3)
        # Reset stats to clearly see what happens after reload
        self.server_manager.reset_all_stats()
        # Make requests after config update
        successful_after = 0
        for _ in range(12):
            try:
                response = self.make_request_through_proxy(balancer_port=balancer_port, target_url="http://httpbin.org/status/200", timeout=10)
                if response and response.status_code == 200:
                    successful_after += 1
            except:
                pass
        self.assertGreater(successful_after, 5)
        final_stats = self.server_manager.get_server_stats()
        # Verify that at least one server is working
        active_servers = sum(1 for count in final_stats.values() if count > 0)
        self.assertGreaterEqual(active_servers, 1)

    def test_health_monitoring_recovery(self):
        servers = self.server_manager.create_servers(3)
        proxies = [{"host": "127.0.0.1", "port": server.port} for server in servers]
        config_path = self.create_test_config(proxies=proxies, algorithm="round_robin", health_check_interval=1)
        balancer = self.start_balancer_with_config(config_path)
        balancer_port = balancer.config['server']['port']
        servers[1].stop()
        self.server_manager.mark_server_stopped(servers[1])
        time.sleep(3)
        for _ in range(10):
            try:
                self.make_request_through_proxy(balancer_port=balancer_port, target_url="http://httpbin.org/status/200", timeout=10)
            except:
                pass
        stats_during_failure = self.server_manager.get_server_stats()
        self.assertEqual(stats_during_failure[servers[1].port], 0)
        servers[1].restart()
        if servers[1] not in self.server_manager.servers:
            self.server_manager.servers.append(servers[1])
        if servers[1].port in self.server_manager.stopped_ports:
            self.server_manager.stopped_ports.remove(servers[1].port)
        time.sleep(2)
        self.server_manager.reset_all_stats()
        for _ in range(10):
            try:
                self.make_request_through_proxy(balancer_port=balancer_port, target_url="http://httpbin.org/status/200", timeout=10)
            except:
                pass
        stats_after_recovery = self.server_manager.get_server_stats()
        self.assertGreater(stats_after_recovery[servers[1].port], 0)

    def test_round_robin_balancing_integration(self):
        servers = self.server_manager.create_servers(3)
        proxies = [{"host": "127.0.0.1", "port": server.port} for server in servers]
        config_path = self.create_test_config(proxies=proxies, algorithm="round_robin", health_check_interval=1)
        balancer = self.start_balancer_with_config(config_path)
        balancer_port = balancer.config['server']['port']
        successful_requests = 0
        for _ in range(18):
            try:
                response = self.make_request_through_proxy(balancer_port=balancer_port, target_url="http://httpbin.org/status/200", timeout=10)
                if response and response.status_code == 200:
                    successful_requests += 1
            except:
                pass
        self.assertGreater(successful_requests, 8)
        stats = self.server_manager.get_server_stats()
        active_servers = sum(1 for count in stats.values() if count > 0)
        self.assertGreaterEqual(active_servers, 2)

    def test_concurrent_load_handling(self):
        servers = self.server_manager.create_servers(3)
        proxies = [{"host": "127.0.0.1", "port": server.port} for server in servers]
        config_path = self.create_test_config(proxies=proxies, algorithm="round_robin", health_check_interval=1)
        balancer = self.start_balancer_with_config(config_path)
        balancer_port = balancer.config['server']['port']
        def make_concurrent_request():
            try:
                response = self.make_request_through_proxy(balancer_port=balancer_port, target_url="http://httpbin.org/delay/1", timeout=15)
                return response is not None and response.status_code == 200
            except:
                return False
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_concurrent_request) for _ in range(20)]
            results = [future.result() for future in futures]
        successful_concurrent = sum(results)
        self.assertGreater(successful_concurrent, 5)
        stats = self.server_manager.get_server_stats()
        active_servers = sum(1 for count in stats.values() if count > 0)
        self.assertGreaterEqual(active_servers, 1)


if __name__ == '__main__':
    unittest.main()
