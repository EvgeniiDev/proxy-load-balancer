import socket
import threading
import time
from typing import Dict, List, Optional, Set
import struct


class MockSocks5Server:
    """Mock SOCKS5 сервер для тестирования"""
    
    def __init__(self, host: str = '127.0.0.1', port: int = 0):
        self.host = host
        self.port = port
        self.server_socket = None
        self.running = False
        self.thread = None
        self.connections_log = []
        self.connection_count = 0
        self.lock = threading.Lock()
        self.server_manager = None  # Reference to the manager that created this server
        self.should_fail = False  # Flag to simulate failure condition
        
    def start(self):
        """Запускает mock SOCKS5 сервер"""
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        
        # Получаем фактический порт если был указан 0
        if self.port == 0:
            self.port = self.server_socket.getsockname()[1]
            
        self.server_socket.listen(5)
        self.running = True
        
        self.thread = threading.Thread(target=self._run_server, daemon=True)
        self.thread.start()
        
        # Даем серверу время на запуск
        time.sleep(0.1)
        
    def stop(self):
        """Останавливает сервер"""
        self.running = False
        # Notify manager if it exists
        if self.server_manager:
            self.server_manager.mark_server_stopped(self)
        if self.server_socket:
            self.server_socket.close()
            self.server_socket = None
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
            
    def restart(self):
        """Перезапускает сервер после его остановки"""
        # Make sure server is stopped
        self.stop()
        # Create a new server socket
        self.server_socket = None
        # Reset connection stats during restart
        self.reset_stats()
        # Start again
        self.start()
            
    def _run_server(self):
        """Основной цикл сервера"""
        while self.running:
            try:
                if self.server_socket is None:
                    # If socket is None, sleep briefly and check if we should continue running
                    time.sleep(0.1)
                    continue
                
                client_socket, client_address = self.server_socket.accept()
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(client_socket, client_address),
                    daemon=True
                )
                client_thread.start()
            except socket.error:
                if self.running:
                    continue
                break
                
    def _handle_client(self, client_socket: socket.socket, client_address):
        """Обрабатывает подключение клиента"""
        try:
            # Record connection before any socket operations that might fail
            with self.lock:
                # Important: increment the connection counter to track activity
                self.connection_count += 1
                self.connections_log.append({
                    'timestamp': time.time(),
                    'client_address': client_address,
                    'connection_id': self.connection_count
                })
            
            # SOCKS5 handshake
            # 1. Читаем приветствие клиента
            client_socket.settimeout(2.0)  # Timeout for receiving data
            data = client_socket.recv(1024)
            if not data or len(data) < 3:
                return
                
            # Проверяем, что это SOCKS5
            if data[0] != 0x05:
                return
                
            # 2. Отвечаем, что аутентификация не требуется
            client_socket.sendall(b'\x05\x00')
            
            # 3. Читаем запрос на подключение
            data = client_socket.recv(1024)
            if not data or len(data) < 4:
                return
                
            # Optionally fail the connection if should_fail is True
            if self.should_fail:
                response = b'\x05\x01\x00\x01'  # General SOCKS server failure
                response += socket.inet_aton('0.0.0.0')  # Address
                response += struct.pack('>H', 0)  # Port
                client_socket.sendall(response)
                return
                
            # 4. Отвечаем успехом (имитируем успешное подключение)
            response = b'\x05\x00\x00\x01'  # Success, IPv4
            response += socket.inet_aton('127.0.0.1')  # Bind address
            response += struct.pack('>H', 8080)  # Bind port
            client_socket.sendall(response)
            
            # 5. Имитируем проксирование (для тестов имитируем успешную передачу данных)
            # Wait longer for data in test environment
            client_socket.settimeout(5.0)
            try:
                client_data = client_socket.recv(8192)
                if client_data:
                    # For HTTP requests, generate a valid HTTP response
                    if client_data.startswith(b'GET') or client_data.startswith(b'POST'):
                        mock_response = b'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nOK'
                    else:
                        # For other protocols, just echo something back
                        mock_response = b'OK'
                    client_socket.sendall(mock_response)
            except socket.timeout:
                # Timeout is normal, just continue
                pass
            except Exception as e:
                import logging
                logging.error(f"Error handling client data: {e}")
            
            # Give client time to receive data before closing
            time.sleep(0.5)
            
        except socket.error:
            pass
        finally:
            try:
                client_socket.close()
            except:
                pass
                
    def get_connection_count(self) -> int:
        """Возвращает количество подключений"""
        with self.lock:
            return self.connection_count
            
    def get_connections_log(self) -> List[Dict]:
        """Возвращает лог подключений"""
        with self.lock:
            return self.connections_log.copy()
            
    def reset_stats(self):
        """Сбрасывает статистику подключений"""
        with self.lock:
            self.connection_count = 0
            self.connections_log.clear()


class MockSocks5ServerManager:
    """Менеджер для управления несколькими mock серверами"""
    
    def __init__(self):
        self.servers: List[MockSocks5Server] = []
        self.stopped_ports = set()
        
    def create_servers(self, count: int, base_port: int = 0) -> List[MockSocks5Server]:
        """Создает и запускает несколько серверов"""
        servers = []
        for i in range(count):
            port = base_port + i if base_port > 0 else 0
            server = MockSocks5Server('127.0.0.1', port)
            server.start()
            servers.append(server)
            self.servers.append(server)
        return servers
        
    def stop_all(self):
        """Останавливает все серверы"""
        for server in self.servers:
            if hasattr(server, 'port'):
                self.stopped_ports.add(server.port)
            server.stop()
        self.servers.clear()
        self.stopped_ports.clear()
        
    def get_total_connections(self) -> int:
        """Возвращает общее количество подключений по всем серверам"""
        return sum(server.get_connection_count() for server in self.servers)
        
    def get_server_stats(self) -> Dict[int, int]:
        """Возвращает статистику по каждому серверу"""
        stats = {}
        for server in self.servers:
            if server.port in self.stopped_ports:
                stats[server.port] = 0
            else:
                stats[server.port] = server.get_connection_count()
        # For stopped servers that are not in self.servers anymore
        for port in self.stopped_ports:
            if port not in stats:
                stats[port] = 0
        return stats
        
    def reset_all_stats(self):
        """Сбрасывает статистику всех серверов"""
        for server in self.servers:
            server.reset_stats()
            
    def mark_server_stopped(self, server: MockSocks5Server):
        """Marks a server as stopped in the internal tracking"""
        if hasattr(server, 'port'):
            self.stopped_ports.add(server.port)
        if server in self.servers:
            self.servers.remove(server)
