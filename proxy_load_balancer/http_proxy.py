import socket
import select
import threading
import logging
import ssl
from typing import Dict, Any, Optional, Tuple
import time

from .proxy_stats import ProxyStats
from .utils import ProxyManager


class HTTPProxy:
    def __init__(self, config: Dict[str, Any], balancer=None):
        self.config = config
        self.balancer = balancer
        self.server_socket = None
        self.running = False
        self.threads = []
        self.logger = logging.getLogger("http_proxy")
        self._setup_logger()
        self._setup_ssl_context()

    def _setup_ssl_context(self):
        self.ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.ssl_context.load_cert_chain(
            certfile=self.config.get('ssl_cert', 'cert.pem'),
            keyfile=self.config.get('ssl_key', 'key.pem')
        )
        self.ssl_context.set_alpn_protocols(['http/1.1'])

    def _setup_logger(self):
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

    def start(self):
        server_config = self.config.get('server', {})
        host = server_config.get('host', '0.0.0.0')
        port = server_config.get('port', 8080)

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((host, port))
        self.server_socket.listen(5)

        self.running = True
        self.logger.info(f"HTTP Proxy listening on {host}:{port}")

        while self.running:
            try:
                client_socket, addr = self.server_socket.accept()
                thread = threading.Thread(
                    target=self._handle_client,
                    args=(client_socket, addr),
                    daemon=True
                )
                thread.start()
                self.threads.append(thread)
            except OSError:
                if self.running:
                    self.logger.error("Error accepting connection")
                break

    def stop(self):
        self.running = False
        if self.server_socket:
            self.server_socket.close()
        
        for thread in self.threads:
            thread.join(timeout=1)

    def _handle_client(self, client_socket: socket.socket, addr: Tuple[str, int]):
        start_time = time.time()
        method = "UNKNOWN"
        host = "unknown"
        
        try:
            request_line = self._read_line(client_socket)
            if not request_line:
                return

            parts = request_line.split(' ')
            if len(parts) < 3:
                self._send_error(client_socket, 400, "Bad Request")
                return

            method, url, version = parts[0], parts[1], parts[2]
            
            headers = {}
            while True:
                header_line = self._read_line(client_socket)
                if not header_line:
                    break
                if ':' in header_line:
                    key, value = header_line.split(':', 1)
                    headers[key.strip().lower()] = value.strip()

            if method == 'CONNECT':
                host = url
                self._handle_connect(client_socket, host, headers)
            else:
                self._handle_http_request(client_socket, method, url, version, headers)

        except Exception as e:
            self.logger.error(f"Error handling client {addr}: {e}")
        finally:
            try:
                client_socket.close()
            except:
                pass

    def _read_line(self, socket: socket.socket) -> Optional[str]:
        try:
            line = b""
            while b"\r\n" not in line:
                chunk = socket.recv(1)
                if not chunk:
                    return None
                line += chunk
            return line.decode('utf-8').strip()
        except:
            return None

    def _handle_connect(self, client_socket: socket.socket, host_port: str, headers: Dict[str, str]):
        start_time = time.time()
        status_code = 0
        
        try:
            if ':' in host_port:
                target_host, target_port = host_port.rsplit(':', 1)
                target_port = int(target_port)
            else:
                target_host = host_port
                target_port = 443

            self.logger.info(f"CONNECT request to {target_host}:{target_port}")

            response = "HTTP/1.1 200 Connection Established\r\n\r\n"
            client_socket.send(response.encode('utf-8'))

            if target_port == 443:
                status_code = self._handle_ssl_termination(client_socket, target_host, target_port)
            else:
                status_code = self._handle_plain_tunnel(client_socket, target_host, target_port)

        except Exception as e:
            self.logger.error(f"Error in CONNECT handler: {e}")
            status_code = 500
            self._send_error(client_socket, 500, "Internal Server Error")
        finally:
            response_time = time.time() - start_time
            self.logger.info(f"CONNECT {target_host}:{target_port} completed with status {status_code} in {response_time:.2f}s")

    def _connect_through_proxy(self, target_host: str, target_port: int, proxy: Dict[str, Any]) -> Optional[socket.socket]:
        try:
            import socks
            sock = socks.socksocket()
            sock.set_proxy(
                proxy_type=socks.SOCKS5,
                addr=proxy["host"],
                port=proxy["port"],
                username=proxy.get("username"),
                password=proxy.get("password"),
            )
            sock.settimeout(10)
            sock.connect((target_host, target_port))
            return sock
        except Exception as e:
            self.logger.error(f"Error connecting through proxy {proxy['host']}:{proxy['port']}: {e}")
            return None

    def _tunnel_data(self, client_socket: socket.socket, target_socket: socket.socket):
        try:
            sockets = [client_socket, target_socket]
            
            while True:
                ready, _, error = select.select(sockets, [], sockets, 60)
                
                if error:
                    break

                if not ready:
                    break

                for sock in ready:
                    try:
                        data = sock.recv(4096)
                        if not data:
                            return
                        
                        if sock is client_socket:
                            target_socket.send(data)
                        else:
                            client_socket.send(data)
                    except:
                        return

        except Exception as e:
            self.logger.error(f"Error tunneling data: {e}")
        finally:
            try:
                target_socket.close()
            except:
                pass

    def _handle_http_request(self, client_socket: socket.socket, method: str, url: str, version: str, headers: Dict[str, str]):
        """Обрабатывает HTTP запросы (не CONNECT)"""
        start_time = time.time()
        status_code = 0
        
        try:
            # Читаем тело запроса если есть
            body = b""
            content_length = headers.get('content-length')
            if content_length:
                try:
                    body_length = int(content_length)
                    body = client_socket.recv(body_length)
                except:
                    pass
            
            # Парсим URL
            from urllib.parse import urlparse
            parsed_url = urlparse(url)
            
            if parsed_url.hostname:
                # Полный URL
                target_host = parsed_url.hostname
                target_port = parsed_url.port or (443 if parsed_url.scheme == 'https' else 80)
                path = parsed_url.path or '/'
                if parsed_url.query:
                    path += '?' + parsed_url.query
            else:
                # Относительный URL, используем Host header
                host_header = headers.get('host', '').strip()
                if ':' in host_header:
                    target_host, port_str = host_header.rsplit(':', 1)
                    target_port = int(port_str)
                else:
                    target_host = host_header
                    target_port = 80
                path = url
            
            self.logger.info(f"HTTP request: {method} {target_host}:{target_port}{path}")
            
            # Пробуем переслать запрос через доступные прокси
            response = self._forward_http_request(method, target_host, target_port, path, headers, body)
            
            if response:
                status_code, response_headers, response_body = response
                self._send_http_response_plain(client_socket, response)
            else:
                status_code = 503
                self._send_error(client_socket, 503, "Service Unavailable")
                
        except Exception as e:
            self.logger.error(f"Error in HTTP request handler: {e}")
            status_code = 500
            self._send_error(client_socket, 500, "Internal Server Error")
        finally:
            response_time = time.time() - start_time
            self.logger.info(f"HTTP {method} {target_host}:{target_port} completed with status {status_code} in {response_time:.2f}s")

    def _send_error(self, client_socket: socket.socket, status_code: int, message: str):
        try:
            response = f"HTTP/1.1 {status_code} {message}\r\n"
            response += "Content-Type: text/html\r\n"
            response += "Connection: close\r\n\r\n"
            response += f"<html><body><h1>{status_code} {message}</h1></body></html>"
            
            client_socket.send(response.encode('utf-8'))
        except Exception as e:
            self.logger.error(f"Error sending error response: {e}")

    def _handle_ssl_termination(self, client_socket: socket.socket, target_host: str, target_port: int) -> int:
        try:
            ssl_socket = self.ssl_context.wrap_socket(client_socket, server_side=True)
            self.logger.info(f"SSL connection established for {target_host}:{target_port}")
            
            last_status = 200
            while True:
                request_data = self._read_http_request(ssl_socket)
                if not request_data:
                    break
                    
                method, path, headers, body = request_data
                self.logger.info(f"SSL terminated request: {method} {path}")
                
                if not headers.get('host'):
                    headers['host'] = target_host
                
                response = self._forward_http_request(method, target_host, target_port, path, headers, body)
                
                if response:
                    status_code, response_headers, response_body = response
                    last_status = status_code
                    self._send_http_response(ssl_socket, response)
                else:
                    last_status = 502
                    self._send_http_error(ssl_socket, 502, "Bad Gateway")
                    break
            
            return last_status
                    
        except ssl.SSLError as e:
            self.logger.error(f"SSL error in termination: {e}")
            return 500
        except Exception as e:
            self.logger.error(f"Error in SSL termination: {e}")
            return 500
        finally:
            try:
                ssl_socket.close()
            except:
                pass

    def _handle_plain_tunnel(self, client_socket: socket.socket, target_host: str, target_port: int) -> int:
        target_socket = None
        proxy = None
        last_proxy = None
        tried = set()
        attempts = 0
        final_status = 502

        self.logger.info(f"Starting plain tunnel to {target_host}:{target_port}")

        while attempts < 20:
            if self.balancer:
                proxy = self.balancer.get_next_proxy()
            
            if not proxy:
                self.logger.warning(f"No proxy available for {target_host}:{target_port}")
                break

            key = ProxyManager.get_proxy_key(proxy)
            if key in tried:
                self.logger.debug(f"Proxy {key} already tried for {target_host}:{target_port}")
                break
            
            tried.add(key)
            last_proxy = proxy

            self.logger.info(f"Attempting connection through proxy {key} to {target_host}:{target_port}")
            target_socket = self._connect_through_proxy(target_host, target_port, proxy)
            
            if target_socket:
                if self.balancer:
                    self.logger.info(f"Plain tunnel: Connection successful for proxy {key}")
                    # Для plain tunnel считаем успехом само подключение
                    self.balancer.mark_success(proxy)
                    final_status = 200
                break
            else:
                if self.balancer:
                    self.logger.info(f"Plain tunnel: Connection failed for proxy {key}")
                    self.balancer.mark_failure(proxy)
                attempts += 1

        if target_socket:
            # Запускаем туннелирование данных
            try:
                self._tunnel_data(client_socket, target_socket)
                return 200
            except Exception as e:
                # Если туннелирование провалилось, не нужно дважды отмечать ошибку
                self.logger.error(f"Plain tunnel: Data tunneling failed: {e}")
                return 502
        else:
            self.logger.error(f"Failed to connect to {target_host}:{target_port} through any proxy")
            return 502

    def _read_http_request(self, ssl_socket) -> Optional[Tuple[str, str, Dict[str, str], bytes]]:
        try:
            request_line = b""
            while b"\r\n" not in request_line:
                chunk = ssl_socket.recv(1)
                if not chunk:
                    return None
                request_line += chunk
            
            request_line = request_line.decode('utf-8').strip()
            if not request_line:
                return None
                
            parts = request_line.split(' ')
            if len(parts) < 3:
                return None
                
            method, path, version = parts[0], parts[1], parts[2]
            
            headers = {}
            while True:
                header_line = b""
                while b"\r\n" not in header_line:
                    chunk = ssl_socket.recv(1)
                    if not chunk:
                        return None
                    header_line += chunk
                
                header_line = header_line.decode('utf-8').strip()
                if not header_line:
                    break
                    
                if ':' in header_line:
                    key, value = header_line.split(':', 1)
                    headers[key.strip().lower()] = value.strip()
            
            body = b""
            content_length = headers.get('content-length')
            if content_length:
                content_length = int(content_length)
                while len(body) < content_length:
                    chunk = ssl_socket.recv(min(4096, content_length - len(body)))
                    if not chunk:
                        break
                    body += chunk
            
            return method, path, headers, body
            
        except Exception as e:
            self.logger.error(f"Error reading HTTP request: {e}")
            return None

    def _forward_http_request(self, method: str, host: str, port: int, path: str, 
                            headers: Dict[str, str], body: bytes) -> Optional[Tuple[int, Dict[str, str], bytes]]:
        proxy = None
        last_proxy = None
        tried = set()
        attempts = 0

        while attempts < 20:
            if self.balancer:
                proxy = self.balancer.get_next_proxy()
            
            if not proxy:
                break

            key = ProxyManager.get_proxy_key(proxy)
            if key in tried:
                break
            
            tried.add(key)
            last_proxy = proxy

            response = self._send_request_through_proxy(method, host, port, path, headers, body, proxy)
            
            if response:
                status_code, response_headers, response_body = response
                
                if self.balancer:
                    if status_code == 429:
                        self.logger.info(f"Marking 429 response for proxy {key}")
                        self.balancer.mark_429_response(proxy)
                        self.balancer.mark_overloaded(proxy)
                        attempts += 1
                        continue
                    elif 200 <= status_code < 400:
                        self.logger.info(f"Marking success for proxy {key}, status={status_code}")
                        self.balancer.mark_success(proxy)
                    else:
                        self.logger.info(f"Marking failure for proxy {key}, status={status_code}")
                        self.balancer.mark_failure(proxy)
                
                return response
            else:
                if self.balancer:
                    self.logger.info(f"Forward request failed for proxy {key}")
                    self.balancer.mark_failure(proxy)
                attempts += 1

        if last_proxy and self.balancer:
            self.balancer.mark_failure(last_proxy)

        return None

    def _send_request_through_proxy(self, method: str, host: str, port: int, path: str, 
                                  headers: Dict[str, str], body: bytes, proxy: Dict[str, Any]) -> Optional[Tuple[int, Dict[str, str], bytes]]:
        try:
            import socks
            sock = socks.socksocket()
            sock.set_proxy(
                proxy_type=socks.SOCKS5,
                addr=proxy["host"],
                port=proxy["port"],
                username=proxy.get("username"),
                password=proxy.get("password"),
            )
            sock.settimeout(10)
            sock.connect((host, port))
            
            if port == 443:
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                sock = context.wrap_socket(sock, server_hostname=host)
            
            request_data = f"{method} {path} HTTP/1.1\r\n"
            for key, value in headers.items():
                request_data += f"{key}: {value}\r\n"
            request_data += "\r\n"
            
            sock.send(request_data.encode('utf-8'))
            if body:
                sock.send(body)
            
            response_data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response_data += chunk
                
                if b"\r\n\r\n" in response_data:
                    header_end = response_data.find(b"\r\n\r\n") + 4
                    headers_part = response_data[:header_end].decode('utf-8')
                    body_part = response_data[header_end:]
                    
                    lines = headers_part.strip().split('\r\n')
                    status_line = lines[0]
                    status_code = int(status_line.split()[1])
                    
                    response_headers = {}
                    for line in lines[1:]:
                        if ':' in line:
                            key, value = line.split(':', 1)
                            response_headers[key.strip().lower()] = value.strip()
                    
                    content_length = response_headers.get('content-length')
                    if content_length:
                        content_length = int(content_length)
                        while len(body_part) < content_length:
                            chunk = sock.recv(min(4096, content_length - len(body_part)))
                            if not chunk:
                                break
                            body_part += chunk
                    elif response_headers.get('transfer-encoding') == 'chunked':
                        body_part = self._read_chunked_response(sock, body_part)
                    
                    sock.close()
                    return status_code, response_headers, body_part
            
            sock.close()
            return None
            
        except Exception as e:
            self.logger.error(f"Error sending request through proxy: {e}")
            return None

    def _read_chunked_response(self, sock, initial_data: bytes) -> bytes:
        body = initial_data
        remaining_data = initial_data
        
        while True:
            while b'\r\n' not in remaining_data:
                chunk = sock.recv(4096)
                if not chunk:
                    return body
                remaining_data += chunk
                
            chunk_line_end = remaining_data.find(b'\r\n')
            chunk_size_line = remaining_data[:chunk_line_end]
            remaining_data = remaining_data[chunk_line_end + 2:]
            
            try:
                chunk_size = int(chunk_size_line.decode('utf-8'), 16)
            except ValueError:
                break
                
            if chunk_size == 0:
                break
                
            while len(remaining_data) < chunk_size + 2:
                chunk = sock.recv(4096)
                if not chunk:
                    return body
                remaining_data += chunk
                
            chunk_data = remaining_data[:chunk_size]
            body += chunk_data
            remaining_data = remaining_data[chunk_size + 2:]
            
        return body

    def _send_http_response_plain(self, client_socket, response: Tuple[int, Dict[str, str], bytes]):
        """Отправляет HTTP ответ через обычный (не SSL) сокет"""
        try:
            status_code, headers, body = response
            
            status_text = "OK" if status_code == 200 else "Error"
            response_line = f"HTTP/1.1 {status_code} {status_text}\r\n"
            client_socket.send(response_line.encode('utf-8'))
            
            for key, value in headers.items():
                header_line = f"{key}: {value}\r\n"
                client_socket.send(header_line.encode('utf-8'))
            
            client_socket.send(b"\r\n")
            
            if body:
                client_socket.send(body)
                
        except Exception as e:
            self.logger.error(f"Error sending HTTP response: {e}")

    def _send_http_response(self, ssl_socket, response: Tuple[int, Dict[str, str], bytes]):
        try:
            status_code, headers, body = response
            
            status_text = "OK" if status_code == 200 else "Error"
            response_line = f"HTTP/1.1 {status_code} {status_text}\r\n"
            ssl_socket.send(response_line.encode('utf-8'))
            
            for key, value in headers.items():
                header_line = f"{key}: {value}\r\n"
                ssl_socket.send(header_line.encode('utf-8'))
            
            ssl_socket.send(b"\r\n")
            
            if body:
                ssl_socket.send(body)
                
        except Exception as e:
            self.logger.error(f"Error sending HTTP response: {e}")

    def _send_http_error(self, ssl_socket, status_code: int, message: str):
        try:
            response = f"HTTP/1.1 {status_code} {message}\r\n"
            response += "Content-Type: text/html\r\n"
            response += "Connection: close\r\n\r\n"
            response += f"<html><body><h1>{status_code} {message}</h1></body></html>"
            
            ssl_socket.send(response.encode('utf-8'))
        except Exception as e:
            self.logger.error(f"Error sending HTTP error response: {e}")
