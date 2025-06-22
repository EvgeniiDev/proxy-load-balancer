import asyncio
import aiohttp
import logging
import socket
import struct
from typing import Optional, Dict, Any
from aiohttp import web, ClientTimeout, TCPConnector
from aiohttp_socks import ProxyConnector, ProxyType


class ProxyBalancerServer:
    def __init__(self, host: str, port: int, config: Optional[Dict[str, Any]] = None, **kwargs):
        self.host = host
        self.port = port
        self.config = config or {}
        self.proxy_balancer = None
        
        # Performance optimization: pre-compile header sets
        self.proxy_headers_to_remove = frozenset([
            "proxy-connection", "proxy-authorization", "via", 
            "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
            "x-real-ip", "x-proxy-authorization", "proxy-authenticate",
            "x-forwarded-server", "x-forwarded-port", "forwarded"
        ])
        
        # Create optimized connector
        perf_config = self.config.get('performance', {})
        connector_kwargs = {
            'limit': perf_config.get('max_concurrent_connections', 100),
            'limit_per_host': perf_config.get('connection_pool_size', 30),
            'ttl_dns_cache': perf_config.get('dns_cache_ttl', 300),
            'use_dns_cache': True,
            'keepalive_timeout': perf_config.get('keepalive_timeout', 75),
        }
        self.connector = TCPConnector(**connector_kwargs)
        self.session = None
        
        self.app = web.Application()
        self.app.router.add_route('*', '/{path:.*}', self.handle_request)
        self.logger = logging.getLogger("proxy_server")
        self._setup_routes()
    
    def _setup_routes(self):
        self.app['proxy_balancer'] = self.proxy_balancer
    
    async def handle_request(self, request: web.Request) -> web.Response:
        proxy_balancer = self.app.get('proxy_balancer') or self.proxy_balancer
        
        self.logger.info(f"Received {request.method} request to {request.path_qs}")
        self.logger.debug(f"Request headers: {dict(request.headers)}")
        
        if not proxy_balancer:
            return web.Response(status=503, text="Service unavailable")
        
        if request.method == 'CONNECT':
            self.logger.info(f"Handling CONNECT request for {request.path_qs}")
            return await self._handle_connect_tunnel(request, proxy_balancer)
        else:
            return await self._handle_http_request(request, proxy_balancer)
    
    async def _handle_connect_tunnel(self, request: web.Request, proxy_balancer) -> web.Response:
        """Handle CONNECT requests with tunnel establishment through SOCKS5 proxy"""
        proxy = proxy_balancer.get_next_proxy()
        if not proxy:
            self.logger.error("No available proxies for CONNECT")
            return web.Response(status=503, text="No available proxies")
        
        try:
            host_port = request.path_qs
            if not host_port or ':' not in host_port:
                self.logger.error(f"Invalid CONNECT request format: {host_port}")
                return web.Response(status=400, text="Invalid CONNECT request")
            
            dest_host, dest_port_str = host_port.split(':', 1)
            try:
                dest_port = int(dest_port_str)
            except ValueError:
                self.logger.error(f"Invalid port in CONNECT: {dest_port_str}")
                return web.Response(status=400, text="Invalid port")
            
            self.logger.info(f"CONNECT to {dest_host}:{dest_port} through SOCKS5 proxy {proxy['host']}:{proxy['port']}")
            
            try:
                await self._test_socks5_connection(proxy, dest_host, dest_port)
                proxy_balancer.mark_success(proxy)
                self.logger.info(f"CONNECT tunnel established successfully to {dest_host}:{dest_port}")
                
                # Return proper CONNECT response
                # Note: Real tunneling would require protocol upgrade which aiohttp doesn't support directly
                return web.Response(
                    status=200,
                    text="Connection established",
                    headers={
                        "Proxy-Agent": "ProxyLoadBalancer/1.0",
                        "Connection": "close"
                    }
                )
                
            except Exception as e:
                proxy_balancer.mark_failure(proxy)
                self.logger.error(f"Error establishing SOCKS5 connection: {e}")
                return web.Response(status=502, text="Bad Gateway")
                
        except Exception as e:
            if proxy:
                proxy_balancer.mark_failure(proxy)
            self.logger.error(f"Error handling CONNECT: {e}")
            return web.Response(status=502, text="Bad Gateway")
    
    async def _test_socks5_connection(self, proxy, dest_host, dest_port):
        """Test SOCKS5 connection to destination through proxy"""
        proxy_reader, proxy_writer = await asyncio.open_connection(
            proxy['host'], proxy['port']
        )
        
        try:
            if proxy.get('username') and proxy.get('password'):
                await self._socks5_auth(proxy_reader, proxy_writer, 
                                      proxy['username'], proxy['password'])
            else:
                await self._socks5_no_auth(proxy_reader, proxy_writer)
            
            await self._socks5_connect(proxy_reader, proxy_writer, dest_host, dest_port)
            self.logger.info(f"SOCKS5 connection test successful to {dest_host}:{dest_port}")
            
        finally:
            proxy_writer.close()
            await proxy_writer.wait_closed()
    
    async def _socks5_no_auth(self, reader, writer):
        """SOCKS5 authentication - no auth required"""
        writer.write(b'\x05\x01\x00')
        await writer.drain()
        
        response = await reader.read(2)
        if len(response) != 2 or response[0] != 0x05:
            raise Exception("Invalid SOCKS5 response")
        
        if response[1] != 0x00:
            raise Exception("SOCKS5 authentication failed")
    
    async def _socks5_auth(self, reader, writer, username, password):
        """SOCKS5 authentication with username/password"""
        writer.write(b'\x05\x02\x00\x02')
        await writer.drain()
        
        response = await reader.read(2)
        if len(response) != 2 or response[0] != 0x05:
            raise Exception("Invalid SOCKS5 response")
        
        if response[1] == 0x00:
            return
        elif response[1] == 0x02:
            username_bytes = username.encode('utf-8')
            password_bytes = password.encode('utf-8')
            
            auth_request = bytes([
                0x01,  # Version
                len(username_bytes)
            ]) + username_bytes + bytes([len(password_bytes)]) + password_bytes
            
            writer.write(auth_request)
            await writer.drain()
            
            auth_response = await reader.read(2)
            if len(auth_response) != 2 or auth_response[0] != 0x01:
                raise Exception("Invalid SOCKS5 auth response")
            
            if auth_response[1] != 0x00:
                raise Exception("SOCKS5 authentication failed")
        else:
            raise Exception("SOCKS5 no acceptable authentication methods")
    
    async def _socks5_connect(self, reader, writer, dest_host, dest_port):
        """Send SOCKS5 CONNECT command"""
        try:
            dest_ip = socket.inet_aton(dest_host)
            addr_type = 0x01  # IPv4
            addr_data = dest_ip
        except socket.error:
            try:
                dest_ip = socket.inet_pton(socket.AF_INET6, dest_host)
                addr_type = 0x04  # IPv6
                addr_data = dest_ip
            except socket.error:
                addr_type = 0x03  # Domain name
                addr_data = bytes([len(dest_host)]) + dest_host.encode('utf-8')
        
        connect_request = bytes([
            0x05,  # SOCKS version
            0x01,  # CONNECT command
            0x00,  # Reserved
            addr_type
        ]) + addr_data + struct.pack('>H', dest_port)
        
        writer.write(connect_request)
        await writer.drain()
        
        response = await reader.read(4)
        if len(response) != 4:
            raise Exception("Invalid SOCKS5 connect response")
        
        if response[0] != 0x05:
            raise Exception("Invalid SOCKS5 version in response")
        
        if response[1] != 0x00:
            error_messages = {
                0x01: "General SOCKS server failure",
                0x02: "Connection not allowed by ruleset",
                0x03: "Network unreachable",
                0x04: "Host unreachable",
                0x05: "Connection refused",
                0x06: "TTL expired",
                0x07: "Command not supported",
                0x08: "Address type not supported"
            }
            error_msg = error_messages.get(response[1], f"Unknown error: {response[1]}")
            raise Exception(f"SOCKS5 connect failed: {error_msg}")
        
        addr_type = response[3]
        if addr_type == 0x01:  # IPv4
            await reader.read(4 + 2)  # IP + port
        elif addr_type == 0x03:  # Domain name
            domain_len = (await reader.read(1))[0]
            await reader.read(domain_len + 2)  # domain + port
        elif addr_type == 0x04:  # IPv6
            await reader.read(16 + 2)  # IP + port
    
    
    async def _handle_http_request(self, request: web.Request, proxy_balancer) -> web.Response:
        proxy = proxy_balancer.get_next_proxy()
        if not proxy:
            self.logger.error("No available proxies")
            return web.Response(status=503, text="No available proxies")
        
        try:
            # Read request body
            body = await request.read()
            
            # Optimized header processing - avoid dict recreation
            headers = {}
            for name, value in request.headers.items():
                if name.lower() not in self.proxy_headers_to_remove:
                    headers[name] = value
            
            # Build target URL
            url = self._build_url(request)
            self.logger.info(f"Proxying {request.method} {url} through {proxy['host']}:{proxy['port']}")
            
            read_timeout = self.config.get('read_timeout', 30)
            connect_timeout = self.config.get('connection_timeout', 10)
            
            timeout = ClientTimeout(
                total=read_timeout + connect_timeout,
                connect=connect_timeout,
                sock_read=read_timeout
            )
            
            # Create SOCKS5 proxy connector for this specific proxy
            proxy_connector = ProxyConnector(
                proxy_type=ProxyType.SOCKS5,
                host=proxy['host'],
                port=proxy['port'],
                username=proxy.get('username'),
                password=proxy.get('password')
            )
            
            # Create session for this request with this specific proxy
            async with aiohttp.ClientSession(
                connector=proxy_connector,
                timeout=timeout
            ) as session:
                # Make request through SOCKS5 proxy
                async with session.request(
                    method=request.method,
                    url=url,
                    headers=headers,
                    data=body,
                    allow_redirects=False,
                    ssl=False  # Allow insecure connections for testing
                ) as response:
                    proxy_balancer.mark_success(proxy)
                    self.logger.info(f"Request successful: {response.status}")
                    
                    # Read response body first
                    response_body = await response.read()
                    
                    # Optimized response header processing
                    response_headers = {}
                    for header, value in response.headers.items():
                        header_lower = header.lower()
                        if header_lower not in [
                            "connection", "transfer-encoding", "via", "x-forwarded-for",
                            "x-forwarded-host", "x-forwarded-proto", "x-real-ip",
                            "proxy-connection", "proxy-authenticate", "server"
                        ]:
                            response_headers[header] = value
                
                    return web.Response(
                        status=response.status,
                        headers=response_headers,
                        body=response_body
                    )
                
        except Exception as e:
            proxy_balancer.mark_failure(proxy)
            self.logger.error(f"Error proxying request: {e}")
            return web.Response(status=502, text="Bad Gateway")
    
    def _build_url(self, request: web.Request) -> str:
        # If the request path is already a full URL (like http://example.com/path), use it directly
        if request.path_qs.startswith("http://") or request.path_qs.startswith("https://"):
            return request.path_qs
        
        # Otherwise, construct URL from Host header and path
        host = request.headers.get("Host", "")
        if not host:
            return request.path_qs  # Fallback to relative path
            
        if ":" in host:
            host, port_str = host.split(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                port = 80
        else:
            port = 80
        
        # Determine scheme based on port or explicit indication
        scheme = "https" if port == 443 else "http"
        
        # Construct full URL
        if port in [80, 443]:
            return f"{scheme}://{host}{request.path_qs}"
        else:
            return f"{scheme}://{host}:{port}{request.path_qs}"
    
    def set_proxy_balancer(self, proxy_balancer):
        self.proxy_balancer = proxy_balancer
        self.app['proxy_balancer'] = proxy_balancer
    
    async def start(self):
        # Configure server with performance settings
        server_config = self.config.get('server', {})
        
        runner = web.AppRunner(
            self.app,
            access_log=server_config.get('access_log', False),
            keepalive_timeout=server_config.get('keepalive_timeout', 75)
        )
        await runner.setup()
        
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        self.logger.info(f"Proxy server started on {self.host}:{self.port}")
        return runner
    
    async def stop(self, runner):
        if self.connector:
            await self.connector.close()
        await runner.cleanup()
        self.logger.info("Proxy server stopped")
