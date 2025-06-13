import argparse
import sys
import time
from typing import Any, Dict, Optional

from balancer import ProxyBalancer
from balancer.config import load_config


def start_balancer(config_file: str, verbose: bool = False) -> int:
    """Запуск прокси-балансировщика"""
    try:
        config = load_config(config_file)
        balancer = ProxyBalancer(config)

        print(f"Starting proxy balancer on {config['host']}:{config['port']}")
        print(f"Proxies: {len(config['proxies'])}")
        if verbose:
            print("Verbose mode enabled")

        balancer.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down...")
            balancer.stop()
            print("Stopped")
            return 0

    except FileNotFoundError:
        print(f"Config file not found: {config_file}")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        return 1


def display_help() -> int:
    """Показать справку"""
    print("HTTP Proxy Load Balancer")
    print("")
    print("Usage:")
    print("    python main.py [start] [-c config.json] [-v]")
    print("")
    print("Commands:")
    print("    start           Start the proxy balancer")
    print("")
    print("Options:")
    print("    -c, --config    Configuration file path")
    print("    -v, --verbose   Enable verbose output")
    print("    -h, --help      Show this help message")
    return 0


def main() -> int:
    """Главная функция консольной утилиты"""
    parser = argparse.ArgumentParser(description="HTTP Proxy Load Balancer")
    parser.add_argument(
        "-c", "--config", default="config.json", help="Configuration file path"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    start_parser = subparsers.add_parser("start", help="Start proxy balancer (default)")
    start_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose output"
    )

    args = parser.parse_args()

    if not args.command:
        args.command = "start"
        args.verbose = False

    if args.command == "start":
        return start_balancer(args.config, getattr(args, "verbose", False))
    else:
        return display_help()


if __name__ == "__main__":
    sys.exit(main())
