# HTTP Proxy Load Balancer

## � Описание

HTTP Proxy Load Balancer — это инструмент для распределения HTTP/HTTPS трафика между несколькими SOCKS5 прокси-серверами. Приложение автоматически отслеживает доступность прокси, перенаправляет запросы на работающие серверы и восстанавливает подключения к прокси после их восстановления.

## ✨ Функциональные возможности

### Основные функции:
- **Протоколы**: Поддерживает HTTP и HTTPS (через метод CONNECT) прокси как входнйо протокол и Socks5 как выходной
- **Алгоритмы балансировки**: Random (случайный) и Round Robin
- **Автоматический Failover**: Моментальное переключение на рабочие прокси при сбоях
- **Обработка перегрузки (429)**: Автоматическое отключение перегруженных прокси с последующим восстановлением
- **Мониторинг здоровья**: Периодическая проверка доступности всех прокси-серверов
- **Восстановление**: Автоматическое возвращение прокси в работу после восстановления
- **Статистика**: Сбор и вывод статистики по использованию каждой прокси (в verbose режиме)

## 🛠 Установка и настройка

### 1. Установка зависимостей
```bash
pip install -r requirements.txt
```

### 3. Конфигурация
Создайте файл `config.json` с настройками прокси-серверов (см. раздел "Конфигурация" ниже).

## 🚀 Способы запуска

Существует два основных способа запуска балансировщика:

### 1. Прямой запуск из командной строки

Это основной и самый простой способ запустить приложение. Используйте `main.py` для запуска.

```bash
# Запуск с подробным логированием
python main.py -v

# Запуск с пользовательским файлом конфигурации
python main.py -c custom_config.json

# Показать справку
python main.py -h
```

**Доступные опции:**
- `-c, --config` - путь к файлу конфигурации (по умолчанию: `config.json`)
- `-v, --verbose` - включить подробное логирование и вывод статистики
- `-h, --help` - показать справку по использованию

В verbose режиме (`-v`) доступны дополнительные возможности:
- Автоматический вывод статистики каждые 30 секунд
- Сигналы для ручного вывода статистики:
  - `kill -USR1 <PID>` - детальная статистика
  - `kill -USR2 <PID>` - краткая статистика

### 2. Использование как Python-модуль (режим демона)

Вы можете импортировать и запустить `ProxyBalancer` в своем собственном Python-скрипте. Это позволяет встроить балансировщик в другие приложения или управлять им как фоновым процессом (демоном).

**Пример использования:**
```python
from proxy_load_balancer.balancer import ProxyBalancer
from proxy_load_balancer.config import ConfigManager
import time

# 1. Загрузка конфигурации
config_manager = ConfigManager('config.json')
config = config_manager.get_config()

# 2. Создание и запуск балансировщика
balancer = ProxyBalancer(config)
balancer.start()  # Запускает сервер и мониторинг в фоновых потоках

print("Балансировщик запущен. Нажмите Ctrl+C для остановки.")

try:
    # Держать основной поток активным
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Остановка балансировщика...")
    balancer.stop()
    print("Балансировщик остановлен.")

```

```python
from proxy_load_balancer.balancer import ProxyBalancer
import time

# 1. Создание конфигурации вручную (словарь)
manual_config = {
  "server": {
    "host": "127.0.0.1",
    "port": 8888
  },
  "proxies": [
    {"host": "127.0.0.1", "port": 9050},
    {"host": "127.0.0.1", "port": 9051}
  ],
  "load_balancing_algorithm": "random",
  "health_check_interval": 60
}

# 2. Создание и запуск балансировщика
# В этом режиме мониторинг изменений файла конфигурации не используется
balancer = ProxyBalancer(manual_config)
balancer.start()

print(f"Балансировщик запущен на {manual_config['server']['host']}:{manual_config['server']['port']}.")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Остановка балансировщика...")
    balancer.stop()
    print("Балансировщик остановлен.")
```
Этот подход дает больше гибкости для управления жизненным циклом балансировщика.

## ⚙️ Конфигурация

### Файл конфигурации (config.json)

Приложение использует JSON-файл для настройки всех параметров работы. По умолчанию ожидается файл `config.json` в корневой папке проекта.

### Пример конфигурации:
```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8080
  },
  "proxies": [
    {
      "host": "127.0.0.1",
      "port": 9050
    },
    {
      "host": "127.0.0.1", 
      "port": 9051
    },
    {
      "host": "127.0.0.1",
      "port": 9052
    }
  ],
  "load_balancing_algorithm": "round_robin",
  "health_check_interval": 30,
  "connection_timeout": 30,
  "max_retries": 3,
  "proxy_rest_duration": 300
}
```

### Описание параметров:

#### Секция `server` (обязательная):
- **`host`** - IP-адрес для прослушивания балансировщика
  - `"0.0.0.0"` - слушать на всех интерфейсах
  - `"127.0.0.1"` - только локальные подключения
- **`port`** - порт для HTTP прокси сервера (например: 8080)

#### Секция `proxies` (обязательная):
Массив SOCKS5 прокси-серверов для балансировки:
- **`host`** - IP-адрес или hostname прокси-сервера
- **`port`** - порт прокси-сервера

#### Параметры мониторинга (опциональные):
- **`load_balancing_algorithm`** - алгоритм балансировки нагрузки:
  - `"random"` - случайный выбор прокси (по умолчанию)
  - `"round_robin"` - циклический перебор прокси
- **`health_check_interval`** - интервал проверки здоровья прокси в секундах (по умолчанию: 30)
- **`connection_timeout`** - таймаут подключения к прокси в секундах (по умолчанию: 30)  
- **`max_retries`** - максимальное количество неудачных попыток до отключения прокси (по умолчанию: 3)
- **`proxy_rest_duration`** - время отдыха прокси после перегрузки (статус 429) в секундах (по умолчанию: 300)

## 🌐 Использование

### Настройка клиентов

После запуска балансировщика настройте ваши приложения на использование HTTP прокси:
- **HTTP Proxy**: `http://localhost:8080` (или ваш хост:порт из конфигурации)
- **HTTPS Proxy**: `http://localhost:8080` (тот же адрес для HTTPS через CONNECT)

## 📊 Мониторинг и статистика

### Встроенная система мониторинга:

#### Проверка здоровья прокси:
- **Автоматическая проверка**: Периодическое тестирование доступности каждого прокси
- **Интервал проверки**: Настраивается в config.json (параметр `health_check_interval`)
- **Отключение неработающих**: Прокси автоматически исключаются при сбоях
- **Обработка перегрузки**: Прокси со статусом 429 (Too Many Requests) отправляются "отдыхать"
- **Восстановление**: Неработающие и отдыхающие прокси автоматически возвращаются в работу после восстановления

#### Состояния прокси:
- **Доступные (Available)**: Прокси работают и обрабатывают запросы ✅
- **Недоступные (Unavailable)**: Прокси недоступны из-за технических проблем ❌
- **Отдыхающие (Resting)**: Прокси временно отключены из-за перегрузки (статус 429) 💤

#### Статистика работы:
- **Успешные запросы**: Подсчет обработанных запросов для каждой прокси
- **Неудачные запросы**: Отслеживание ошибок и причин сбоев по каждой прокси
- **Процент успешности**: Автоматический расчет success rate для каждой прокси
- **Статус прокси**: Текущее состояние каждой прокси (доступна/недоступна/отдыхает)
- **Счетчики перегрузок**: Отслеживание количества перегрузок (429) для каждой прокси
- **Общая статистика**: Суммарные метрики по всей системе
- **Автоматический вывод**: В verbose режиме статистика выводится каждые 30 секунд
- **Сигналы управления**: SIGUSR1 (детальная) и SIGUSR2 (краткая) статистика

#### Логирование:
- **Стандартный режим**: Основная информация о работе балансировщика
- **Verbose режим** (`-v`): Подробные логи всех операций
- **Состояние прокси**: Уведомления о подключении/отключении прокси
- **Статистика запросов**: Информация об обработанных запросах

### Процесс работы:
1. При запуске все прокси проверяются на доступность
2. Запросы распределяются между работающими прокси согласно выбранному алгоритму балансировки
3. При сбое прокси он немедленно исключается из ротации
4. Неработающие прокси периодически проверяются и восстанавливаются
5. Ведется статистика по каждому прокси и общая статистика балансировщика

### 📋 Программное использование StatsReporter

Класс `StatsReporter` предоставляет удобный API для получения детальной статистики о работе прокси. Вы можете использовать его для интеграции с системами мониторинга, создания дашбордов или автоматизации управления прокси.

#### Получение общей статистики:

```python
from proxy_load_balancer.proxy_balancer import ProxyBalancer

# Создание балансировщика
config = {
    "proxies": [
        {"host": "proxy1.example.com", "port": 8080, "type": "socks5"},
        {"host": "proxy2.example.com", "port": 8080, "type": "socks5"}
    ],
    "load_balancing_algorithm": "round_robin"
}

balancer = ProxyBalancer(config)

# Получение общей статистики
stats = balancer.stats_reporter.get_stats()
print(f"Всего запросов: {stats['total_requests']}")
print(f"Процент успеха: {stats['overall_success_rate']}%")
print(f"Доступных прокси: {stats['available_proxies_count']}")
print(f"Недоступных прокси: {stats['unavailable_proxies_count']}")

# Вывод красиво отформатированной статистики
balancer.stats_reporter.print_stats()
```

#### Работа с отдельными прокси:

```python
# Получение списка всех прокси
all_proxies = balancer.stats_reporter.get_all_proxy_keys()
print(f"Все прокси: {all_proxies}")

# Получение статистики конкретного прокси
proxy_key = "proxy1.example.com:8080"
proxy_stats = balancer.stats_reporter.get_proxy_stats(proxy_key)

if "error" not in proxy_stats:
    print(f"Прокси: {proxy_stats['proxy_key']}")
    print(f"Статус: {proxy_stats['status']}")
    print(f"Запросов: {proxy_stats['requests']}")
    print(f"Успешных: {proxy_stats['successes']}")
    print(f"Неудачных: {proxy_stats['failures']}")
    print(f"Процент успеха: {proxy_stats['success_rate']}%")
    print(f"Использовался: {'Да' if proxy_stats['has_been_used'] else 'Нет'}")

# Красивый вывод статистики отдельного прокси
balancer.stats_reporter.print_proxy_stats(proxy_key)
```

#### Фильтрация прокси по статусу:

```python
# Получение только доступных прокси
available_proxies = balancer.stats_reporter.get_proxies_by_status("available")
print(f"Доступных прокси: {len(available_proxies)}")

for proxy in available_proxies:
    print(f"  - {proxy['proxy_key']}: {proxy['requests']} запросов")

# Получение недоступных прокси
unavailable_proxies = balancer.stats_reporter.get_proxies_by_status("unavailable")
print(f"Недоступных прокси: {len(unavailable_proxies)}")

for proxy in unavailable_proxies:
    print(f"  - {proxy['proxy_key']}: {proxy['requests']} запросов")
```

#### Сводка по всем прокси:

```python
# Получение сводки по всем прокси
summary = balancer.stats_reporter.get_proxy_summary()

for proxy_key, stats in summary.items():
    status_icon = "✓" if stats['status'] == 'available' else "✗"
    print(f"{proxy_key} {status_icon} - {stats['requests']} запросов, "
          f"{stats['success_rate']}% успеха")
```

#### Доступные методы StatsReporter:

| Метод | Описание | Возвращает |
|-------|----------|------------|
| `get_stats()` | Общая статистика балансировщика | `Dict[str, Any]` |
| `get_proxy_stats(proxy_key)` | Статистика конкретного прокси | `Dict[str, Any]` |
| `get_all_proxy_keys()` | Список всех ключей прокси | `List[str]` |
| `get_proxy_summary()` | Сводка по всем прокси | `Dict[str, Dict[str, Any]]` |
| `get_proxies_by_status(status)` | Прокси с определенным статусом | `List[Dict[str, Any]]` |
| `print_stats()` | Вывод общей статистики | `None` |
| `print_proxy_stats(proxy_key)` | Вывод статистики прокси | `None` |
| `print_compact_stats()` | Краткий вывод статистики | `None` |

#### Структура данных статистики прокси:

```python
{
    "proxy_key": "host:port",           # Ключ прокси
    "status": "available|unavailable",  # Статус прокси
    "requests": 42,                     # Общее количество запросов
    "successes": 38,                    # Успешные запросы
    "failures": 4,                      # Неудачные запросы
    "success_rate": 90.48,              # Процент успешных запросов
    "sessions_pooled": 3,               # Количество сессий в пуле
    "has_been_used": True               # Использовался ли прокси
}
```

#### Пример мониторинга в реальном времени:

```python
import time
import threading

def monitor_proxies(balancer):
    """Функция для мониторинга прокси в реальном времени"""
    while True:
        stats = balancer.stats_reporter.get_stats()
        
        # Проверка наличия недоступных прокси
        if stats['unavailable_proxies_count'] > 0:
            unavailable = balancer.stats_reporter.get_proxies_by_status("unavailable")
            print(f"⚠️  Внимание: {len(unavailable)} прокси недоступны!")
            for proxy in unavailable:
                print(f"   - {proxy['proxy_key']}")
        
        # Проверка низкого процента успеха
        if stats['overall_success_rate'] < 80 and stats['total_requests'] > 0:
            print(f"⚠️  Низкий процент успеха: {stats['overall_success_rate']}%")
        
        # Поиск проблемных прокси
        all_proxies = balancer.stats_reporter.get_proxy_summary()
        for proxy_key, proxy_stats in all_proxies.items():
            if (proxy_stats['requests'] > 10 and 
                proxy_stats['success_rate'] < 50):
                print(f"🚨 Проблемный прокси {proxy_key}: "
                      f"{proxy_stats['success_rate']}% успеха")
        
        # Краткая статистика каждые 30 секунд
        balancer.stats_reporter.print_compact_stats()
        
        time.sleep(30)

# Запуск балансировщика с мониторингом
balancer = ProxyBalancer(config, verbose=True)
balancer.start()

# Запуск мониторинга в отдельном потоке
```

## 🧪 Тестирование функциональности обработки перегрузки

### Демонстрационный скрипт

Для тестирования новой функциональности обработки перегруженных прокси запустите демонстрационный скрипт:

```bash
python test_overload_demo.py
```

Этот скрипт покажет:
- Начальное состояние прокси
- Процесс отправки прокси на "отдых" при перегрузке
- Автоматическое восстановление через заданное время
- Работу статистики и мониторинга

### Тестирование в реальной среде

1. **Запустите балансировщик** с verbose режимом:
```bash
python main.py -v
```

2. **Настройте короткое время отдыха** в `config.json` для быстрого тестирования:
```json
{
  "proxy_rest_duration": 60
}
```

3. **Симулируйте перегрузку** выполнив множество запросов через прокси, который возвращает 429
4. **Наблюдайте** как прокси автоматически перемещается в состояние "отдыхает" 💤
5. **Ждите восстановления** через указанное время

### Подробная документация

Полная документация по обработке перегрузки доступна в файле [`OVERLOAD_HANDLING.md`](OVERLOAD_HANDLING.md).

## 📝 Лицензия

MIT License. См. файл [LICENSE](LICENSE) для подробностей.
monitor_thread = threading.Thread(target=monitor_proxies, args=(balancer,), daemon=True)
monitor_thread.start()

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    balancer.stop()
```
