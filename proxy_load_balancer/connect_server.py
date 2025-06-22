import asyncio
import logging
import struct
import socket
from typing import Optional, Dict, Any, Tuple
from proxy_load_balancer.balancer import ProxyBalancer


class ConnectTunnelServer:
    """TCP server specifically for handling CONNECT requests with full tunneling"""
    
    def __init__(self, host: str, port: int, proxy_balancer: ProxyBalancer, config: Optional[Dict[str, Any]] = None):
        self.host = host
        self.port = port
        self.proxy_balancer = proxy_balancer
        self.config = config or {}
        self.server = None
        self.logger = logging.getLogger("connect_tunnel_server")
    
    async def start(self):
        """Start the CONNECT tunnel server"""
        self.server = await asyncio.start_server(
            self.handle_connect_client, self.host, self.port
        )
        self.logger.info(f"CONNECT tunnel server started on {self.host}:{self.port}")
        return self.server
    
    async def stop(self):
        """Stop the CONNECT tunnel server"""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.logger.info("CONNECT tunnel server stopped")
    
    async def handle_connect_client(self, client_reader, client_writer):
        """Handle incoming client connection"""
        client_addr = client_writer.get_extra_info('peername')
        self.logger.info(f"New client connection from {client_addr}")
        
        try:
            # Read HTTP request line
            request_line = await client_reader.readline()
            if not request_line:
                await self._send_error_response(client_writer, 400, "Bad Request")
                return
            
            request_line = request_line.decode('utf-8').strip()
            self.logger.debug(f"Request line: {request_line}")
            
            # Parse CONNECT request
            parts = request_line.split(' ')
            if len(parts) != 3 or parts[0] != 'CONNECT':
                await self._send_error_response(client_writer, 405, "Method Not Allowed")
                return
            
            host_port = parts[1]
            if ':' not in host_port:
                await self._send_error_response(client_writer, 400, "Bad Request")
                return
            
            dest_host, dest_port_str = host_port.split(':', 1)
            try:
                dest_port = int(dest_port_str)
            except ValueError:
                await self._send_error_response(client_writer, 400, "Bad Request")
                return
            
            # Read headers (we don't need them but must consume them)
            while True:
                header_line = await client_reader.readline()
                if not header_line or header_line == b'\r\n':
                    break
            
            # Get proxy from balancer
            proxy = self.proxy_balancer.get_next_proxy()
            if not proxy:
                await self._send_error_response(client_writer, 503, "Service Unavailable")
                return
            
            self.logger.info(f"Establishing tunnel to {dest_host}:{dest_port} through {proxy['host']}:{proxy['port']}")
            
            try:
                # Connect to SOCKS5 proxy and establish tunnel
                proxy_reader, proxy_writer = await self._connect_socks5(proxy, dest_host, dest_port)
                
                # Send successful response
                response = b"HTTP/1.1 200 Connection established\r\n\r\n"
                client_writer.write(response)
                await client_writer.drain()
                
                # Start tunneling
                await self._tunnel_data(client_reader, client_writer, proxy_reader, proxy_writer)
                
                self.proxy_balancer.mark_success(proxy)
                self.logger.info(f"Tunnel closed for {dest_host}:{dest_port}")
                
            except Exception as e:
                self.proxy_balancer.mark_failure(proxy)
                self.logger.error(f"Failed to establish tunnel: {e}")
                await self._send_error_response(client_writer, 502, "Bad Gateway")
        
        except Exception as e:
            self.logger.error(f"Error handling client {client_addr}: {e}")
        finally:
            try:
                client_writer.close()
                await client_writer.wait_closed()
            except:
                pass
    
    async def _send_error_response(self, writer, status_code: int, status_text: str):
        """Send HTTP error response"""
        response = f"HTTP/1.1 {status_code} {status_text}\r\nConnection: close\r\n\r\n".encode()
        writer.write(response)
        await writer.drain()
    
    async def _connect_socks5(self, proxy: Dict[str, Any], dest_host: str, dest_port: int) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Connect to destination through SOCKS5 proxy"""
        proxy_reader, proxy_writer = await asyncio.open_connection(
            proxy['host'], proxy['port']
        )
        
        try:
            # SOCKS5 authentication
            if proxy.get('username') and proxy.get('password'):
                await self._socks5_auth_with_password(proxy_reader, proxy_writer, 
                                                    proxy['username'], proxy['password'])
            else:
                await self._socks5_no_auth(proxy_reader, proxy_writer)
            
            # SOCKS5 connect
            await self._socks5_connect(proxy_reader, proxy_writer, dest_host, dest_port)
            
            return proxy_reader, proxy_writer
            
        except Exception as e:
            proxy_writer.close()
            await proxy_writer.wait_closed()
            raise e
    
    async def _socks5_no_auth(self, reader, writer):
        """SOCKS5 no authentication"""
        writer.write(b'\x05\x01\x00')  # SOCKS5, 1 method, no auth
        await writer.drain()
        
        response = await reader.read(2)
        if len(response) != 2 or response[0] != 0x05 or response[1] != 0x00:
            raise Exception("SOCKS5 authentication failed")
    
    async def _socks5_auth_with_password(self, reader, writer, username: str, password: str):
        """SOCKS5 authentication with username/password"""
        writer.write(b'\x05\x02\x00\x02')  # SOCKS5, 2 methods, no auth + user/pass
        await writer.drain()
        
        response = await reader.read(2)
        if len(response) != 2 or response[0] != 0x05:
            raise Exception("Invalid SOCKS5 response")
        
        if response[1] == 0x00:  # No auth
            return
        elif response[1] == 0x02:  # Username/password
            username_bytes = username.encode('utf-8')
            password_bytes = password.encode('utf-8')
            
            auth_request = bytes([0x01, len(username_bytes)]) + username_bytes + bytes([len(password_bytes)]) + password_bytes
            writer.write(auth_request)
            await writer.drain()
            
            auth_response = await reader.read(2)
            if len(auth_response) != 2 or auth_response[0] != 0x01 or auth_response[1] != 0x00:
                raise Exception("SOCKS5 authentication failed")
        else:
            raise Exception("SOCKS5 no acceptable authentication methods")
    
    async def _socks5_connect(self, reader, writer, dest_host: str, dest_port: int):
        """Send SOCKS5 CONNECT command"""
        # Determine address type and format
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
        
        connect_request = bytes([0x05, 0x01, 0x00, addr_type]) + addr_data + struct.pack('>H', dest_port)
        writer.write(connect_request)
        await writer.drain()
        
        # Read response
        response = await reader.read(4)
        if len(response) != 4 or response[0] != 0x05:
            raise Exception("Invalid SOCKS5 connect response")
        
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
        
        # Read bound address (we don't need it but must consume it)
        addr_type = response[3]
        if addr_type == 0x01:  # IPv4
            await reader.read(4 + 2)  # IP + port
        elif addr_type == 0x03:  # Domain name
            domain_len = (await reader.read(1))[0]
            await reader.read(domain_len + 2)  # domain + port
        elif addr_type == 0x04:  # IPv6
            await reader.read(16 + 2)  # IP + port
    
    async def _tunnel_data(self, client_reader, client_writer, proxy_reader, proxy_writer):
        """Tunnel data between client and proxy"""
        async def copy_data(src_reader, dst_writer, direction):
            try:
                while True:
                    data = await src_reader.read(8192)
                    if not data:
                        break
                    dst_writer.write(data)
                    await dst_writer.drain()
            except Exception as e:
                self.logger.debug(f"Copy data error ({direction}): {e}")
            finally:
                try:
                    dst_writer.close()
                    await dst_writer.wait_closed()
                except:
                    pass
        
        # Start bidirectional data copying
        client_to_proxy = asyncio.create_task(
            copy_data(client_reader, proxy_writer, "client->proxy")
        )
        proxy_to_client = asyncio.create_task(
            copy_data(proxy_reader, client_writer, "proxy->client")
        )
        
        # Wait for either direction to finish
        try:
            await asyncio.gather(client_to_proxy, proxy_to_client, return_exceptions=True)
        finally:
            client_to_proxy.cancel()
            proxy_to_client.cancel()
            
            try:
                proxy_writer.close()
                await proxy_writer.wait_closed()
            except:
                pass
