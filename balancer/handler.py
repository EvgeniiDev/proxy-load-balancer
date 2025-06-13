import socket
import select
from http.server import BaseHTTPRequestHandler
from typing import Dict, Any, Optional
import socks


class ProxyHandler(BaseHTTPRequestHandler):
    """HTTP обработчик запросов с поддержкой прокси"""

    def do_GET(self) -> None:
        self._handle_request()

    def do_POST(self) -> None:
        self._handle_request()

    def do_PUT(self) -> None:
        self._handle_request()

    def do_DELETE(self) -> None:
        self._handle_request()

    def do_PATCH(self) -> None:
        self._handle_request()

    def do_HEAD(self) -> None:
        self._handle_request()

    def do_CONNECT(self) -> None:
        self._handle_connect()

    def _handle_request(self) -> None:
        """Обработка стандартных HTTP запросов"""
        balancer = getattr(self.server, 'proxy_balancer', None)
        if not balancer:
            self._send_error(503, "Service unavailable")
            return

        proxy: Optional[Dict[str, Any]] = balancer.get_next_proxy()
        if not proxy:
            self._send_error(503, "No available proxies")
            return

        try:
            content_length: int = int(self.headers.get('Content-Length', 0))
            body: bytes = self.rfile.read(
                content_length) if content_length > 0 else b''

            headers: Dict[str, str] = dict(self.headers)
            headers.pop('Host', None)
            headers.pop('Connection', None)
            headers.pop('Proxy-Connection', None)

            url: str = self._build_url()
            session = balancer.get_session(proxy)

            response = session.request(
                method=self.command,
                url=url,
                headers=headers,
                data=body,
                timeout=balancer.config['connection_timeout'],
                allow_redirects=False,
                verify=False
            )

            balancer.mark_success(proxy)

            self.send_response(response.status_code)
            for header, value in response.headers.items():
                if header.lower() not in ['connection', 'transfer-encoding']:
                    self.send_header(header, value)
            self.end_headers()

            if response.content:
                self.wfile.write(response.content)

        except Exception:
            balancer.mark_failure(proxy)
            self._send_error(502, "Proxy error")

    def _build_url(self) -> str:
        """Построение целевого URL из запроса"""
        if self.path.startswith('http'):
            return self.path

        host: str = self.headers.get('Host', '')
        if ':' in host:
            host, port_str = host.split(':', 1)
            port: int = int(port_str)
        else:
            port = 80

        scheme: str = 'https' if port == 443 else 'http'
        return f"{scheme}://{host}:{port}{self.path}" if port not in [80, 443] else f"{scheme}://{host}{self.path}"

    def _handle_connect(self) -> None:
        """Обработка CONNECT запросов для HTTPS туннелирования"""
        balancer = getattr(self.server, 'proxy_balancer', None)
        if not balancer:
            self._send_error(503, "Service unavailable")
            return

        proxy = balancer.get_next_proxy()
        if not proxy:
            self._send_error(503, "No available proxies")
            return

        host_port: str = self.path
        if ':' in host_port:
            host, port_str = host_port.rsplit(':', 1)
            port: int = int(port_str)
        else:
            host = host_port
            port = 443

        try:
            proxy_socket: socket.socket = self._create_proxy_socket(
                proxy, host, port)

            self.send_response(200, 'Connection Established')
            self.end_headers()

            balancer.mark_success(proxy)
            self._tunnel_data(self.connection, proxy_socket)

        except Exception:
            balancer.mark_failure(proxy)
            self._send_error(502, "Tunnel failed")

    def _create_proxy_socket(self, proxy: Dict[str, Any], target_host: str, target_port: int) -> socket.socket:
        """Создание SOCKS5 соединения через прокси"""
        sock: socket.socket = socks.socksocket()
        sock.set_proxy(socks.SOCKS5, proxy['host'], proxy['port'])
        sock.settimeout(30)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.connect((target_host, target_port))
        return sock

    def _tunnel_data(self, client_socket: socket.socket, proxy_socket: socket.socket) -> None:
        """Туннелирование данных между клиентом и прокси"""
        try:
            proxy_socket.settimeout(30)

            while True:
                ready, _, error = select.select([client_socket, proxy_socket], [],
                                                [client_socket, proxy_socket], 1.0)

                if error:
                    break

                if not ready:
                    continue

                if client_socket in ready:
                    try:
                        data = client_socket.recv(8192)
                        if not data:
                            break
                        proxy_socket.sendall(data)
                    except:
                        break

                if proxy_socket in ready:
                    try:
                        data = proxy_socket.recv(8192)
                        if not data:
                            break
                        client_socket.sendall(data)
                    except:
                        break

        finally:
            try:
                proxy_socket.close()
            except:
                pass

    def _send_error(self, code: int, message: str) -> None:
        """Отправка ошибки клиенту"""
        try:
            self.send_response(code)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(message.encode())
        except:
            pass

    def log_message(self, format: str, *args: Any) -> None:
        """Отключение логирования HTTP сервера"""
        pass
