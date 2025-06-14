import select
import socket
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict, Optional

import socks


class ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._handle_request()

    def do_POST(self):
        self._handle_request()

    def do_PUT(self):
        self._handle_request()

    def do_DELETE(self):
        self._handle_request()

    def do_PATCH(self):
        self._handle_request()

    def do_HEAD(self):
        self._handle_request()

    def do_CONNECT(self):
        self._handle_connect()

    def _handle_request(self):
        balancer = getattr(self.server, "proxy_balancer", None)
        if not balancer:
            self._send_error(503, "Service unavailable")
            return

        proxy = balancer.get_next_proxy()
        if not proxy:
            self._send_error(503, "No available proxies")
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b""

            headers = dict(self.headers)
            headers.pop("Host", None)
            headers.pop("Connection", None)
            headers.pop("Proxy-Connection", None)

            url = self._build_url()
            session = balancer.get_session(proxy)

            response = session.request(
                method=self.command,
                url=url,
                headers=headers,
                data=body,
                timeout=balancer.config["connection_timeout"],
                allow_redirects=False,
                verify=True,
                stream=True,
            )

            balancer.mark_success(proxy)

            self.send_response(response.status_code)
            for header, value in response.headers.items():
                if header.lower() not in ["connection", "transfer-encoding"]:
                    self.send_header(header, value)
            self.end_headers()

            for chunk in response.iter_content(8192):
                if chunk:
                    self.wfile.write(chunk)

        except Exception as e:
            import logging
            logging.error(f"Proxy error: {str(e)}")
            balancer.mark_failure(proxy)
            self._send_error(502, "Proxy error")

    def _build_url(self) -> str:
        if self.path.startswith("http"):
            return self.path

        host = self.headers.get("Host", "")
        if ":" in host:
            host, port_str = host.split(":", 1)
            port = int(port_str)
        else:
            port = 80

        scheme = "https" if port == 443 else "http"
        return (
            f"{scheme}://{host}:{port}{self.path}"
            if port not in [80, 443]
            else f"{scheme}://{host}{self.path}"
        )

    def _handle_connect(self) -> None:
        balancer = getattr(self.server, "proxy_balancer", None)
        if not balancer:
            self._send_error(503, "Service unavailable")
            return

        proxy = balancer.get_next_proxy()
        if not proxy:
            self._send_error(503, "No available proxies")
            return

        host_port = self.path
        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            port = int(port_str)
        else:
            host = host_port
            port = 443

        try:
            proxy_socket = self._create_proxy_socket(proxy, host, port)

            self.send_response(200, "Connection Established")
            self.end_headers()

            balancer.mark_success(proxy)
            self._tunnel_data(self.connection, proxy_socket)

        except Exception:
            balancer.mark_failure(proxy)
            self._send_error(502, "Tunnel failed")

    def _create_proxy_socket(self, proxy: Dict[str, Any], target_host: str, target_port: int) -> socket.socket:
        sock = socks.socksocket()
        sock.set_proxy(socks.SOCKS5, proxy["host"], proxy["port"])
        sock.settimeout(30)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.connect((target_host, target_port))
        return sock

    def _tunnel_data(self, client_socket: socket.socket, proxy_socket: socket.socket):
        try:
            client_socket.settimeout(30)
            proxy_socket.settimeout(30)
            
            import selectors
            sel = selectors.DefaultSelector()
            sel.register(client_socket, selectors.EVENT_READ)
            sel.register(proxy_socket, selectors.EVENT_READ)
            
            while True:
                events = sel.select(timeout=1.0)
                if not events:
                    continue
                    
                for key, _ in events:
                    if key.fileobj == client_socket:
                        try:
                            data = client_socket.recv(8192)
                            if not data:
                                return
                            proxy_socket.sendall(data)
                        except (ConnectionError, TimeoutError, socket.timeout) as e:
                            return
                        except Exception:
                            return
                            
                    if key.fileobj == proxy_socket:
                        try:
                            data = proxy_socket.recv(8192)
                            if not data:
                                return
                            client_socket.sendall(data)
                        except (ConnectionError, TimeoutError, socket.timeout) as e:
                            return
                        except Exception:
                            return
        finally:
            sel.unregister(client_socket)
            sel.unregister(proxy_socket)
            try:
                proxy_socket.close()
            except Exception:
                pass
            try:
                client_socket.close()
            except Exception:
                pass

    def _send_error(self, code: int, message: str):
        try:
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(message.encode())
        except BaseException:
            pass

    def log_message(self, format: str, *args: Any):
        pass
