"""
Базовые утилиты и общие компоненты для прокси-балансировщика.
"""
import logging
from typing import Any, Dict


class Logger:
    """Статический логгер для всех компонентов системы."""
    
    _logger = None
    
    @classmethod
    def get_logger(cls, name: str = "proxy_balancer") -> logging.Logger:
        """Получение настроенного логгера."""
        if cls._logger is None:
            cls._logger = cls._setup_logger(name)
        return cls._logger
    
    @classmethod
    def _setup_logger(cls, name: str) -> logging.Logger:
        """Настройка логгера."""
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            
        return logger


class ProxyHandler:
    """Утилиты для работы с прокси."""
    
    @staticmethod
    def get_proxy_key(proxy: Dict[str, Any]) -> str:
        """Получение уникального ключа прокси."""
        return f"{proxy['host']}:{proxy['port']}"
    
    @staticmethod
    def create_proxy_url(proxy: Dict[str, Any], protocol: str = "socks5") -> str:
        """Создание URL прокси."""
        return f"{protocol}://{proxy['host']}:{proxy['port']}"
    
    @staticmethod
    def validate_proxy(proxy: Dict[str, Any]) -> bool:
        """Валидация конфигурации прокси."""
        required_fields = ['host', 'port']
        return all(field in proxy for field in required_fields)


class ConfigValidator:
    """Валидатор конфигурации."""
    
    @staticmethod
    def validate_config(config: Dict[str, Any]) -> bool:
        """Полная валидация конфигурации."""
        required_fields = ["server", "proxies", "health_check_interval", "max_retries"]
        
        # Проверка обязательных полей
        for field in required_fields:
            if field not in config:
                print(f"Missing required field in config: {field}")
                return False
        
        # Проверка списка прокси
        if not isinstance(config["proxies"], list):
            print("Proxies must be a list")
            return False
            
        # Валидация каждого прокси
        for i, proxy in enumerate(config["proxies"]):
            if not ProxyHandler.validate_proxy(proxy):
                print(f"Invalid proxy configuration at index {i}")
                return False
                
        return True
    
    @staticmethod
    def get_config_value(config: Dict[str, Any], key: str, default: Any) -> Any:
        """Безопасное получение значения из конфигурации."""
        return config.get(key, default)
