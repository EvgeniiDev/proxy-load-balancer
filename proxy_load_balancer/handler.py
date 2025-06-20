import select
import socket
import logging
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict, Optional
import socks


class ProxyHandler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        """Override to handle broken pipe errors gracefully."""
        try:
            super().send_error(code, message, explain)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Client connection is broken, can't send response
            logging.warning(f"Cannot send error {code} to client: connection broken")

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
                    try:
                        data = s.recv(8192)
                        if not data:
                            sockets.remove(s)
                            if s is client_socket:
                                if remote_socket in sockets:
                                    sockets.remove(remote_socket)
                                    try:
                                        remote_socket.close()
                                    except:
                                        pass
                            else:
                                if client_socket in sockets:
                                    sockets.remove(client_socket)
                                    try:
                                        client_socket.close()
                                    except:
                                        pass
                            if not sockets:
                                break
                            continue

                        if s is client_socket:
                            try:
                                remote_socket.sendall(data)
                            except (ConnectionResetError, BrokenPipeError):
                                # Remote connection lost, remove it
                                if remote_socket in sockets:
                                    sockets.remove(remote_socket)
                                    try:
                                        remote_socket.close()
                                    except:
                                        pass
                                break
                        else:
                            try:
                                client_socket.sendall(data)
                            except (ConnectionResetError, BrokenPipeError):
                                # Client connection lost, remove it
                                if client_socket in sockets:
                                    sockets.remove(client_socket)
                                    try:
                                        client_socket.close()
                                    except:
                                        pass
                                break
                    except (ConnectionResetError, BrokenPipeError):
                        # Connection reset during recv, remove the socket
                        if s in sockets:
                            sockets.remove(s)
                            try:
                                s.close()
                            except:
                                pass
                        if s is client_socket and remote_socket in sockets:
                            sockets.remove(remote_socket)
                            try:
                                remote_socket.close()
                            except:
                                pass
                        elif s is remote_socket and client_socket in sockets:
                            sockets.remove(client_socket)
                            try:
                                client_socket.close()
                            except:
                                pass
                        break
                if not sockets:
                    break
        except Exception as e:
            logging.error(f"CONNECT error: {str(e)}")
            if proxy:
                balancer.mark_failure(proxy)
            # Only send error if the connection is still alive
            try:
                self.send_error(502, "Proxy error")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # Client connection is already broken, can't send error response
                logging.warning("Client connection broken, unable to send error response")
        finally:
            if remote_socket:
                try:
                    remote_socket.close()
                except:
                    pass

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
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        # Client disconnected during data transfer
                        logging.warning("Client disconnected during data transfer")
                        break
        except Exception as e:
            logging.error(f"Proxy error: {str(e)}")
            balancer.mark_failure(proxy)
            # Only send error if the connection is still alive
            try:
                self.send_error(502, "Proxy error")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # Client connection is already broken, can't send error response
                logging.warning("Client connection broken, unable to send error response")

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
