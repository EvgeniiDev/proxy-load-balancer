import select
import socket
import logging
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict, Optional
import socks
from .utils import ProxyManager


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

        try:
            dest_host, dest_port = self._parse_connect_destination()
            remote_socket = self._create_proxy_connection(proxy, dest_host, dest_port)
            balancer.mark_success(proxy)
            
            self.send_response(200, "Connection Established")
            self.end_headers()
            
            self._tunnel_data(self.connection, remote_socket)
            
        except Exception as e:
            if proxy:
                balancer.mark_failure(proxy)
            self._send_error_safely(502, "Bad Gateway")

    def _parse_connect_destination(self):
        dest_host, dest_port_str = self.path.split(":", 1)
        dest_port = int(dest_port_str)
        return dest_host, dest_port

    def _create_proxy_connection(self, proxy, dest_host, dest_port):
        remote_socket = socks.socksocket()
        remote_socket.set_proxy(
            proxy_type=socks.SOCKS5,
            addr=proxy["host"],
            port=proxy["port"],
            username=proxy.get("username"),
            password=proxy.get("password"),
        )
        remote_socket.settimeout(10)
        remote_socket.connect((dest_host, dest_port))
        return remote_socket

    def _tunnel_data(self, client_socket, remote_socket):
        sockets = [client_socket, remote_socket]
        
        try:
            while sockets:
                readable, _, exceptional = select.select(sockets, [], sockets, 60)
                
                if exceptional or not readable:
                    break
                
                for socket in readable:
                    if not self._handle_socket_data(socket, client_socket, remote_socket, sockets):
                        return
                        
        finally:
            self._close_socket_safely(remote_socket)

    def _handle_socket_data(self, socket, client_socket, remote_socket, sockets):
        try:
            data = socket.recv(8192)
            if not data:
                self._remove_socket_and_close_peer(socket, client_socket, remote_socket, sockets)
                return len(sockets) > 0
            
            target_socket = remote_socket if socket is client_socket else client_socket
            return self._forward_data(data, target_socket, socket, client_socket, remote_socket, sockets)
            
        except (ConnectionResetError, BrokenPipeError):
            self._handle_connection_error(socket, client_socket, remote_socket, sockets)
            return False

    def _forward_data(self, data, target_socket, source_socket, client_socket, remote_socket, sockets):
        try:
            target_socket.sendall(data)
            return True
        except (ConnectionResetError, BrokenPipeError):
            self._remove_socket_safely(target_socket, sockets)
            return False

    def _remove_socket_and_close_peer(self, socket, client_socket, remote_socket, sockets):
        self._remove_socket_safely(socket, sockets)
        
        if socket is client_socket:
            self._remove_socket_safely(remote_socket, sockets)
        else:
            self._remove_socket_safely(client_socket, sockets)

    def _handle_connection_error(self, socket, client_socket, remote_socket, sockets):
        self._remove_socket_safely(socket, sockets)
        
        if socket is client_socket:
            self._remove_socket_safely(remote_socket, sockets)
        elif socket is remote_socket:
            self._remove_socket_safely(client_socket, sockets)

    def _remove_socket_safely(self, socket, sockets):
        if socket in sockets:
            sockets.remove(socket)
            self._close_socket_safely(socket)

    def _close_socket_safely(self, socket):
        try:
            socket.close()
        except:
            pass

    def _send_error_safely(self, code, message):
        try:
            self.send_error(code, message)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
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
                "X-Forwarded-For", "X-Forwarded-Host",
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
            
            if response.status_code == 429:
                balancer.mark_overloaded(proxy)
                if session:
                    balancer.return_session(proxy, session)
                tried_keys = {ProxyManager.get_proxy_key(proxy)}
                last_response = response
                available_count = len(getattr(balancer, "available_proxies", []))
                max_retries_429 = 20

                retries_done = 0
                while retries_done < max_retries_429 and len(tried_keys) < max(available_count, 1):
                    alt = balancer.get_next_proxy()
                    if not alt:
                        break
                    alt_key = ProxyManager.get_proxy_key(alt)
                    if alt_key in tried_keys:
                        continue
                    tried_keys.add(alt_key)
                    retries_done += 1
                    alt_session = None
                    try:
                        alt_session = balancer.get_session(alt)
                        alt_resp = alt_session.request(
                            method=self.command,
                            url=url,
                            headers=headers,
                            data=body,
                            timeout=balancer.config["connection_timeout"],
                            allow_redirects=False,
                            verify=True,
                            stream=True,
                        )
                        last_response = alt_resp
                        if alt_resp.status_code == 429:
                            balancer.mark_overloaded(alt)
                            balancer.return_session(alt, alt_session)
                            continue
                        balancer.mark_success(alt)
                        self.send_response(alt_resp.status_code)
                        for header, value in alt_resp.headers.items():
                            header_lower = header.lower()
                            if header_lower not in [
                                "connection", "transfer-encoding", "via", "x-forwarded-for",
                                "x-forwarded-host", "x-forwarded-proto", "x-real-ip",
                                "proxy-connection", "proxy-authenticate", "server"
                            ]:
                                self.send_header(header, value)
                        self.end_headers()
                        for chunk in alt_resp.iter_content(8192):
                            if chunk:
                                try:
                                    self.wfile.write(chunk)
                                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                                    break
                        balancer.return_session(alt, alt_session)
                        return
                    except Exception:
                        balancer.mark_failure(alt)
                        if alt_session:
                            try:
                                alt_session.close()
                            except:
                                pass
                        continue
                try:
                    if getattr(last_response, "status_code", None) == 429:
                        if retries_done >= max_retries_429 or available_count >= 10:
                            self.send_error(503, "Service Unavailable")
                        else:
                            self.send_error(429, "Too Many Requests - Proxy overloaded")
                    else:
                        self.send_error(502, "Bad Gateway")
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                return
            
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
