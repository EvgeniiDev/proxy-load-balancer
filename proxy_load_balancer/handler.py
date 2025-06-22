import select
import socket
import logging
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict, Optional
import socks


class ProxyHandler(BaseHTTPRequestHandler):
    # Disable server signature to avoid exposing server information
    server_version = ""
    sys_version = ""
    
    def version_string(self):
        """Return empty version string to avoid server identification"""
        return ""
    
    def log_message(self, format, *args):
        """Disable default request logging to avoid traces"""
        pass
    
    def send_error(self, code, message=None, explain=None):
        """Override to send generic error messages that don't reveal proxy usage."""
        try:
            # Override message and explanation to avoid revealing proxy information
            if code == 502:
                message = "Bad Gateway"
                explain = "The server encountered a temporary error and could not complete your request."
            elif code == 503:
                message = "Service Unavailable"
                explain = "The server is temporarily unable to service your request."
            elif code == 504:
                message = "Gateway Timeout"
                explain = "The server did not receive a timely response."
            
            super().send_error(code, message, explain)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Client connection is broken, can't send response
            pass

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
            if proxy:
                balancer.mark_failure(proxy)
            # Send generic error without revealing proxy usage
            try:
                self.send_error(502, "Bad Gateway")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # Client connection is already broken, can't send error response
                pass
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
        
        session = None
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b""
            headers = dict(self.headers)
            
            proxy_headers_to_remove = [
                "Proxy-Connection", "Proxy-Authorization", "Via", 
                "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
                "X-Real-IP", "X-Proxy-Authorization", "Proxy-Authenticate",
                "X-Forwarded-Server", "X-Forwarded-Port", "Forwarded"
            ]
            
            for header in proxy_headers_to_remove:
                headers.pop(header, None)
                headers.pop(header.lower(), None)
            
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
                header_lower = header.lower()
                if header_lower not in [
                    "connection", "transfer-encoding", "via", "x-forwarded-for",
                    "x-forwarded-host", "x-forwarded-proto", "x-real-ip",
                    "proxy-connection", "proxy-authenticate", "server"
                ]:
                    self.send_header(header, value)
            
            self.end_headers()
            for chunk in response.iter_content(8192):
                if chunk:
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        break
            
            balancer.return_session(proxy, session)
            
        except Exception as e:
            balancer.mark_failure(proxy)
            if session:
                session.close()
            try:
                self.send_error(502, "Bad Gateway")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

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

    error_message_format = '''<!DOCTYPE HTML>
<html lang="en">
    <head>
        <meta charset="utf-8">
        <title>Error response</title>
    </head>
    <body>
        <h1>Error response</h1>
        <p>Error code: %(code)d</p>
        <p>Message: %(message)s</p>
        <p>Error code explanation: %(code)d - %(explain)s</p>
    </body>
</html>
'''
