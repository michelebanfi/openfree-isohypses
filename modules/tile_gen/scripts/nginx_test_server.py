#!/usr/bin/env python3
"""
Lightweight tile server that mimics nginx behavior.
Uses efficient file-based serving for testing contour tiles locally.

This server serves tiles from a directory structure (z/x/y.pbf),
similar to how nginx serves tiles in production.

Usage:
    python nginx_test_server.py --tiles-dir ./contours_tiles/tiles --port 8080

Features:
- Serves tiles from directory structure
- Proper CORS headers (like nginx)
- Content-Encoding: gzip (assumes pre-compressed tiles)
- Serves static files (viewer.html, tilejson.json)
- Minimal dependencies (Python stdlib only)
"""

import argparse
import http.server
import json
import os
import socketserver
from pathlib import Path
from urllib.parse import urlparse


class NginxLikeTileHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler that mimics nginx tile serving behavior."""
    
    tiles_dir = None
    static_dir = None
    
    def do_GET(self):
        path = urlparse(self.path).path
        
        # Serve tile requests: /tiles/{z}/{x}/{y}.pbf or /contours/{z}/{x}/{y}.pbf
        if '/tiles/' in path or '/contours/' in path:
            # Extract z/x/y from path
            parts = path.rstrip('/').split('/')
            try:
                y_pbf = parts[-1]
                x = parts[-2]
                z = parts[-3]
                
                if y_pbf.endswith('.pbf'):
                    y = y_pbf[:-4]
                    self.serve_tile(z, x, y)
                    return
            except (IndexError, ValueError):
                pass
            
            self.send_error(404, 'Tile not found')
            return
        
        # Serve viewer
        if path == '/' or path == '/index.html' or path == '/viewer.html':
            self.serve_file('viewer.html', 'text/html')
            return
        
        # Serve tilejson
        if path == '/tilejson.json' or path == '/contours':
            self.serve_file('tilejson.json', 'application/json')
            return
        
        # Default 404
        self.send_error(404, 'Not Found')
    
    def serve_tile(self, z, x, y):
        """Serve a tile from the file system."""
        tile_path = self.tiles_dir / z / x / f'{y}.pbf'
        
        if tile_path.exists():
            self.send_response(200)
            self.send_header('Content-Type', 'application/vnd.mapbox-vector-tile')
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'public, max-age=31536000')
            self.send_header('X-OFM-Debug', f'tile {z}/{x}/{y}')
            
            with open(tile_path, 'rb') as f:
                data = f.read()
            
            self.send_header('Content-Length', len(data))
            self.end_headers()
            self.wfile.write(data)
        else:
            # Return empty tile (like nginx @empty_tile)
            self.send_response(200)
            self.send_header('Content-Type', 'application/vnd.mapbox-vector-tile')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'public, max-age=31536000')
            self.send_header('X-OFM-Debug', 'empty tile')
            self.send_header('Content-Length', 0)
            self.end_headers()
    
    def serve_file(self, filename, content_type):
        """Serve a static file from the static directory."""
        file_path = self.static_dir / filename
        
        if file_path.exists():
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Access-Control-Allow-Origin', '*')
            
            with open(file_path, 'rb') as f:
                data = f.read()
            
            self.send_header('Content-Length', len(data))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404, f'{filename} not found')
    
    def log_message(self, format, *args):
        """Custom log format."""
        if '200' in str(args):
            status = '✓'
        else:
            status = '✗'
        print(f'{status} {args[0]}')
    
    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()


def main():
    parser = argparse.ArgumentParser(
        description='Lightweight nginx-like tile server for local testing',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('--tiles-dir', type=Path, required=True, 
                        help='Directory containing tiles (z/x/y.pbf structure)')
    parser.add_argument('--static-dir', type=Path, default=None,
                        help='Directory for static files (viewer.html, tilejson.json)')
    parser.add_argument('--port', type=int, default=8080, help='Port to serve on')
    parser.add_argument('--host', default='127.0.0.1', help='Host to bind to')
    
    args = parser.parse_args()
    
    if not args.tiles_dir.exists():
        print(f'Error: tiles directory not found: {args.tiles_dir}')
        return 1
    
    # Default static dir to parent of tiles dir
    static_dir = args.static_dir or args.tiles_dir.parent
    
    # Count tiles
    tile_count = sum(1 for _ in args.tiles_dir.rglob('*.pbf'))
    
    # Set class variables
    NginxLikeTileHandler.tiles_dir = args.tiles_dir
    NginxLikeTileHandler.static_dir = static_dir
    
    print(f'''
╔══════════════════════════════════════════════════════════════╗
║           🏔️  Nginx-like Contour Tile Server                 ║
╠══════════════════════════════════════════════════════════════╣
║  Tiles directory: {str(args.tiles_dir):<40} ║
║  Tile count: {tile_count:<46} ║
║  Static files: {str(static_dir):<42} ║
╠══════════════════════════════════════════════════════════════╣
║  Viewer:    http://{args.host}:{args.port}/                            ║
║  TileJSON:  http://{args.host}:{args.port}/tilejson.json               ║
║  Tiles:     http://{args.host}:{args.port}/tiles/{{z}}/{{x}}/{{y}}.pbf         ║
╠══════════════════════════════════════════════════════════════╣
║  Press Ctrl+C to stop                                        ║
╚══════════════════════════════════════════════════════════════╝
''')
    
    with socketserver.TCPServer((args.host, args.port), NginxLikeTileHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\nServer stopped.')
    
    return 0


if __name__ == '__main__':
    exit(main())
