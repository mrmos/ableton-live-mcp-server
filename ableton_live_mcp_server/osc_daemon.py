# osc_daemon.py
import asyncio
from pythonosc.udp_client import SimpleUDPClient
from pythonosc.osc_server import AsyncIOOSCUDPServer
from pythonosc.dispatcher import Dispatcher
import json
from typing import Optional, Dict, Any

class AbletonOSCDaemon:
    def __init__(self, 
                 socket_host='127.0.0.1', socket_port=65432,
                 ableton_host='127.0.0.1', ableton_port=11000,
                 receive_port=11001):
        self.socket_host = socket_host
        self.socket_port = socket_port
        self.ableton_host = ableton_host
        self.ableton_port = ableton_port
        self.receive_port = receive_port
        
        # Initialize OSC client for Ableton
        self.osc_client = SimpleUDPClient(ableton_host, ableton_port)
        
        # Store active connections waiting for responses
        self.pending_responses: Dict[str, asyncio.Future] = {}
        
        # Initialize OSC server dispatcher
        self.dispatcher = Dispatcher()
        self.dispatcher.set_default_handler(self.handle_ableton_message)
        
    def handle_ableton_message(self, address: str, *args):
        """Handle incoming OSC messages from Ableton."""
        print(f"[ABLETON MESSAGE] Address: {address}, Args: {args}")
        
        # If this address has a pending response, resolve it
        if address in self.pending_responses:
            future = self.pending_responses[address]
            if not future.done():
                future.set_result({
                    'status': 'success',
                    'address': address,
                    'data': args
                })
            del self.pending_responses[address]
            
    async def start(self):
        """Start both the socket server and OSC server."""
        # Start OSC server to receive Ableton messages
        self.osc_server = AsyncIOOSCUDPServer(
            (self.socket_host, self.receive_port),
            self.dispatcher,
            asyncio.get_event_loop()
        )
        
        transport, protocol = await self.osc_server.create_serve_endpoint()
        
        # Start TCP server for client connections
        server = await asyncio.start_server(
            self.handle_socket_client, 
            self.socket_host, 
            self.socket_port
        )
        
        addr = server.sockets[0].getsockname()
        print(f"Serving on {addr}")
        
        async with server:
            await server.serve_forever()
            
    async def handle_socket_client(self, reader, writer):
        """Handle a new client connection."""
        addr = writer.get_extra_info('peername')
        print(f"New connection from {addr}")
        
        try:
            while True:
                # Read data from the client
                data = await reader.read(4096)
                if not data:
                    break
                
                try:
                    request = json.loads(data.decode())
                except json.JSONDecodeError:
                    writer.write(json.dumps({
                        'error': 'Invalid JSON'
                    }).encode())
                    await writer.drain()
                    continue
                
                # Handle the request
                try:
                    result = await self.handle_request(request)
                    writer.write(json.dumps(result).encode())
                    await writer.drain()
                except Exception as e:
                    print(f"Error handling request: {e}")
                    writer.write(json.dumps({
                        'id': request.get('id'),
                        'error': str(e)
                    }).encode())
                    await writer.drain()
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Error with client {addr}: {e}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except:
                pass
            print(f"Connection from {addr} closed")
            
    async def handle_request(self, request):
        """Handle a JSON-RPC request from a client."""
        method = request.get('method')
        params = request.get('params', {})
        req_id = request.get('id')
        
        if method == 'send_osc':
            # Extract the OSC address and arguments
            address = params.get('address')
            args = params.get('args', [])
            
            # Check if we're waiting for a response
            wait_for_response = params.get('wait_for_response', True)
            
            # Send the OSC message
            self.osc_client.send_message(address, args)
            
            # If we're waiting for a response, create a pending response
            if wait_for_response:
                # Create a future for the response
                future = asyncio.Future()
                
                # Store the future
                self.pending_responses[address] = future
                
                try:
                    # Wait for the response (with timeout)
                    response = await asyncio.wait_for(future, timeout=5.0)
                    
                    # Return the response
                    return {
                        'jsonrpc': '2.0',
                        'id': req_id,
                        'result': response
                    }
                except asyncio.TimeoutError:
                    # Remove the pending response
                    if address in self.pending_responses:
                        del self.pending_responses[address]
                    
                    # Return a timeout error
                    return {
                        'jsonrpc': '2.0',
                        'id': req_id,
                        'result': {
                            'status': 'error',
                            'message': 'Timeout waiting for Ableton response'
                        }
                    }
            else:
                # Return success without waiting for a response
                return {
                    'jsonrpc': '2.0',
                    'id': req_id,
                    'result': {
                        'status': 'success',
                    }
                }
        else:
            # Unknown method
            return {
                'jsonrpc': '2.0',
                'id': req_id,
                'error': f'Unknown method: {method}'
            }

def main():
    """Run the Ableton OSC daemon."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Ableton OSC Daemon')
    parser.add_argument('--socket-host', default='127.0.0.1', help='Socket host')
    parser.add_argument('--socket-port', type=int, default=65432, help='Socket port')
    parser.add_argument('--ableton-host', default='127.0.0.1', help='Ableton host')
    parser.add_argument('--ableton-port', type=int, default=11000, help='Ableton port')
    parser.add_argument('--receive-port', type=int, default=11001, help='Receive port')
    
    args = parser.parse_args()
    
    # Create the daemon
    daemon = AbletonOSCDaemon(
        socket_host=args.socket_host,
        socket_port=args.socket_port,
        ableton_host=args.ableton_host,
        ableton_port=args.ableton_port,
        receive_port=args.receive_port
    )
    
    # Start the daemon
    loop = asyncio.get_event_loop()
    try:
        print(f"Starting Ableton OSC Daemon...")
        print(f"Listening for clients on {args.socket_host}:{args.socket_port}")
        print(f"Connecting to Ableton on {args.ableton_host}:{args.ableton_port}")
        print(f"Receiving OSC on port {args.receive_port}")
        loop.run_until_complete(daemon.start())
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        loop.close()

if __name__ == "__main__":
    main() 