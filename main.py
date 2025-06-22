import asyncio
import argparse
import sys
import signal
from proxy_load_balancer.balancer import ProxyBalancer
from proxy_load_balancer.config import ConfigManager
from proxy_load_balancer.connect_server import ConnectTunnelServer


def display_help():
    print("HTTP Proxy Load Balancer")
    print("")
    print("Usage:")
    print("    python main.py [-c config.json] [-v]")
    print("")
    print("Options:")
    print("    -c, --config    Configuration file path")
    print("    -v, --verbose   Enable verbose output")
    print("    -h, --help      Show this help message")


async def configure_performance_settings(perf_config: dict):
    """Configure asyncio and system performance settings"""
    import gc

    # Configure garbage collection for better performance
    gc.set_threshold(700, 10, 10)

    # Set asyncio policies for better performance if available
    try:
        if hasattr(asyncio, 'set_event_loop_policy'):
            if sys.platform == 'win32':
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            else:
                try:
                    import uvloop
                    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
                except ImportError:
                    pass  # Use default policy
    except Exception:
        pass  # Use default settings


async def start_balancer(config_file: str, verbose: bool = False):
    import logging
    
    # Configure logging
    if verbose:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    else:
        logging.basicConfig(level=logging.WARNING)
    
    try:
        config_manager = ConfigManager(config_file)
        config = config_manager.get_config()

        # Configure performance settings
        if 'performance' in config:
            await configure_performance_settings(config['performance'])

        balancer = ProxyBalancer(config)

        def on_config_change(new_config):
            if verbose:
                print("Configuration changed, updating balancer...")
            balancer.update_proxies(new_config)
            balancer.reload_algorithm()

        balancer.set_config_manager(config_manager, on_config_change)
        config_manager.add_change_callback(on_config_change)
        config_manager.start_monitoring()

        print(f"Starting proxy balancer on {config['server']['host']}:{config['server']['port']}")
        
        # Start CONNECT tunnel server if configured
        connect_server = None
        if config.get('connect_server', {}).get('enabled', True):
            connect_port = config.get('connect_server', {}).get('port', config['server']['port'] + 1)
            connect_host = config.get('connect_server', {}).get('host', config['server']['host'])
            connect_server = ConnectTunnelServer(connect_host, connect_port, balancer, config)
            await connect_server.start()
            print(f"CONNECT tunnel server started on {connect_host}:{connect_port}")
        
        print(f"Proxies: {len(config['proxies'])}")
        print(f"Config monitoring: enabled for {config_file}")
        if verbose:
            print("Verbose mode enabled")
            if 'performance' in config:
                perf = config['performance']
                print(f"Performance: max_connections={perf.get('max_concurrent_connections', 1000)}, "
                      f"pool_size={perf.get('connection_pool_size', 100)}")

        # Start the main HTTP balancer
        runner = await balancer.start()

        # Setup graceful shutdown with proper signal handling
        shutdown_event = asyncio.Event()

        def signal_handler():
            print("\nShutting down...")
            shutdown_event.set()

        if sys.platform != 'win32':
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, signal_handler)

        try:
            # Efficient wait for shutdown instead of busy loop
            await shutdown_event.wait()
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            await shutdown(balancer, config_manager, connect_server)

    except FileNotFoundError:
        print(f"Config file not found: {config_file}")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        return 1

    return 0


async def shutdown(balancer: ProxyBalancer, config_manager: ConfigManager, connect_server=None):
    config_manager.stop_monitoring()
    await balancer.stop()
    if connect_server:
        await connect_server.stop()
        print("CONNECT server stopped")
    print("Stopped")

    # Stop the event loop
    loop = asyncio.get_running_loop()
    loop.stop()


def main():
    parser = argparse.ArgumentParser(description="HTTP Proxy Load Balancer", add_help=False)
    parser.add_argument("-c", "--config", default="config.json", help="Configuration file path")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("-h", "--help", action="store_true", help="Show this help message")
    args = parser.parse_args()

    if args.help:
        display_help()
        return 0

    try:
        return asyncio.run(start_balancer(args.config, args.verbose))
    except KeyboardInterrupt:
        print("\nShutdown complete")
        return 0


if __name__ == "__main__":
    sys.exit(main())
