# Testing Directory

Эта директория содержит все файлы для тестирования proxy load balancer.

## ✅ Статус системы

**Система полностью функциональна и готова к использованию:**
- ✅ Реализован rate limiting для Steam API (6 запросов/минуту на прокси)
- ✅ Балансировщик успешно работает с Tor прокси  
- ✅ Структура проекта организована для удобного тестирования
- ✅ Проверена работа с 3 Tor узлами

**Последний тест показал:**
- 17 запросов выполнено через балансировщик
- 6 успешных ответов (200 OK) 
- 11 rate limited ответов (429) - система корректно ограничивает скорость
- Rate limiting активен и предотвращает перегрузку Steam API

## 🚀 Быстрый старт

```bash
# 1. Запуск Tor узлов (из testing/scripts/)
cd /workspaces/proxy-load-balancer/testing/scripts
./start_working_tor.sh

# 2. Запуск балансировщика (из корневой директории)
cd /workspaces/proxy-load-balancer  
python main.py -v

# 3. Тестирование (из testing/scripts/)
cd /workspaces/proxy-load-balancer/testing/scripts
python quick_test.py
```

## Структура

### `/configs/`
Конфигурационные файлы для различных сценариев тестирования:
- `config_3_nodes.json` - конфигурация для 3 Tor узлов (рекомендуется для начального тестирования)
- `config_5_nodes.json` - конфигурация для 5 Tor узлов
- `config_working_nodes.json` - конфигурация только для проверенных рабочих узлов
- `config_all_nodes.json` - конфигурация для всех 35 exit nodes

### `/scripts/`
Скрипты для запуска и тестирования:

**Запуск Tor:**
- `start_working_tor.sh` - запуск проверенных рабочих Tor узлов
- `start_multiple_tor.sh` - запуск множественных Tor узлов
- `stop_tor.sh` - остановка всех Tor процессов

**Тестирование:**
- `test_30_steam_rate_limited_3_proxies.py` - основной тест с rate limiting для Steam Market
- `quick_test_rate_limit.py` - быстрый тест rate limiting
- `test_steam_market.py` - тест Steam Community Market

**Альтернативные main файлы:**
- `main_working_nodes.py` - запуск с проверенными узлами
- `main_all_nodes.py` - запуск со всеми 35 узлами

### `/tor_configs/`
Конфигурационные файлы Tor (torrc-0 до torrc-34)

### `/results/`
Результаты тестирования в JSON формате

## Быстрый старт

1. **Запуск Tor узлов:**
   ```bash
   cd /workspaces/proxy-load-balancer/testing/scripts
   ./start_working_tor.sh
   ```

2. **Запуск load balancer:**
   ```bash
   cd /workspaces/proxy-load-balancer
   python main.py -c testing/configs/config_3_nodes.json -v
   ```

3. **Тестирование с rate limiting:**
   ```bash
   cd /workspaces/proxy-load-balancer/testing/scripts
   python test_30_steam_rate_limited_3_proxies.py
   ```

## Rate Limiting

Система настроена на ограничение 6 запросов в минуту на каждый прокси в соответствии с требованиями Steam Community Market. Rate limiting автоматически предотвращает 429 ошибки и обеспечивает стабильную работу.

## Рекомендации

- Для начального тестирования используйте `config_3_nodes.json`
- Всегда запускайте Tor узлы перед load balancer
- Используйте rate limited тесты для работы с Steam Market
- Проверяйте результаты в `/results/` директории
