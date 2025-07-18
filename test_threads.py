#!/usr/bin/env python3
"""
Скрипт для тестирования именования потоков в proxy load balancer
"""

import subprocess
import time
import sys

def check_thread_names():
    """Проверяет имена потоков в процессе proxy load balancer"""
    try:
        # Найти PID процесса python
        result = subprocess.run(['pgrep', '-f', 'main.py'], capture_output=True, text=True)
        if not result.stdout.strip():
            print("Процесс proxy load balancer не найден")
            return
        
        pid = result.stdout.strip().split('\n')[0]
        print(f"Найден процесс PID: {pid}")
        
        # Получить список потоков с именами
        result = subprocess.run(['ps', '-T', '-p', pid, '-o', 'pid,spid,comm'], 
                               capture_output=True, text=True)
        
        if result.returncode == 0:
            print("\nПотоки процесса:")
            print(result.stdout)
        else:
            print("Ошибка при получении списка потоков")
            
        # Также проверим /proc/PID/task/*/comm для более детальной информации
        print(f"\nДетальная информация о потоках из /proc/{pid}/task/:")
        result = subprocess.run(['find', f'/proc/{pid}/task/', '-name', 'comm', '-exec', 'cat', '{}', ';'], 
                               capture_output=True, text=True)
        if result.returncode == 0:
            thread_names = result.stdout.strip().split('\n')
            for i, name in enumerate(thread_names, 1):
                print(f"Поток {i}: {name}")
        
    except Exception as e:
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    print("Мониторинг потоков proxy load balancer...")
    print("Для выхода нажмите Ctrl+C")
    
    try:
        while True:
            check_thread_names()
            print("\n" + "="*50)
            time.sleep(10)
    except KeyboardInterrupt:
        print("\nЗавершение мониторинга")
