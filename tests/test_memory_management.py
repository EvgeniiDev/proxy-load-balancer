import unittest
import time
import tempfile
import json
import os
from unittest.mock import patch, MagicMock
from proxy_load_balancer.proxy_balancer import ProxyBalancer
from proxy_load_balancer.proxy_stats import ProxyStats
from proxy_load_balancer.stats_reporter import StatsReporter
from proxy_load_balancer.base import ProxyHandler


class TestMemoryManagement(unittest.TestCase):
    
    def setUp(self):
        self.config = {
            "server": {"host": "127.0.0.1", "port": 8080},
            "proxies": [
                {"host": "127.0.0.1", "port": 1080},
                {"host": "127.0.0.1", "port": 1081}
            ],
            "load_balancing_algorithm": "random",
            "health_check_interval": 30,
            "connection_timeout": 5,
            "max_retries": 3
        }
        
    def test_stats_cleanup(self):
        """Test that statistics are cleaned up properly"""
        balancer = ProxyBalancer(self.config)
        
        # Add some fake statistics for non-existent proxies
        # from proxy_load_balancer.proxy_stats import ProxyStats
        balancer.proxy_stats["old_proxy_1"] = ProxyStats()
        balancer.proxy_stats["old_proxy_1"].request_count = 100
        balancer.proxy_stats["old_proxy_1"].success_count = 80
        balancer.proxy_stats["old_proxy_1"].failure_count = 20
        
        balancer.proxy_stats["old_proxy_2"] = ProxyStats()
        balancer.proxy_stats["old_proxy_2"].request_count = 50
        balancer.proxy_stats["old_proxy_2"].success_count = 40
        balancer.proxy_stats["old_proxy_2"].failure_count = 10
        
        # Cleanup with current proxy keys (which don't include old proxies)
        current_proxy_keys = set(ProxyHandler.get_proxy_key(proxy) for proxy in balancer.available_proxies)
        balancer._cleanup_old_proxy_data(current_proxy_keys)
        
        # Check that old proxy stats are removed
        self.assertNotIn("old_proxy_1", balancer.proxy_stats)
        self.assertNotIn("old_proxy_2", balancer.proxy_stats)
        
    def test_session_pool_cleanup(self):
        """Test that session pools are cleaned up when proxies are removed"""
        balancer = ProxyBalancer(self.config)
        
        # Add some fake session pools for non-existent proxies
        # from proxy_load_balancer.proxy_stats import ProxyStats
        mock_session1 = MagicMock()
        mock_session2 = MagicMock()
        
        balancer.proxy_stats["old_proxy_1"] = ProxyStats()
        balancer.proxy_stats["old_proxy_1"].session_pool = [mock_session1]
        balancer.proxy_stats["old_proxy_2"] = ProxyStats()
        balancer.proxy_stats["old_proxy_2"].session_pool = [mock_session2]
        
        # Simulate proxy update with only current proxies
        current_proxy_keys = {"127.0.0.1:1080", "127.0.0.1:1081"}
        balancer._cleanup_old_proxy_data(current_proxy_keys)
        
        # Check that old session pools are removed
        self.assertNotIn("old_proxy_1", balancer.proxy_stats)
        self.assertNotIn("old_proxy_2", balancer.proxy_stats)
        
        # Verify sessions were closed
        mock_session1.close.assert_called_once()
        mock_session2.close.assert_called_once()
        
    def test_session_pool_size_limit(self):
        """Test that session pool size is limited"""
        balancer = ProxyBalancer(self.config)
        balancer.max_session_pool_size = 2  # Small limit for testing
        
        proxy = {"host": "127.0.0.1", "port": 1080}
        
        # Add sessions up to the limit
        mock_sessions = []
        for i in range(5):  # Try to add more than limit
            mock_session = MagicMock()
            mock_sessions.append(mock_session)
            balancer.return_session(proxy, mock_session)
        
        # Check that only max_session_pool_size sessions are kept
        proxy_key = "127.0.0.1:1080"
        stats = balancer.proxy_stats[proxy_key]
        self.assertLessEqual(len(stats.session_pool), balancer.max_session_pool_size)
        
        # Check that excess sessions were closed
        closed_sessions = sum(1 for session in mock_sessions if session.close.called)
        self.assertGreater(closed_sessions, 0)
        
    def test_monitor_cleanup(self):
        """Test that stats reporter cleans up old stats"""
        balancer = ProxyBalancer(self.config)
        stats_reporter = balancer.stats_reporter
        stats_reporter.cleanup_interval = 1  # 1 second for testing
        
        # Add old stats
        old_time = time.time() - 1000  # Very old timestamp
        stats_reporter.proxy_stats["old_proxy"] = {
            "last_update": old_time,
            "requests": 100
        }
        
        # Add recent stats
        recent_time = time.time()
        stats_reporter.proxy_stats["recent_proxy"] = {
            "last_update": recent_time,
            "requests": 50
        }
        
        # Force cleanup
        stats_reporter._cleanup_old_proxy_stats()
        
        # Check that old stats are removed but recent ones remain
        self.assertNotIn("old_proxy", stats_reporter.proxy_stats)
        self.assertIn("recent_proxy", stats_reporter.proxy_stats)
        
    def test_update_proxies_cleanup(self):
        """Test that updating proxies cleans up old data"""
        balancer = ProxyBalancer(self.config)
        
        # Add stats for current proxies
        # from proxy_load_balancer.proxy_stats import ProxyStats
        stats_current = ProxyStats()
        stats_current.request_count = 100
        stats_current.success_count = 80
        stats_current.failure_count = 20
        balancer.proxy_stats["127.0.0.1:1080"] = stats_current
        
        # Add stats for old proxy that will be removed
        stats_old = ProxyStats()
        stats_old.request_count = 50
        stats_old.success_count = 40
        stats_old.failure_count = 10
        balancer.proxy_stats["old.proxy:9999"] = stats_old
        
        # Update config with only one proxy
        new_config = {
            **self.config,
            "proxies": [{"host": "127.0.0.1", "port": 1080}]
        }
        
        balancer.update_proxies(new_config)
        
        # Check that stats for removed proxy are cleaned up
        self.assertNotIn("old.proxy:9999", balancer.proxy_stats)
        
        # Check that stats for current proxy remain
        self.assertIn("127.0.0.1:1080", balancer.proxy_stats)


if __name__ == '__main__':
    unittest.main()
