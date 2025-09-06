#!/usr/bin/env python3
import sys
import os
import subprocess
import time
import threading
import requests
import json
import random
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from proxy_load_balancer.config import ConfigManager
from proxy_load_balancer.proxy_balancer import ProxyBalancer

TOR_NODES = int(os.getenv("TOR_NODES", "35"))
TEST_REQUESTS = int(os.getenv("TEST_REQUESTS", "300"))

class TorManager:
    def __init__(self):
        self.tor_processes = []
        self.scripts_dir = os.path.dirname(os.path.abspath(__file__))
        self.testing_dir = os.path.dirname(self.scripts_dir)
        self.nodes_count = TOR_NODES

    def cleanup_existing_processes(self):
        print("🛑 Stopping existing Tor processes...")
        try:
            subprocess.run(["pkill", "-f", "tor"], capture_output=True, timeout=10)
            time.sleep(1)
        except subprocess.TimeoutExpired:
            pass

    def create_data_directories(self):
        print("📁 Creating data directories...")
        tor_data_dir = os.path.join(self.testing_dir, "tor-data")
        os.makedirs(tor_data_dir, exist_ok=True)
        for i in range(self.nodes_count):
            node_dir = os.path.join(tor_data_dir, f"tor-{i}")
            os.makedirs(node_dir, exist_ok=True)

    def start_all_tor_instances(self):
        print("🚀 Starting all Tor instances...")
        self.cleanup_existing_processes()
        self.create_data_directories()
        config_files = []
        tor_configs_dir = os.path.join(self.testing_dir, "tor_configs")
        for i in range(self.nodes_count):
            cfg = os.path.join(tor_configs_dir, f"torrc-{i}")
            if os.path.exists(cfg):
                config_files.append((str(i), cfg))
        successful_starts = 0
        for node_num, config_path in config_files:
            try:
                temp_config_path = os.path.join(self.testing_dir, f"temp_torrc_{node_num}.tmp")
                data_dir = os.path.join(self.testing_dir, "tor-data", f"tor-{node_num}")
                log_file = os.path.join(self.testing_dir, "tor-data", f"tor-{node_num}.log")
                with open(config_path, 'r') as f:
                    original_config = f.read()
                modified_config = original_config.replace(f"../tor-data/tor-{node_num}", data_dir).replace(f"../tor-data/tor-{node_num}.log", log_file)
                with open(temp_config_path, 'w') as f:
                    f.write(modified_config)
                p = subprocess.Popen(["tor", "-f", temp_config_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.tor_processes.append(p)
                successful_starts += 1
                print(f"✅ Started Tor node {node_num}")
                time.sleep(0.2)
            except Exception as e:
                print(f"❌ Failed to start Tor node {node_num}: {e}")
        print(f"🎯 Started {successful_starts}/{len(config_files)} Tor instances")
        time.sleep(5)
        try:
            result = subprocess.run(["netstat", "-tuln"], capture_output=True, text=True, timeout=5)
            listening = any(":90" in line and "LISTEN" in line for line in result.stdout.splitlines())
            return listening or len(self.tor_processes) > 0
        except Exception:
            return len(self.tor_processes) > 0

    def stop_all_tor_instances(self):
        print("🛑 Stopping Tor instances...")
        for p in self.tor_processes:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        try:
            subprocess.run(["pkill", "-f", "tor"], capture_output=True, timeout=10)
        except Exception:
            pass
        try:
            for f in os.listdir(self.testing_dir):
                if f.startswith("temp_torrc_") and f.endswith(".tmp"):
                    os.remove(os.path.join(self.testing_dir, f))
        except Exception:
            pass

class LoadBalancerTester:
    REQUESTS_PER_MINUTE = 10

    def __init__(self):
        self.balancer = None
        self.balancer_thread = None
        self.testing_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.nodes_count = TOR_NODES
        self.temp_config_path = None
        self.repo_root = os.path.dirname(self.testing_dir)
        self.config_path = os.getenv("CONFIG_PATH", os.path.join(self.repo_root, "config.json"))
        self.target_urls = [
            "https://steamcommunity.com/market/search?appid=730&q=AK-47",
            "https://steamcommunity.com/market/search?appid=730&q=M4A4",
            "https://steamcommunity.com/market/search?appid=730&q=M4A1-S",
            "https://steamcommunity.com/market/search?appid=730&q=AWP",
            "https://steamcommunity.com/market/search?appid=730&q=Glock-18",
            "https://steamcommunity.com/market/search?appid=730&q=USP-S",
            "https://steamcommunity.com/market/search?appid=730&q=Desert+Eagle",
            "https://steamcommunity.com/market/search?appid=730&q=P250",
            "https://steamcommunity.com/market/search?appid=730&q=Five-SeveN",
            "https://steamcommunity.com/market/search?appid=730&q=Tec-9",
            "https://steamcommunity.com/market/search?appid=730&q=UMP-45",
            "https://steamcommunity.com/market/search?appid=730&q=MP9",
            "https://steamcommunity.com/market/search?appid=730&q=MAC-10",
            "https://steamcommunity.com/market/search?appid=730&q=P90",
            "https://steamcommunity.com/market/search?appid=730&q=Galil+AR",
            "https://steamcommunity.com/market/search?appid=730&q=FAMAS",
            "https://steamcommunity.com/market/search?appid=730&q=SG+553",
            "https://steamcommunity.com/market/search?appid=730&q=AUG",
            "https://steamcommunity.com/market/search?appid=730&q=SSG+08",
            "https://steamcommunity.com/market/search?appid=730&q=Knife",
        ]

    def start_balancer(self):
        print("🚀 Starting load balancer...")
        if not os.path.exists(self.config_path):
            print(f"❌ Config file not found: {self.config_path}")
            return False
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            print(f"❌ Failed to read config file: {e}")
            return False
        proxies = [{"host": "127.0.0.1", "port": 9050 + i, "type": "socks5"} for i in range(self.nodes_count)]
        config["proxies"] = proxies
        configs_dir = os.path.join(self.testing_dir, "configs")
        os.makedirs(configs_dir, exist_ok=True)
        self.temp_config_path = os.path.join(configs_dir, "generated_small_config.json")
        try:
            with open(self.temp_config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"❌ Failed to write temp config: {e}")
            return False
        config_path = self.temp_config_path
        try:
            config_manager = ConfigManager(config_path)
            cfg = config_manager.get_config()
            self.balancer = ProxyBalancer(cfg, verbose=True)
            def on_config_change(new_cfg):
                try:
                    self.balancer.update_proxies(new_cfg)
                    self.balancer.reload_algorithm()
                except Exception as e:
                    print(f"⚠️ Config reload error: {e}")
            self.balancer.set_config_manager(config_manager, on_config_change)
            config_manager.add_change_callback(on_config_change)
            config_manager.start_monitoring()
            def run_balancer():
                try:
                    self.balancer.start()
                except Exception as e:
                    print(f"❌ Balancer error: {e}")
            self.balancer_thread = threading.Thread(target=run_balancer, daemon=True)
            self.balancer_thread.start()
            time.sleep(5)
            try:
                test_url = "https://steamcommunity.com/market/"
                r = requests.get(test_url, proxies={"http": "http://localhost:8080", "https": "http://localhost:8080"}, timeout=10, verify=False)
                print(f"✅ Load balancer responded: HTTP {r.status_code}")
            except Exception as e:
                print(f"⚠️ Load balancer test failed: {e}")
            return True
        except Exception as e:
            print(f"❌ Failed to start load balancer: {e}")
            return False

    def run_comprehensive_test(self, num_requests=TEST_REQUESTS):
        print(f"\n📊 Starting comprehensive test with {num_requests} requests...")
        print(f"🕒 Request rate limit: {self.REQUESTS_PER_MINUTE} requests per minute")
        print("=" * 70)
        delay_between_requests = 60.0 / self.REQUESTS_PER_MINUTE
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        results = []
        success_count = 0
        error_429_count = 0
        other_errors = 0
        connection_errors = 0
        def make_request(request_id):
            try:
                url = random.choice(self.target_urls)
                t0 = time.time()
                resp = requests.get(url, headers=headers, proxies={"http": "http://localhost:8080", "https": "http://localhost:8080"}, timeout=30, verify=False)
                t1 = time.time()
                item = {
                    'request_id': request_id,
                    'status_code': resp.status_code,
                    'response_time': round(t1 - t0, 3),
                    'proxy_port': resp.headers.get('X-Proxy-Port', 'unknown'),
                    'timestamp': int(time.time()),
                }
                if resp.status_code == 200:
                    item['result_type'] = 'success'
                    return item, 'success'
                if resp.status_code == 429:
                    item['result_type'] = 'rate_limited'
                    return item, 'rate_limited'
                item['result_type'] = 'http_error'
                item['error'] = f'HTTP {resp.status_code}'
                print(resp.text[:200])
                return item, 'other_error'
            except requests.exceptions.ConnectionError:
                return {
                    'request_id': request_id,
                    'status_code': None,
                    'response_time': None,
                    'proxy_port': 'unknown',
                    'result_type': 'connection_error',
                    'error': 'Connection failed',
                    'timestamp': int(time.time()),
                }, 'connection_error'
            except Exception as e:
                return {
                    'request_id': request_id,
                    'status_code': None,
                    'response_time': None,
                    'proxy_port': 'unknown',
                    'result_type': 'exception',
                    'error': str(e)[:100],
                    'timestamp': int(time.time()),
                }, 'other_error'
        print("🔄 Executing requests...")
        print(f"⏱️ Delay between requests: {delay_between_requests:.1f} seconds")
        for i in range(num_requests):
            try:
                if i > 0:
                    time.sleep(delay_between_requests)
                item, kind = make_request(i + 1)
                results.append(item)
                if kind == 'success':
                    success_count += 1
                elif kind == 'rate_limited':
                    error_429_count += 1
                elif kind == 'connection_error':
                    connection_errors += 1
                else:
                    other_errors += 1
                if (i + 1) % 5 == 0:
                    print(f"📈 Progress: {i + 1}/{num_requests} (Success: {success_count}, Errors: {other_errors + error_429_count + connection_errors})")
            except Exception as e:
                other_errors += 1
                print(f"❌ Request {i + 1} failed: {e}")
                results.append({
                    'request_id': i + 1,
                    'status_code': None,
                    'response_time': None,
                    'proxy_port': 'unknown',
                    'result_type': 'exception',
                    'error': str(e)[:100],
                    'timestamp': int(time.time()),
                })
        return results, success_count, error_429_count, other_errors, connection_errors

    def save_results(self, results, success_count, error_429_count, other_errors, connection_errors):
        timestamp = int(time.time())
        total = len(results)
        proxy_usage = {}
        for r in results:
            proxy = r.get('proxy_port', 'unknown')
            proxy_usage[proxy] = proxy_usage.get(proxy, 0) + 1
        summary = {
            'test_info': {'timestamp': timestamp, 'total_requests': total, 'test_type': 'comprehensive_tor_load_balancer_test'},
            'results_summary': {
                'success_count': success_count,
                'rate_limited_count': error_429_count,
                'connection_errors': connection_errors,
                'other_errors': other_errors,
                'success_rate': round((success_count / total) * 100, 2) if total > 0 else 0,
                'rate_limit_rate': round((error_429_count / total) * 100, 2) if total > 0 else 0,
            },
            'proxy_usage': proxy_usage,
            'detailed_results': results,
        }
        results_dir = os.path.join(self.testing_dir, "results")
        os.makedirs(results_dir, exist_ok=True)
        filename = f"full_tor_test_{timestamp}.json"
        path = os.path.join(results_dir, filename)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            print(f"\n💾 Results saved to: {filename}")
            return summary, path
        except Exception as e:
            print(f"❌ Failed to save results: {e}")
            return summary, None

    def stop_balancer(self):
        if self.balancer:
            try:
                print("🛑 Stopping load balancer...")
                self.balancer.stop()
            except Exception as e:
                print(f"⚠️ Error stopping balancer: {e}")

def print_final_results(summary):
    print("\n" + "=" * 70)
    print("📊 ИТОГОВЫЕ РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ")
    print("=" * 70)
    r = summary['results_summary']
    print(f"✅ Успешные запросы (HTTP 200): {r['success_count']}")
    print(f"🚫 Ограничения скорости (HTTP 429): {r['rate_limited_count']}")
    print(f"🔌 Ошибки соединения: {r['connection_errors']}")
    print(f"❌ Другие ошибки: {r['other_errors']}")
    print(f"📈 Процент успеха: {r['success_rate']}%")
    print(f"🚦 Процент ограничений: {r['rate_limit_rate']}%")
    usage = summary['proxy_usage']
    if len(usage) > 1:
        print("\n🔄 Использование прокси:")
        total = summary['test_info']['total_requests']
        for proxy, count in sorted(usage.items()):
            if proxy != 'unknown':
                pct = round((count / total) * 100, 1)
                print(f"  Порт {proxy}: {count} запросов ({pct}%)")

def main():
    print("🎯 Tor Load Balancer - Comprehensive Testing System")
    print("=" * 70)
    tor_manager = TorManager()
    tester = LoadBalancerTester()
    try:
        print("\n🚀 PHASE 1: Starting Tor Network")
        if not tor_manager.start_all_tor_instances():
            print("❌ Failed to start Tor instances. Exiting.")
            return 1
        print("\n🚀 PHASE 2: Starting Load Balancer")
        if not tester.start_balancer():
            print("❌ Failed to start load balancer. Exiting.")
            return 1

        print("\n🚀 PHASE 3: Running Comprehensive Test")
        results, success, rate_limited, other_errors, connection_errors = tester.run_comprehensive_test(TEST_REQUESTS)
        print("\n🚀 PHASE 4: Saving Results")
        summary, _ = tester.save_results(results, success, rate_limited, other_errors, connection_errors)
        print_final_results(summary)
        return 0
    except KeyboardInterrupt:
        print("\n🛑 Test interrupted by user")
        return 1
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        return 1
    finally:
        print("\n🧹 CLEANUP: Stopping all services...")
        try:
            tester.stop_balancer()
        finally:
            tor_manager.stop_all_tor_instances()
        try:
            if getattr(tester, "temp_config_path", None) and os.path.exists(tester.temp_config_path):
                os.remove(tester.temp_config_path)
        except Exception:
            pass
        print("✅ Cleanup completed")

if __name__ == "__main__":
    sys.exit(main())
    
