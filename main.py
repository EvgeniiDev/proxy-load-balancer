import argparse
import sys
import threading
from typing import Optional

from proxy_load_balancer.proxy_balancer import ProxyBalancer
from proxy_load_balancer.config import ConfigManager


def display_help():
    """Отображение справки по использованию."""
    print("HTTPS Proxy Load Balancer")
    print("")
    print("Usage:")
    print("    python main.py [-c config.json] [-v]")
    print("")
    print("Options:")
    print("    -c, --config    Configuration file path")
    print("    -v, --verbose   Enable verbose output")
    print("    -h, --help      Show this help message")


def setup_config_callbacks(balancer: ProxyBalancer, config_manager: ConfigManager, verbose: bool):
    """Настройка callback'ов для изменения конфигурации."""
    def on_config_change(new_config):
        if verbose:
            print("Configuration changed, updating balancer...")
        balancer.update_proxies(new_config)
        balancer.reload_algorithm()

    balancer.set_config_manager(config_manager, on_config_change)
    config_manager.add_change_callback(on_config_change)
    return on_config_change


def print_startup_info(config, config_file: str, verbose: bool):
    """Вывод информации о запуске."""
    print("Starting proxy balancer")
    print(f"Proxies: {len(config['proxies'])}")
    print(f"Config monitoring: enabled for {config_file}")
    if verbose:
        print("Verbose mode enabled")


def start_balancer(config_file: str, verbose: bool = False) -> Optional[int]:
    """Запуск балансировщика прокси."""
    try:
        # Инициализация
        config_manager = ConfigManager(config_file)
        config = config_manager.get_config()
        balancer = ProxyBalancer(config, verbose=verbose)

        # Настройка callbacks
        setup_config_callbacks(balancer, config_manager, verbose)

        # Запуск мониторинга конфигурации
        config_manager.start_monitoring()

        # Вывод информации о запуске
        print_startup_info(config, config_file, verbose)

        # Запуск балансировщика
        balancer.start()

        # Ожидание завершения
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            print("\nShutting down...")
            config_manager.stop_monitoring()
            balancer.stop()
            print("Stopped")

        return 0

    except FileNotFoundError:
        print(f"Config file not found: {config_file}")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        return 1


def parse_arguments():
    """Парсинг аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description="HTTPS Proxy Load Balancer", 
        add_help=False
    )
    parser.add_argument(
        "-c", "--config", 
        default="config.json", 
        help="Configuration file path"
    )
    parser.add_argument(
        "-v", "--verbose", 
        action="store_true", 
        help="Enable verbose output"
    )
    parser.add_argument(
        "-h", "--help", 
        action="store_true", 
        help="Show this help message"
    )
    return parser.parse_args()


def main():
    """Главная функция."""
    args = parse_arguments()
    
    if args.help:
        display_help()
        return 0
        
    return start_balancer(args.config, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
