import unittest
import time
import tempfile
import json
import os
from unittest.mock import patch, MagicMock
from proxy_load_balancer.balancer import ProxyBalancer
from proxy_load_balancer.monitor import ProxyMonitor


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
        from proxy_load_balancer.balancer import ProxyStats
        balancer.proxy_stats["old_proxy_1"] = ProxyStats()
        balancer.proxy_stats["old_proxy_1"].request_count = 100
        balancer.proxy_stats["old_proxy_1"].success_count = 80
        balancer.proxy_stats["old_proxy_1"].failure_count = 20
        
        balancer.proxy_stats["old_proxy_2"] = ProxyStats()
        balancer.proxy_stats["old_proxy_2"].request_count = 50
        balancer.proxy_stats["old_proxy_2"].success_count = 40
        balancer.proxy_stats["old_proxy_2"].failure_count = 10
        
        # Force cleanup
        balancer._cleanup_stats()
        
        # Check that old proxy stats are removed
        self.assertNotIn("old_proxy_1", balancer.proxy_stats)
        self.assertNotIn("old_proxy_2", balancer.proxy_stats)
        
    def test_session_pool_cleanup(self):
        """Test that session pools are cleaned up properly"""
        balancer = ProxyBalancer(self.config)
        
        # Add some fake session pools for non-existent proxies
        from proxy_load_balancer.balancer import ProxyStats
        mock_session1 = MagicMock()
        mock_session2 = MagicMock()
        
        balancer.proxy_stats["old_proxy_1"] = ProxyStats()
        balancer.proxy_stats["old_proxy_1"].session_pool = [mock_session1]
        balancer.proxy_stats["old_proxy_2"] = ProxyStats()
        balancer.proxy_stats["old_proxy_2"].session_pool = [mock_session2]
        
        # Force cleanup using _cleanup_stats instead
        balancer._cleanup_stats()
        
        # Check that old session pools are removed
        self.assertNotIn("old_proxy_1", balancer.proxy_stats)
        self.assertNotIn("old_proxy_2", balancer.proxy_stats)
        
    def test_stats_size_limits(self):
        """Test that stats don't exceed maximum size"""
        balancer = ProxyBalancer(self.config)
        balancer.max_stats_entries = 5  # Small limit for testing
        
        # Add more entries than the limit
        from proxy_load_balancer.balancer import ProxyStats
        for i in range(10):
            key = f"proxy_{i}"
            stats = ProxyStats()
            stats.request_count = i
            stats.success_count = i
            stats.failure_count = i
            balancer.proxy_stats[key] = stats
        
        # Force cleanup with limit enforcement
        balancer._enforce_stats_limits()
        
        # Check that size is within limit
        self.assertLessEqual(len(balancer.proxy_stats), balancer.max_stats_entries)
        
    def test_periodic_cleanup_timing(self):
        """Test that periodic cleanup only runs when interval has passed"""
        balancer = ProxyBalancer(self.config)
        balancer.cleanup_interval = 1  # 1 second for testing
        
        # First call should run cleanup
        initial_time = balancer.last_cleanup_time
        time.sleep(1.1)  # Wait longer than cleanup interval
        
        with patch.object(balancer, '_cleanup_stats') as mock_cleanup_stats:
            balancer._periodic_cleanup()
            
            # Cleanup method should be called
            mock_cleanup_stats.assert_called_once()
            
            # Time should be updated
            self.assertGreater(balancer.last_cleanup_time, initial_time)
        
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
        """Test that monitor cleans up old stats"""
        balancer = ProxyBalancer(self.config)
        monitor = ProxyMonitor(balancer)
        monitor.cleanup_interval = 1  # 1 second for testing
        
        # Add old stats
        old_time = time.time() - 1000  # Very old timestamp
        monitor.proxy_stats["old_proxy"] = {
            "last_update": old_time,
            "requests": 100
        }
        
        # Add recent stats
        recent_time = time.time()
        monitor.proxy_stats["recent_proxy"] = {
            "last_update": recent_time,
            "requests": 50
        }
        
        # Force cleanup
        monitor._cleanup_old_proxy_stats()
        
        # Check that old stats are removed but recent ones remain
        self.assertNotIn("old_proxy", monitor.proxy_stats)
        self.assertIn("recent_proxy", monitor.proxy_stats)
        
    def test_update_proxies_cleanup(self):
        """Test that updating proxies cleans up old data"""
        balancer = ProxyBalancer(self.config)
        
        # Add stats for current proxies
        from proxy_load_balancer.balancer import ProxyStats
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
