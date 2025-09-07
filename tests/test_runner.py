#!/usr/bin/env python3
"""
Система запуска тестов для Proxy Load Balancer

Использование:
    python -m tests.test_runner                    # Запустить все тесты
    python -m tests.test_runner --fast             # Быстрые тесты
    python -m tests.test_runner --category load    # Тесты по категории
    python -m tests.test_runner test_load_balancing # Конкретный модуль
"""

import unittest
import sys
import argparse
import time
from typing import List, Dict, Any


class TestRunner:
    """Система запуска и управления тестами"""
    
    def __init__(self):
        self.test_categories = {
            'load': ['test_load_balancing'],
            'health': ['test_proxy_health'],
            'http': ['test_http_methods'],
            'stats': ['test_stats_monitoring'],
            'config': ['test_configuration'],
            'integration': ['test_integration'],
            'edge': ['test_edge_cases'],
            'memory': ['test_memory_management'],
            'fast': ['test_load_balancing', 'test_proxy_health', 'test_http_methods'],
            'full': ['test_load_balancing', 'test_proxy_health', 'test_http_methods', 
                    'test_stats_monitoring', 'test_configuration', 'test_integration', 
                    'test_edge_cases', 'test_memory_management']
        }
        
        self.test_descriptions = {
            'test_load_balancing': 'Тесты алгоритмов балансировки нагрузки',
            'test_proxy_health': 'Тесты здоровья прокси и обработки ошибок 429',
            'test_http_methods': 'Тесты HTTP/HTTPS методов и протоколов',
            'test_stats_monitoring': 'Тесты статистики и мониторинга',
            'test_configuration': 'Тесты конфигурации системы',
            'test_integration': 'Комплексные интеграционные тесты',
            'test_edge_cases': 'Тесты граничных случаев',
            'test_memory_management': 'Тесты управления памятью'
        }
    
    def run_tests(self, test_modules: List[str] = None, verbose: bool = False, 
                  stop_on_failure: bool = False) -> Dict[str, Any]:
        """Запускает указанные тесты и возвращает результаты"""
        
        if test_modules is None:
            test_modules = self.test_categories['full']
        
        results = {
            'total_tests': 0,
            'passed': 0,
            'failed': 0,
            'errors': 0,
            'skipped': 0,
            'modules': {},
            'start_time': time.time(),
            'end_time': None
        }
        
        print(f"🚀 Запуск тестов: {', '.join(test_modules)}")
        print("=" * 80)
        
        for module_name in test_modules:
            print(f"\n📦 Модуль: {module_name}")
            print(f"📝 {self.test_descriptions.get(module_name, 'Описание недоступно')}")
            print("-" * 60)
            
            module_results = self._run_single_module(module_name, verbose, stop_on_failure)
            results['modules'][module_name] = module_results
            
            # Обновляем общие результаты
            results['total_tests'] += module_results['tests_run']
            results['passed'] += module_results['passed']
            results['failed'] += len(module_results['failures'])
            results['errors'] += len(module_results['errors'])
            results['skipped'] += len(module_results['skipped'])
            
            # Отображаем результаты модуля
            self._print_module_results(module_name, module_results)
            
            # Останавливаемся при ошибке, если требуется
            if stop_on_failure and (module_results['failures'] or module_results['errors']):
                print(f"\n⚠️ Остановка выполнения из-за ошибок в модуле {module_name}")
                break
        
        results['end_time'] = time.time()
        return results
    
    def _run_single_module(self, module_name: str, verbose: bool, stop_on_failure: bool) -> Dict[str, Any]:
        """Запускает тесты одного модуля"""
        try:
            # Импортируем модуль
            module = __import__(f'tests.{module_name}', fromlist=[module_name])
            
            # Создаем test suite
            loader = unittest.TestLoader()
            suite = loader.loadTestsFromModule(module)
            
            # Запускаем тесты
            stream = sys.stdout if verbose else None
            runner = unittest.TextTestRunner(
                stream=stream,
                verbosity=2 if verbose else 1,
                failfast=stop_on_failure
            )
            
            result = runner.run(suite)
            
            return {
                'tests_run': result.testsRun,
                'passed': result.testsRun - len(result.failures) - len(result.errors),
                'failures': result.failures,
                'errors': result.errors,
                'skipped': result.skipped if hasattr(result, 'skipped') else []
            }
            
        except ImportError as e:
            print(f"❌ Ошибка импорта модуля {module_name}: {e}")
            return {
                'tests_run': 0,
                'passed': 0,
                'failures': [],
                'errors': [('ImportError', str(e))],
                'skipped': []
            }
        except Exception as e:
            print(f"❌ Неожиданная ошибка в модуле {module_name}: {e}")
            return {
                'tests_run': 0,
                'passed': 0,
                'failures': [],
                'errors': [('UnexpectedError', str(e))],
                'skipped': []
            }
    
    def _print_module_results(self, module_name: str, results: Dict[str, Any]):
        """Выводит результаты модуля"""
        tests_run = results['tests_run']
        passed = results['passed']
        failures = len(results['failures'])
        errors = len(results['errors'])
        skipped = len(results['skipped'])
        
        if tests_run == 0:
            print(f"⚠️ Тесты не выполнялись")
            return
        
        print(f"📊 Результаты:")
        print(f"   ✅ Пройдено: {passed}")
        if failures > 0:
            print(f"   ❌ Провалено: {failures}")
        if errors > 0:
            print(f"   💥 Ошибки: {errors}")
        if skipped > 0:
            print(f"   ⏭️ Пропущено: {skipped}")
        
        success_rate = (passed / tests_run) * 100 if tests_run > 0 else 0
        status_emoji = "✅" if success_rate == 100 else "⚠️" if success_rate >= 80 else "❌"
        print(f"   {status_emoji} Успешность: {success_rate:.1f}%")
        
        # Выводим детали ошибок если есть
        if failures and len(results['failures']) > 0:
            print(f"\n🚨 Провалившиеся тесты:")
            for test, traceback in results['failures'][:3]:  # Показываем первые 3
                print(f"   - {test}")
        
        if errors and len(results['errors']) > 0:
            print(f"\n💥 Ошибки:")
            for test, traceback in results['errors'][:3]:  # Показываем первые 3
                print(f"   - {test}")
    
    def print_final_summary(self, results: Dict[str, Any]):
        """Выводит итоговую сводку"""
        duration = results['end_time'] - results['start_time']
        
        print("\n" + "=" * 80)
        print("📋 ИТОГОВАЯ СВОДКА")
        print("=" * 80)
        
        print(f"⏱️ Время выполнения: {duration:.2f} секунд")
        print(f"🔢 Всего тестов: {results['total_tests']}")
        print(f"✅ Пройдено: {results['passed']}")
        print(f"❌ Провалено: {results['failed']}")
        print(f"💥 Ошибки: {results['errors']}")
        print(f"⏭️ Пропущено: {results['skipped']}")
        
        if results['total_tests'] > 0:
            success_rate = (results['passed'] / results['total_tests']) * 100
            print(f"📈 Общая успешность: {success_rate:.1f}%")
            
            # Определяем общий статус
            if success_rate == 100:
                status = "🎉 ВСЕ ТЕСТЫ ПРОШЛИ!"
                print(f"\n{status}")
            elif success_rate >= 80:
                status = "⚠️ Большинство тестов прошло"
                print(f"\n{status}")
            else:
                status = "❌ Много проваленных тестов"
                print(f"\n{status}")
        
        # Выводим статистику по модулям
        print(f"\n📦 Результаты по модулям:")
        for module_name, module_results in results['modules'].items():
            tests_run = module_results['tests_run']
            passed = module_results['passed']
            if tests_run > 0:
                success_rate = (passed / tests_run) * 100
                status_emoji = "✅" if success_rate == 100 else "⚠️" if success_rate >= 80 else "❌"
                print(f"   {status_emoji} {module_name}: {passed}/{tests_run} ({success_rate:.1f}%)")
            else:
                print(f"   ⚠️ {module_name}: Не выполнялся")
    
    def list_available_tests(self):
        """Выводит список доступных тестов и категорий"""
        print("📋 ДОСТУПНЫЕ ТЕСТЫ И КАТЕГОРИИ")
        print("=" * 80)
        
        print("\n🏷️ Категории:")
        for category, modules in self.test_categories.items():
            print(f"   {category}: {', '.join(modules)}")
        
        print(f"\n📦 Модули тестов:")
        for module, description in self.test_descriptions.items():
            print(f"   {module}: {description}")
        
        print(f"\n💡 Примеры использования:")
        print(f"   python -m tests.test_runner --category fast")
        print(f"   python -m tests.test_runner test_load_balancing")
        print(f"   python -m tests.test_runner --verbose test_proxy_health")


def main():
    """Главная функция"""
    parser = argparse.ArgumentParser(
        description="Система запуска тестов Proxy Load Balancer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  %(prog)s                           # Все тесты
  %(prog)s --fast                    # Быстрые тесты
  %(prog)s --category load           # Тесты балансировки
  %(prog)s test_load_balancing       # Конкретный модуль
  %(prog)s --list                    # Список доступных тестов
  %(prog)s --verbose test_proxy_health  # Подробный вывод
        """
    )
    
    parser.add_argument('modules', nargs='*', 
                       help='Модули тестов для запуска')
    parser.add_argument('--category', '-c', 
                       help='Категория тестов для запуска')
    parser.add_argument('--fast', action='store_true', 
                       help='Запустить только быстрые тесты')
    parser.add_argument('--verbose', '-v', action='store_true', 
                       help='Подробный вывод')
    parser.add_argument('--stop-on-failure', '-s', action='store_true', 
                       help='Остановиться при первой ошибке')
    parser.add_argument('--list', '-l', action='store_true', 
                       help='Показать список доступных тестов')
    
    args = parser.parse_args()
    
    runner = TestRunner()
    
    if args.list:
        runner.list_available_tests()
        return
    
    # Определяем какие тесты запускать
    test_modules = None
    
    if args.fast:
        test_modules = runner.test_categories['fast']
    elif args.category:
        if args.category in runner.test_categories:
            test_modules = runner.test_categories[args.category]
        else:
            print(f"❌ Неизвестная категория: {args.category}")
            print(f"Доступные категории: {', '.join(runner.test_categories.keys())}")
            sys.exit(1)
    elif args.modules:
        test_modules = args.modules
    else:
        test_modules = runner.test_categories['full']
    
    # Запускаем тесты
    try:
        results = runner.run_tests(
            test_modules=test_modules,
            verbose=args.verbose,
            stop_on_failure=args.stop_on_failure
        )
        
        runner.print_final_summary(results)
        
        # Возвращаем код выхода на основе результатов
        if results['failed'] > 0 or results['errors'] > 0:
            sys.exit(1)
        else:
            sys.exit(0)
            
    except KeyboardInterrupt:
        print(f"\n\n⚠️ Выполнение прервано пользователем")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n💥 Критическая ошибка: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
