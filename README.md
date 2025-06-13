# HTTP Proxy Load Balancer

Высокопроизводительный HTTP прокси-балансировщик с модульной архитектурой для распределения нагрузки между SOCKS5 прокси-серверами.

## 🚀 Возможности

- **Высокая производительность**: Многопоточная архитектура для обработки тысяч одновременных соединений
- **Модульная структура**: Четкое разделение компонентов для легкой поддержки и расширения
- **Протоколы**: Поддержка HTTP, HTTPS (через CONNECT) и всех HTTP методов
- **Балансировка**: Случайное распределение нагрузки с автоматическим failover
- **Мониторинг**: Встроенная система проверки здоровья прокси и статистики
- **CLI управление**: Удобные инструменты для конфигурации и управления

## 📁 Структура проекта

```
proxy-load-balancer/
├── balancer/                 # Основной пакет
│   ├── server.py            # HTTP сервер
│   ├── handler.py           # Обработчик запросов
│   ├── balancer.py          # Логика балансировщика
│   ├── config.py            # Загрузка конфигурации
│   ├── utils.py             # Утилиты управления прокси
│   ├── monitor.py           # Мониторинг и статистика
│   └── __init__.py          # Инициализация пакета
├── main.py                  # Единая точка входа (запуск + управление)
├── test_proxy.py            # Тесты
├── config.json              # Конфигурация
└── requirements.txt         # Зависимости
```

## ⚡ Быстрый старт

### 1. Установка зависимостей
```bash
pip install -r requirements.txt
```

### 2. Конфигурация
Создайте файл `config.json` с настройками прокси серверов (см. пример ниже).

### 3. Запуск
```bash
python main.py start -v
```

## 🔧 Конфигурация

### Пример config.json:
```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8080
  },
  "proxies": [
    {"host": "127.0.0.1", "port": 9050},
    {"host": "127.0.0.1", "port": 9051},
    {"host": "127.0.0.1", "port": 9052}
  ],
  "health_check_interval": 30,
  "connection_timeout": 30,
  "max_retries": 3
}
```

### Пример расширенной конфигурации:
```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8080
  },
  "proxies": [
    {"host": "127.0.0.1", "port": 9050},
    {"host": "127.0.0.1", "port": 9051},
    {"host": "127.0.0.1", "port": 9052},
    {"host": "proxy1.example.com", "port": 1080},
    {"host": "proxy2.example.com", "port": 1080}
  ],
  "health_check_interval": 30,
  "connection_timeout": 30,
  "max_retries": 3
}
```

### Параметры:
- **server**: Настройки HTTP сервера балансировщика
- **proxies**: Список SOCKS5 прокси серверов
- **health_check_interval**: Интервал проверки здоровья (секунды)
- **connection_timeout**: Таймаут соединения (секунды)
- **max_retries**: Максимум ошибок до отключения прокси

## 💻 Команды

```bash
# Запуск балансировщика (по умолчанию)
python main.py
python main.py start
python main.py start -v  # с подробным выводом
```

## 🌐 Использование

После запуска настройте приложения на использование HTTP прокси:
- **HTTP Proxy**: `http://localhost:8080`
- **HTTPS Proxy**: `http://localhost:8080`

### Примеры:
```bash
# curl
curl --proxy http://localhost:8080 http://example.com

# wget
wget --proxy=on --http-proxy=localhost:8080 http://example.com
```

## 🧪 Тестирование

```bash
# Базовое тестирование
python test_proxy.py

# Проверка модулей
python -c "from balancer import ProxyBalancer; print('OK')"
```

## 📊 Мониторинг

Балансировщик включает встроенную систему мониторинга:
- Автоматическая проверка здоровья прокси
- Статистика успешных/неудачных запросов
- Отслеживание времени отклика
- Автоматическое восстановление прокси

## 🏗️ Архитектура

### Модули:
- **ProxyBalancerServer**: Многопоточный HTTP сервер
- **ProxyHandler**: Обработка HTTP/HTTPS запросов
- **ProxyBalancer**: Управление пулом прокси
- **ProxyManager**: Утилиты управления
- **ProxyMonitor**: Система мониторинга

### Оптимизации:
- ThreadingHTTPServer для параллельной обработки
- Переиспользование HTTP сессий
- Эффективное туннелирование с select()
- Минимальные блокировки с RLock
- Случайное распределение нагрузки

## 🔧 Программное использование

```python
from balancer import ProxyBalancer, load_config

# Загрузка и запуск
config = load_config('config.json')
balancer = ProxyBalancer(config)
balancer.start()

# Статистика
stats = balancer.get_stats()
print(f"Available proxies: {stats['available']}")
```

## 📝 Лицензия

Смотрите файл LICENSE для деталей.

## 🤝 Вклад в проект

Проект имеет модульную архитектуру, что упрощает добавление новых функций:
- Новые алгоритмы балансировки в `balancer.py`
- Дополнительные протоколы в `handler.py`
- Расширенная статистика в `monitor.py`
- Утилиты управления в `utils.py`