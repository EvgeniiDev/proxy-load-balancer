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
        balancer = getattr(self.server, "proxy_balancer", None)
        if not balancer:
            self.send_error(503, "Service unavailable")
            return

        proxy = balancer.get_next_proxy()
        if not proxy:
            self.send_error(503, "No available proxies")
            return

        remote_socket = None
        try:
            dest_host, dest_port_str = self.path.split(":", 1)
            dest_port = int(dest_port_str)

            remote_socket = socks.socksocket()
            remote_socket.set_proxy(
                proxy_type=socks.SOCKS5,
                addr=proxy["host"],
                port=proxy["port"],
                username=proxy.get("username"),
                password=proxy.get("password"),
            )

            remote_socket.connect((dest_host, dest_port))
            balancer.mark_success(proxy)
            self.send_response(200, "Connection Established")
            self.end_headers()

            client_socket = self.connection
            sockets = [client_socket, remote_socket]
            while True:
                readable, _, exceptional = select.select(sockets, [], sockets, 60)
                if exceptional:
                    break
                if not readable:
                    break

                for s in readable:
                    data = s.recv(8192)
                    if not data:
                        sockets.remove(s)
                        if s is client_socket:
                            if remote_socket in sockets:
                                sockets.remove(remote_socket)
                                remote_socket.close()
                        else:
                            if client_socket in sockets:
                                sockets.remove(client_socket)
                                client_socket.close()
                        if not sockets:
                            break
                        continue

                    if s is client_socket:
                        remote_socket.sendall(data)
                    else:
                        client_socket.sendall(data)
                if not sockets:
                    break
        except Exception as e:
            import logging

            logging.error(f"CONNECT error: {str(e)}")
            if proxy:
                balancer.mark_failure(proxy)
            self.send_error(502, "Proxy error")
        finally:
            if remote_socket:
                remote_socket.close()

    def _handle_request(self):
        balancer = getattr(self.server, "proxy_balancer", None)
        if not balancer:
            self.send_error(503, "Service unavailable")
            return
        proxy = balancer.get_next_proxy()
        if not proxy:
            self.send_error(503, "No available proxies")
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
            self.send_error(502, "Proxy error")

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
