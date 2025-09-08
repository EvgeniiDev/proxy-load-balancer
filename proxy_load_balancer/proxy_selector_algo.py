import random
import threading
from typing import Any, Dict, List, Optional

from .base import Logger


class LoadBalancingAlgorithm:
    """Базовый класс для алгоритмов балансировки нагрузки."""
    
    def __init__(self, name: str):
        self.logger = Logger.get_logger(f"algorithm_{name}")
        self._lock = threading.Lock()
        
    def select_proxy(self, available_proxies: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Выбор прокси из доступных."""
        raise NotImplementedError

    def reset(self) -> None:
        """Сброс состояния алгоритма."""
        raise NotImplementedError


class RandomAlgorithm(LoadBalancingAlgorithm):
    """Алгоритм случайного выбора прокси."""
    
    def __init__(self):
        super().__init__("random")
    
    def select_proxy(self, available_proxies: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not available_proxies:
            return None
        return random.choice(available_proxies)
    
    def reset(self) -> None:
        pass


class RoundRobinAlgorithm(LoadBalancingAlgorithm):
    """Алгоритм циклического выбора прокси."""
    
    def __init__(self):
        super().__init__("round_robin")
        self._current_index = 0

    def select_proxy(self, available_proxies: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not available_proxies:
            return None
            
        with self._lock:
            if self._current_index >= len(available_proxies):
                self._current_index = 0
            selected_proxy = available_proxies[self._current_index]
            self._current_index = (self._current_index + 1) % len(available_proxies)
            return selected_proxy
        
    def reset(self) -> None:
        with self._lock:
            self._current_index = 0
            
class AlgorithmFactory:
    _algorithms = {
        'random': RandomAlgorithm,
        'round_robin': RoundRobinAlgorithm,
    }

    @classmethod
    def create_algorithm(cls, algorithm_name: str) -> LoadBalancingAlgorithm:
        algorithm_name_lower = algorithm_name.lower()
        if algorithm_name_lower not in cls._algorithms:
            available = ', '.join(cls._algorithms.keys())
            raise ValueError(
                f"Неизвестный алгоритм балансировки: {algorithm_name}. "
                f"Доступные алгоритмы: {available}"
            )
        return cls._algorithms[algorithm_name_lower]()
    
    @classmethod
    def get_available_algorithms(cls) -> List[str]:
        return list(cls._algorithms.keys())
