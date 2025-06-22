import asyncio
import aiohttp
import logging
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
        
        if not proxy_balancer:
            return web.Response(status=503, text="Service unavailable")
        
        if request.method == 'CONNECT':
            return await self._handle_connect(request, proxy_balancer)
        else:
            return await self._handle_http_request(request, proxy_balancer)
    
    async def _handle_connect(self, request: web.Request, proxy_balancer) -> web.Response:
        proxy = proxy_balancer.get_next_proxy()
        if not proxy:
            return web.Response(status=503, text="No available proxies")
        
        try:
            host_port = request.path_qs
            if ':' not in host_port:
                return web.Response(status=400, text="Invalid CONNECT request")
            
            dest_host, dest_port_str = host_port.split(':', 1)
            dest_port = int(dest_port_str)
            
            # Create SOCKS5 connection through proxy
            proxy_connector = ProxyConnector(
                proxy_type=ProxyType.SOCKS5,
                host=proxy["host"],
                port=proxy["port"],
                username=proxy.get("username"),
                password=proxy.get("password")
            )
            
            # Establish connection through proxy
            async with aiohttp.ClientSession(connector=proxy_connector) as session:
                try:
                    async with session.request('CONNECT', f'{dest_host}:{dest_port}') as resp:
                        proxy_balancer.mark_success(proxy)
                        return web.Response(status=200, text="Connection Established")
                except Exception as e:
                    proxy_balancer.mark_failure(proxy)
                    return web.Response(status=502, text="Bad Gateway")
                        
        except Exception as e:
            if proxy:
                proxy_balancer.mark_failure(proxy)
            return web.Response(status=502, text="Bad Gateway")
    
    async def _handle_http_request(self, request: web.Request, proxy_balancer) -> web.Response:
        proxy = proxy_balancer.get_next_proxy()
        if not proxy:
            return web.Response(status=503, text="No available proxies")
        
        try:
            # Use shared session for better connection reuse
            if not self.session:
                read_timeout = self.config.get('read_timeout', 30)
                connect_timeout = self.config.get('connection_timeout', 10)
                
                timeout = ClientTimeout(
                    total=read_timeout + connect_timeout,
                    connect=connect_timeout,
                    sock_read=read_timeout
                )
                self.session = aiohttp.ClientSession(
                    connector=self.connector,
                    timeout=timeout
                )
            
            # Read request body
            body = await request.read()
            
            # Optimized header processing - avoid dict recreation
            headers = {}
            for name, value in request.headers.items():
                if name.lower() not in self.proxy_headers_to_remove:
                    headers[name] = value
            
            # Build target URL
            url = self._build_url(request)
            
            # Create HTTP proxy URL
            proxy_url = f"http://{proxy['host']}:{proxy['port']}"
            
            # Use shared session instead of creating new one
            async with self.session.request(
                method=request.method,
                url=url,
                headers=headers,
                data=body,
                proxy=proxy_url,
                allow_redirects=False,
                ssl=True
            ) as response:
                proxy_balancer.mark_success(proxy)
                
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
                
                # Read response body
                response_body = await response.read()
                
                return web.Response(
                    status=response.status,
                    headers=response_headers,
                    body=response_body
                )
                
        except Exception as e:
            proxy_balancer.mark_failure(proxy)
            return web.Response(status=502, text="Bad Gateway")
    
    def _build_url(self, request: web.Request) -> str:
        if request.path_qs.startswith("http"):
            return request.path_qs
        
        host = request.headers.get("Host", "")
        if ":" in host:
            host, port_str = host.split(":", 1)
            port = int(port_str)
        else:
            port = 80
        
        scheme = "https" if port == 443 else "http"
        return (
            f"{scheme}://{host}:{port}{request.path_qs}"
            if port not in [80, 443]
            else f"{scheme}://{host}{request.path_qs}"
        )
    
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
        if self.session:
            await self.session.close()
        if self.connector:
            await self.connector.close()
        await runner.cleanup()
        self.logger.info("Proxy server stopped")
