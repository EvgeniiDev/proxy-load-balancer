"""
Алгоритмы балансировки нагрузки для выбора прокси-серверов
"""
import random
import threading
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class LoadBalancingAlgorithm(ABC):
    """Абстрактный базовый класс для алгоритмов балансировки нагрузки"""

    def __init__(self):
        self.lock = threading.Lock()

    @abstractmethod
    def select_proxy(self, available_proxies: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Выбирает прокси из списка доступных

        Args:
            available_proxies: Список доступных прокси-серверов

        Returns:
            Выбранный прокси или None, если список пуст
        """
        pass

    @abstractmethod
    def reset(self) -> None:
        """Сбрасывает внутреннее состояние алгоритма"""
        pass


class RandomAlgorithm(LoadBalancingAlgorithm):
    """Алгоритм случайного выбора прокси"""

    def select_proxy(self, available_proxies: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not available_proxies:
            return None
        return random.choice(available_proxies)

    def reset(self) -> None:
        # Для случайного алгоритма нет состояния для сброса
        pass


class RoundRobinAlgorithm(LoadBalancingAlgorithm):
    """Алгоритм Round Robin для выбора прокси"""

    def __init__(self):
        super().__init__()
        self.current_index = 0

    def select_proxy(self, available_proxies: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not available_proxies:
            return None

        with self.lock:
            # Убеждаемся, что индекс не выходит за границы списка
            if self.current_index >= len(available_proxies):
                self.current_index = 0

            selected_proxy = available_proxies[self.current_index]
            self.current_index = (self.current_index +
                                  1) % len(available_proxies)

            return selected_proxy

    def reset(self) -> None:
        """Сбрасывает индекс на начало"""
        with self.lock:
            self.current_index = 0


class AlgorithmFactory:
    """Фабрика для создания алгоритмов балансировки"""
    _algorithms = {
        'random': RandomAlgorithm,
        'round_robin': RoundRobinAlgorithm,
    }

    @classmethod
    def create_algorithm(cls, algorithm_name: str) -> LoadBalancingAlgorithm:
        """
        Создает экземпляр алгоритма по его имени

        Args:
            algorithm_name: Название алгоритма

        Returns:
            Экземпляр алгоритма

        Raises:
            ValueError: Если алгоритм не найден
        """
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
        """Возвращает список доступных алгоритмов"""
        return list(cls._algorithms.keys())
