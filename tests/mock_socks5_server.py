import logging
import json
import select
import socket
import struct
import threading
import time
from typing import Dict, List, Optional, Set


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
        self.requests_count = 0
        self.lock = threading.Lock()
        self.server_manager = None
        self.should_fail = False
        self.fixed_response_code = None
    
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
            
            # 5. Проксируем данные к реальному серверу
            success = self._proxy_data(client_socket, data)
            if success:
                with self.lock:
                    self.requests_count += 1
            
        except socket.error:
            pass
        finally:
            try:
                client_socket.close()
            except:
                pass
                
    def _proxy_data(self, client_socket: socket.socket, connection_data: bytes) -> bool:
        """Проксирует данные к реальному серверу. Возвращает True при успехе."""
        try:
            if self.fixed_response_code is not None:
                try:
                    client_socket.settimeout(5.0)
                    buffer = b""
                    while b"\r\n\r\n" not in buffer:
                        chunk = client_socket.recv(4096)
                        if not chunk:
                            break
                        buffer += chunk

                    code = int(self.fixed_response_code)
                    reason_map = {
                        200: 'OK',
                        201: 'Created',
                        204: 'No Content',
                        400: 'Bad Request',
                        404: 'Not Found',
                        429: 'Too Many Requests',
                        500: 'Internal Server Error',
                    }
                    reason = reason_map.get(code, 'OK' if 200 <= code < 300 else 'Error')
                    if code in (204,):
                        body = b''
                    elif code == -1:
                        client_socket.sendall(b"HTTP/1.1 MALFORMED RESPONSE\r\n\r\n")
                        return True
                    elif code == 429:
                        body = b''
                    else:
                        body = b'{"ok": true}'

                    headers = [
                        f"HTTP/1.1 {code} {reason}\r\n".encode('utf-8'),
                        b"Content-Type: application/json\r\n",
                        f"Content-Length: {len(body)}\r\n".encode('utf-8'),
                        b"Connection: close\r\n",
                        b"\r\n",
                    ]
                    for h in headers:
                        client_socket.sendall(h)
                    if body:
                        client_socket.sendall(body)
                    return True
                except Exception:
                    return False

            # Try to emulate basic httpbin.org behavior without external network
            try:
                client_socket.settimeout(5.0)
                buffer = b""
                # Read until end of headers
                while b"\r\n\r\n" not in buffer:
                    chunk = client_socket.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk
                if b"\r\n\r\n" in buffer:
                    header_bytes, body_remainder = buffer.split(b"\r\n\r\n", 1)
                    header_text = header_bytes.decode('iso-8859-1', errors='ignore')
                    lines = header_text.split("\r\n")
                    if lines:
                        request_line = lines[0]
                        parts = request_line.split()
                        if len(parts) >= 3:
                            method, url, _ = parts[0], parts[1], parts[2]
                            # Build headers dict
                            hdrs = {}
                            for ln in lines[1:]:
                                if ":" in ln:
                                    k, v = ln.split(":", 1)
                                    hdrs[k.strip().lower()] = v.strip()
                            content_length = int(hdrs.get('content-length', '0') or '0')
                            body = body_remainder
                            # Read remaining body if not fully read
                            if content_length > 0:
                                try:
                                    if content_length > 512 * 1024:
                                        client_socket.settimeout(20.0)
                                except Exception:
                                    pass
                                while len(body) < content_length:
                                    more = client_socket.recv(min(65536, content_length - len(body)))
                                    if not more:
                                        break
                                    body += more

                            status_code = 200
                            resp_obj = None
                            
                            # Parse query parameters
                            url_parts = url.split('?', 1)
                            base_url = url_parts[0]
                            query_args = {}
                            if len(url_parts) > 1:
                                query_string = url_parts[1]
                                for param in query_string.split('&'):
                                    if '=' in param:
                                        key, value = param.split('=', 1)
                                        query_args[key] = value
                                    else:
                                        query_args[param] = ""
                            
                            # Emulate /status/<code>
                            if '/status/' in base_url:
                                # Always emulate /status/429 and /status/200 for overload tests
                                if base_url.endswith('/429'):
                                    status_code = 429
                                elif base_url.endswith('/200'):
                                    status_code = 200
                                else:
                                    try:
                                        status_code = int(base_url.rsplit('/', 1)[-1])
                                    except Exception:
                                        status_code = 200
                            elif method == 'GET' and ('httpbin.org/get' in base_url or '/get' in base_url):
                                host_hdr = hdrs.get('host', '')
                                if url.startswith('http://') or url.startswith('https://'):
                                    full_url = url
                                else:
                                    scheme = 'https' if hdrs.get('x-forwarded-proto', '').lower() == 'https' else 'http'
                                    if host_hdr:
                                        full_url = f"{scheme}://{host_hdr}{url}"
                                    else:
                                        full_url = url
                                # Mock httpbin.org/get response structure
                                resp_obj = {
                                    "args": query_args,
                                    "headers": dict(hdrs),
                                    "origin": "127.0.0.1",
                                    "url": full_url
                                }
                            elif method == 'GET' and ('httpbin.org/headers' in base_url or '/headers' in base_url):
                                host_hdr = hdrs.get('host', '')
                                if url.startswith('http://') or url.startswith('https://'):
                                    full_url = url
                                else:
                                    scheme = 'https' if hdrs.get('x-forwarded-proto', '').lower() == 'https' else 'http'
                                    if host_hdr:
                                        full_url = f"{scheme}://{host_hdr}{url}"
                                    else:
                                        full_url = url
                                # Mock httpbin.org/headers response structure
                                resp_obj = {
                                    "headers": dict(hdrs)
                                }
                            elif method == 'GET' and ('httpbin.org/gzip' in base_url or '/gzip' in base_url):
                                host_hdr = hdrs.get('host', '')
                                if url.startswith('http://') or url.startswith('https://'):
                                    full_url = url
                                else:
                                    scheme = 'https' if hdrs.get('x-forwarded-proto', '').lower() == 'https' else 'http'
                                    if host_hdr:
                                        full_url = f"{scheme}://{host_hdr}{url}"
                                    else:
                                        full_url = url
                                # Mock httpbin.org/gzip response structure
                                resp_obj = {
                                    "args": query_args,
                                    "headers": dict(hdrs),
                                    "origin": "127.0.0.1",
                                    "url": full_url,
                                    "gzipped": True
                                }
                            elif method == 'GET' and ('httpbin.org/redirect' in base_url or '/redirect' in base_url):
                                # Simulate redirect response - just return the final GET response
                                host_hdr = hdrs.get('host', '')
                                if url.startswith('http://') or url.startswith('https://'):
                                    full_url = url
                                else:
                                    scheme = 'https' if hdrs.get('x-forwarded-proto', '').lower() == 'https' else 'http'
                                    if host_hdr:
                                        full_url = f"{scheme}://{host_hdr}{url}"
                                    else:
                                        full_url = url
                                # Mock the final GET endpoint response after redirect
                                final_url = f"{scheme}://{host_hdr}/get" if host_hdr else "/get"
                                resp_obj = {
                                    "args": query_args,
                                    "headers": dict(hdrs),
                                    "origin": "127.0.0.1",
                                    "url": final_url
                                }
                            elif method in ('POST', 'PUT', 'PATCH') and ((('httpbin.org/post' in base_url or '/post' in base_url) or ('httpbin.org/put' in base_url or '/put' in base_url) or ('httpbin.org/patch' in base_url or '/patch' in base_url))):
                                try:
                                    json_body = json.loads(body.decode('utf-8') or 'null')
                                except Exception:
                                    json_body = None
                                
                                host_hdr = hdrs.get('host', '')
                                if url.startswith('http://') or url.startswith('https://'):
                                    full_url = url
                                else:
                                    scheme = 'https' if hdrs.get('x-forwarded-proto', '').lower() == 'https' else 'http'
                                    if host_hdr:
                                        full_url = f"{scheme}://{host_hdr}{url}"
                                    else:
                                        full_url = url
                                
                                resp_obj = {
                                    "args": query_args,
                                    "data": body.decode('utf-8') if body else "",
                                    "files": {},
                                    "form": {},
                                    "headers": dict(hdrs),
                                    "json": json_body,
                                    "origin": "127.0.0.1",
                                    "url": full_url
                                }
                            elif method == 'DELETE' and ('httpbin.org/delete' in base_url or '/delete' in base_url):
                                host_hdr = hdrs.get('host', '')
                                if url.startswith('http://') or url.startswith('https://'):
                                    full_url = url
                                else:
                                    scheme = 'https' if hdrs.get('x-forwarded-proto', '').lower() == 'https' else 'http'
                                    if host_hdr:
                                        full_url = f"{scheme}://{host_hdr}{url}"
                                    else:
                                        full_url = url
                                
                                resp_obj = {
                                    "args": query_args,
                                    "data": "",
                                    "files": {},
                                    "form": {},
                                    "headers": dict(hdrs),
                                    "json": None,
                                    "origin": "127.0.0.1",
                                    "url": full_url
                                }
                            elif method == 'GET' and ('/delay/' in base_url or 'httpbin.org/delay/' in base_url):
                                host_hdr = hdrs.get('host', '')
                                scheme = 'https' if hdrs.get('x-forwarded-proto', '').lower() == 'https' else 'http'
                                try:
                                    delay_str = base_url.rsplit('/', 1)[-1]
                                    delay = float(delay_str)
                                except Exception:
                                    delay = 1.0
                                time.sleep(min(max(delay, 0.0), 15.0))
                                if url.startswith('http://') or url.startswith('https://'):
                                    full_url = url
                                else:
                                    if host_hdr:
                                        full_url = f"{scheme}://{host_hdr}{url}"
                                    else:
                                        full_url = url
                                resp_obj = {
                                    "args": query_args,
                                    "headers": dict(hdrs),
                                    "origin": "127.0.0.1",
                                    "url": full_url
                                }

                            # If status_code is 429, send that
                            if status_code == 429:
                                reason = 'Too Many Requests'
                                resp_body = b''
                            else:
                                reason = 'OK' if status_code == 200 else 'OK'
                                if resp_obj is None:
                                    resp_obj = {"ok": True}
                                resp_body = json.dumps(resp_obj).encode('utf-8')

                            resp_headers = [
                                f"HTTP/1.1 {status_code} {reason}\r\n".encode('utf-8'),
                                b"Content-Type: application/json\r\n",
                                f"Content-Length: {len(resp_body)}\r\n".encode('utf-8'),
                                b"Connection: close\r\n",
                                b"\r\n",
                            ]
                            try:
                                for h in resp_headers:
                                    client_socket.sendall(h)
                                if resp_body:
                                    client_socket.sendall(resp_body)
                            except Exception:
                                pass
                            return True
            except Exception:
                # If parsing/emulation fails, fall back to original behavior
                pass
            # Извлекаем информацию о целевом сервере из данных подключения SOCKS5
            if len(connection_data) < 6:
                return False
                
            cmd = connection_data[1]
            atyp = connection_data[3]
            
            if cmd != 0x01:  # Только CONNECT команда поддерживается
                return False
                
            if atyp == 0x01:  # IPv4
                target_ip = socket.inet_ntoa(connection_data[4:8])
                target_port = struct.unpack('>H', connection_data[8:10])[0]
            elif atyp == 0x03:  # Domain name
                domain_length = connection_data[4]
                target_ip = connection_data[5:5+domain_length].decode('utf-8')
                target_port = struct.unpack('>H', connection_data[5+domain_length:7+domain_length])[0]
            else:
                return False
                
            # Создаем соединение с реальным сервером
            target_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            target_socket.settimeout(1.0)
            
            try:
                target_socket.connect((target_ip, target_port))
                
                # Проксируем данные между клиентом и сервером
                client_socket.settimeout(30.0)
                
                # Простое проксирование данных
                sockets = [client_socket, target_socket]
                request_processed = False
                
                while sockets:
                    readable, _, exceptional = select.select(sockets, [], sockets, 1.0)
                    
                    if exceptional:
                        break
                        
                    for sock in readable:
                        try:
                            data = sock.recv(4096)
                            if not data:
                                # Connection closed
                                if sock in sockets:
                                    sockets.remove(sock)
                                    sock.close()
                                # Close the other socket too
                                other_sock = target_socket if sock == client_socket else client_socket
                                if other_sock in sockets:
                                    sockets.remove(other_sock)
                                    other_sock.close()
                                break
                            else:
                                # Forward data
                                other_sock = target_socket if sock == client_socket else client_socket
                                other_sock.sendall(data)
                                
                                # If data from client to server, mark as request processed
                                if sock == client_socket:
                                    request_processed = True
                        except socket.error:
                            # Error occurred, close both sockets
                            for s in sockets[:]:
                                try:
                                    s.close()
                                    sockets.remove(s)
                                except:
                                    pass
                            break
                            
                return request_processed
                            
            except Exception as e:
                logging.error(f"Error connecting to target server {target_ip}:{target_port}: {e}")
                return False
            finally:
                try:
                    target_socket.close()
                except:
                    pass
                    
        except Exception as e:
            logging.error(f"Error in proxy_data: {e}")
            return False
                
    def get_connection_count(self) -> int:
        """Возвращает количество обработанных запросов"""
        with self.lock:
            return max(self.connection_count, self.requests_count)
            
    def get_request_count(self) -> int:
        """Возвращает количество успешно обработанных HTTP запросов"""
        with self.lock:
            return self.requests_count
            
    def get_connections_log(self) -> List[Dict]:
        """Возвращает лог подключений"""
        with self.lock:
            return self.connections_log.copy()
            
    def reset_stats(self):
        """Сбрасывает статистику подключений"""
        with self.lock:
            self.connection_count = 0
            self.requests_count = 0
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
        
    def get_total_requests(self) -> int:
        """Возвращает общее количество HTTP запросов по всем серверам"""
        return sum(server.get_request_count() for server in self.servers)
        
    def get_server_stats(self) -> Dict[int, int]:
        """Возвращает статистику по каждому серверу (только HTTP запросы)"""
        stats = {}
        for server in self.servers:
            if server.port in self.stopped_ports:
                stats[server.port] = 0
            else:
                stats[server.port] = server.get_request_count()
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

    def set_fixed_response_codes(self, mapping: Dict[int, int]):
        """Устанавливает фиксированные HTTP коды, которые вернут серверы с указанными портами"""
        for server in self.servers:
            if server.port in mapping:
                server.fixed_response_code = mapping[server.port]
            else:
                server.fixed_response_code = None
    
    def stop_server(self, port: int):
        """Останавливает сервер с указанным портом"""
        for server in self.servers:
            if server.port == port:
                server.stop()
                self.stopped_ports.add(port)
                self.servers.remove(server)
                break
    
    def restart_server(self, port: int):
        """Перезапускает сервер с указанным портом"""
        # Если сервер был остановлен, создаем новый
        if port in self.stopped_ports:
            server = MockSocks5Server('127.0.0.1', port)
            server.start()
            self.servers.append(server)
            self.stopped_ports.remove(port)
    
    def reset_stats(self):
        """Сбрасывает статистику всех серверов"""
        for server in self.servers:
            server.reset_stats()
        # Сбрасываем счетчики для остановленных серверов
        self.stopped_ports.clear()
    
    def set_malformed_responses(self, port: int, enabled: bool):
        """Настраивает сервер на возврат некорректных ответов"""
        for server in self.servers:
            if server.port == port:
                if enabled:
                    server.fixed_response_code = -1  # Специальный код для некорректных ответов
                else:
                    server.fixed_response_code = None
                break
