from mcp.server.fastmcp import FastMCP
import asyncio
import json
import socket
import sys
from typing import List, Optional

class AbletonClient:
    def __init__(self, host='127.0.0.1', port=65432):
        self.host = host
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.connected = False
        self.responses = {}  # Store futures keyed by (request_id)
        self.lock = asyncio.Lock()
        self._request_id = 0  # compteur pour générer des ids uniques
        
        # Task asynchrone pour lire les réponses
        self.response_task = None

    async def start_response_reader(self):
        """Background task to read responses from the socket, potentially multiple messages."""
        # On convertit self.sock en Streams asyncio
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        loop = asyncio.get_running_loop()
        await loop.create_connection(lambda: protocol, sock=self.sock)

        while self.connected:
            try:
                data = await reader.read(4096)
                if not data:
                    # Connection close
                    break

                try:
                    msg = json.loads(data.decode())
                except json.JSONDecodeError:
                    print("Invalid JSON from daemon", file=sys.stderr)
                    continue

                # Si c'est une réponse JSON-RPC
                resp_id = msg.get('id')
                if 'result' in msg or 'error' in msg:
                    # Réponse à une requête
                    async with self.lock:
                        fut = self.responses.pop(resp_id, None)
                    if fut and not fut.done():
                        fut.set_result(msg)
                else:
                    # Sinon c'est un message "osc_response" ou un autre type
                    # (Selon le code du daemon)
                    if msg.get('type') == 'osc_response':
                        # On peut router selon l'adresse
                        address = msg.get('address')
                        args = msg.get('args')
                        await self.handle_osc_response(address, args)
                    else:
                        print(f"Unknown message: {msg}", file=sys.stderr)

            except Exception as e:
                print(f"Error reading response: {e}", file=sys.stderr)
                break

    async def handle_osc_response(self, address: str, args):
        """Callback quand on reçoit un message de type OSC depuis Ableton."""
        # Exemple simple : on pourrait faire un set_result sur un future
        print(f"OSC Notification from {address}: {args}", file=sys.stderr)

    def connect(self):
        """Connect to the OSC daemon via TCP socket."""
        if not self.connected:
            try:
                self.sock.connect((self.host, self.port))
                self.connected = True
                
                # Start the response reader task
                self.response_task = asyncio.create_task(self.start_response_reader())
                return True
            except Exception as e:
                print(f"Failed to connect to daemon: {e}", file=sys.stderr)
                return False
        return True

    async def send_rpc_request(self, method: str, params: dict) -> dict:
        """
        Envoie une requête JSON-RPC (method, params) et attend la réponse.
        """
        if not self.connected:
            if not self.connect():
                return {'status': 'error', 'message': 'Not connected to daemon'}

        # Génération d'un ID unique
        self._request_id += 1
        request_id = str(self._request_id)

        # Construit la requête JSON-RPC
        request_obj = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params
        }
        
        # Créer un Future pour attendre la réponse
        future = asyncio.Future()
        
        # Enregistre le Future dans le dictionnaire
        async with self.lock:
            self.responses[request_id] = future
        
        try:
            # Envoie la requête
            request_bytes = json.dumps(request_obj).encode()
            self.sock.sendall(request_bytes)
            
            # Attend la réponse (avec timeout)
            try:
                response = await asyncio.wait_for(future, timeout=10.0)
                if 'error' in response:
                    return {'status': 'error', 'message': response.get('error')}
                else:
                    return response.get('result', {})
            except asyncio.TimeoutError:
                # Supprime le future en cas de timeout
                async with self.lock:
                    self.responses.pop(request_id, None)
                return {'status': 'error', 'message': 'Request timed out'}
                
        except Exception as e:
            # Supprime le future en cas d'erreur
            async with self.lock:
                self.responses.pop(request_id, None)
            return {'status': 'error', 'message': str(e)}
    
    async def send_osc(self, address: str, args: List[any]) -> dict:
        """
        Envoie une commande OSC vers Ableton et attend potentiellement la réponse.
        """
        # Construit la requête JSON-RPC pour send_osc
        response = await self.send_rpc_request('send_osc', {
            'address': address,
            'args': args
        })
        
        return response
    
    async def get_browser_items(self, path: str) -> list:
        """
        Get browser items at the specified path.
        """
        response = await self.send_osc('/live/browser/get_items_at_path', [path])
        if response.get('status') == 'success':
            # The response format depends on the OSC daemon implementation
            return response.get('data', [])
        return []
    
    async def get_track_names(self, min_index: Optional[int] = None, max_index: Optional[int] = None) -> list:
        """
        Get the names of tracks within the given index range.
        """
        params = []
        if min_index is not None:
            params.append(min_index)
            if max_index is not None:
                params.append(max_index)
        
        response = await self.send_osc('/live/tracks/get_names', params)
        if response.get('status') == 'success':
            return response.get('data', [])
        return []
    
    async def close(self):
        """Close the connection."""
        if self.connected:
            self.connected = False
            
            # Cancel the response reader task
            if self.response_task:
                self.response_task.cancel()
                try:
                    await self.response_task
                except asyncio.CancelledError:
                    pass
                
            # Close the socket
            self.sock.close()


# MCP Server configuration
from mcp.server import mcp

# Create MCP server using FastMCP
ableton_client = AbletonClient()

@mcp.tool()
async def get_session_info(random_string: str) -> str:
    """Get detailed information about the current Ableton session"""
    response = await ableton_client.send_osc('/live/song/get_info', [])
    return json.dumps(response)

@mcp.tool()
async def get_track_info(track_index: int) -> str:
    """
    Get detailed information about a specific track in Ableton.
    
    Parameters:
    - track_index: The index of the track to get information about
    """
    response = await ableton_client.send_osc('/live/track/get_info', [track_index])
    return json.dumps(response)

@mcp.tool()
async def create_midi_track(index: int = -1) -> str:
    """
    Create a new MIDI track in the Ableton session.
    
    Parameters:
    - index: The index to insert the track at (-1 = end of list)
    """
    response = await ableton_client.send_osc('/live/song/create_midi_track', [index])
    return json.dumps(response)

@mcp.tool()
async def set_track_name(track_index: int, name: str) -> str:
    """
    Set the name of a track.
    
    Parameters:
    - track_index: The index of the track to rename
    - name: The new name for the track
    """
    response = await ableton_client.send_osc('/live/track/set_name', [track_index, name])
    return json.dumps(response)

@mcp.tool()
async def create_clip(track_index: int, clip_index: int, length: float = 4.0) -> str:
    """
    Create a new MIDI clip in the specified track and clip slot. First check if there are less than 7 clips, if not, ask the user to delete a clip first.
    
    Parameters:
    - track_index: The index of the track to create the clip in
    - clip_index: The index of the clip slot to create the clip in
    - length: The length of the clip in beats (default: 4.0)
    """
    response = await ableton_client.send_osc('/live/clip/create', [track_index, clip_index, length])
    return json.dumps(response)

@mcp.tool()
async def add_notes_to_clip(track_index: int, clip_index: int, notes: List[dict]) -> str:
    """
    Add MIDI notes to a clip.
    
    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - notes: List of note dictionaries, each with pitch, start_time, duration, velocity, and mute
    """
    # Flatten the notes array for OSC
    notes_flat = []
    for note in notes:
        notes_flat.extend([
            note.get("pitch", 60),
            note.get("start_time", 0.0),
            note.get("duration", 1.0),
            note.get("velocity", 100),
            1 if note.get("mute", False) else 0
        ])
    
    response = await ableton_client.send_osc('/live/clip/add_notes', [track_index, clip_index, *notes_flat])
    return json.dumps(response)

@mcp.tool()
async def set_clip_name(track_index: int, clip_index: int, name: str) -> str:
    """
    Set the name of a clip.
    
    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - name: The new name for the clip
    """
    response = await ableton_client.send_osc('/live/clip/set_name', [track_index, clip_index, name])
    return json.dumps(response)

@mcp.tool()
async def set_tempo(tempo: float) -> str:
    """
    Set the tempo of the Ableton session.
    
    Parameters:
    - tempo: The new tempo in BPM
    """
    response = await ableton_client.send_osc('/live/song/set_tempo', [tempo])
    return json.dumps(response)

@mcp.tool()
async def load_instrument_or_effect(track_index: int, uri: str) -> str:
    """
    Load an instrument or effect onto a track using its URI.
    
    Parameters:
    - track_index: The index of the track to load the instrument on
    - uri: The URI of the instrument or effect to load (e.g., 'query:Synths#Instrument%20Rack:Bass:FileId_5116')
    """
    response = await ableton_client.send_osc('/live/track/load_device', [track_index, uri])
    return json.dumps(response)

@mcp.tool()
async def fire_clip(track_index: int, clip_index: int) -> str:
    """
    Start playing a clip.
    
    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    response = await ableton_client.send_osc('/live/clip/fire', [track_index, clip_index])
    return json.dumps(response)

@mcp.tool()
async def stop_clip(track_index: int, clip_index: int) -> str:
    """
    Stop playing a clip.
    
    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    response = await ableton_client.send_osc('/live/clip/stop', [track_index, clip_index])
    return json.dumps(response)

@mcp.tool()
async def start_playback(random_string: str) -> str:
    """Start playing the Ableton session."""
    response = await ableton_client.send_osc('/live/song/start_playing', [])
    return json.dumps(response)

@mcp.tool()
async def stop_playback(random_string: str) -> str:
    """Stop playing the Ableton session."""
    response = await ableton_client.send_osc('/live/song/stop_playing', [])
    return json.dumps(response)

@mcp.tool()
async def get_browser_tree(category_type: str = "all") -> str:
    """
    Get a hierarchical tree of browser categories from Ableton.
    
    Parameters:
    - category_type: Type of categories to get ('all', 'instruments', 'sounds', 'drums', 'audio_effects', 'midi_effects')
    """
    response = await ableton_client.send_osc('/live/browser/get_tree', [category_type])
    return json.dumps(response)

@mcp.tool()
async def get_browser_items_at_path(path: str) -> str:
    """
    Get browser items at a specific path in Ableton's browser.
    
    Parameters:
    - path: Path in the format "category/folder/subfolder"
            where category is one of the available browser categories in Ableton
    """
    response = await ableton_client.send_osc('/live/browser/get_items_at_path', [path])
    return json.dumps(response)

@mcp.tool()
async def load_drum_kit(track_index: int, rack_uri: str, kit_path: str) -> str:
    """
    Load a drum rack and then load a specific drum kit into it.
    
    Parameters:
    - track_index: The index of the track to load on
    - rack_uri: The URI of the drum rack to load (e.g., 'Drums/Drum Rack')
    - kit_path: Path to the drum kit inside the browser (e.g., 'drums/acoustic/kit1')
    """
    # First load the drum rack
    rack_response = await ableton_client.send_osc('/live/track/load_device', [track_index, rack_uri])
    
    # Then load the kit
    kit_response = await ableton_client.send_osc('/live/browser/load_drum_kit', [track_index, kit_path])
    
    return json.dumps({
        "rack_response": rack_response,
        "kit_response": kit_response
    })

def main():
    """Run the MCP server."""
    import uvicorn
    import os
    
    # Configure the FastMCP server
    app = FastMCP(
        name="Ableton Live MCP Server",
        tools=[
            get_session_info,
            get_track_info,
            create_midi_track,
            set_track_name,
            create_clip,
            add_notes_to_clip,
            set_clip_name,
            set_tempo,
            load_instrument_or_effect,
            fire_clip,
            stop_clip,
            start_playback,
            stop_playback,
            get_browser_tree,
            get_browser_items_at_path,
            load_drum_kit,
        ],
        prefix="mcp_AbletonMCP_"
    )
    
    # Connect to Ableton OSC daemon
    ableton_client.connect()
    
    # Start the FastMCP server
    port = int(os.environ.get("MCP_PORT", 8000))
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    
    print(f"Starting Ableton Live MCP Server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    main() 