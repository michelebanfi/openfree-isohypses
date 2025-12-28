#!/usr/bin/env python3
"""
Setup contour tiles for nginx serving.

This script:
1. Extracts mbtiles to directory structure (z/x/y.pbf)
2. Creates nginx config for serving contour tiles
3. Generates metadata.json for the tiles

Usage:
    python setup_contour_nginx.py /path/to/contours.mbtiles --output-dir /data/ofm/contours

The tiles will be served at: http://localhost/contours/{z}/{x}/{y}.pbf
"""

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


def flip_y(zoom: int, y: int) -> int:
    """Convert TMS y-coordinate to XYZ (slippy map) format."""
    return (2 ** zoom - 1) - y


def extract_mbtiles_standard(mbtiles_path: Path, output_dir: Path) -> dict:
    """
    Extract standard mbtiles (with 'tiles' table) to directory structure.
    Returns metadata dict.
    """
    tiles_dir = output_dir / 'tiles'
    tiles_dir.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(mbtiles_path)
    c = conn.cursor()
    
    # Get metadata
    metadata = dict(c.execute('SELECT name, value FROM metadata').fetchall())
    
    # Count tiles
    total = c.execute('SELECT COUNT(*) FROM tiles').fetchone()[0]
    print(f'Extracting {total} tiles...')
    
    # Extract tiles
    c.execute('SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles')
    
    extracted = 0
    for row in c:
        z = row[0]
        x = row[1]
        y = flip_y(z, row[2])  # Convert TMS to XYZ
        tile_data = row[3]
        
        tile_path = tiles_dir / str(z) / str(x) / f'{y}.pbf'
        tile_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(tile_path, 'wb') as f:
            f.write(tile_data)
        
        extracted += 1
        if extracted % 100 == 0 or extracted == total:
            print(f'  Extracted {extracted}/{total} tiles ({extracted/total*100:.1f}%)')
    
    conn.close()
    
    # Write metadata.json
    metadata_path = output_dir / 'metadata.json'
    with open(metadata_path, 'w') as f:
        json.dump(metadata, f, indent=2)
    print(f'Wrote metadata to {metadata_path}')
    
    return metadata


def create_nginx_config(output_dir: Path, url_path: str = '/contours') -> str:
    """
    Create nginx location block for serving contour tiles.
    Returns the nginx config string.
    """
    tiles_dir = output_dir / 'tiles'
    
    config = f'''
# Contour tiles location block
# Add this to your nginx server configuration

location {url_path}/ {{
    # Serve contour vector tiles
    alias {tiles_dir}/;
    try_files $uri @empty_tile;
    
    # Tiles are gzip-compressed
    add_header Content-Encoding gzip;
    
    # Long cache - tiles don't change often
    expires 10y;
    
    types {{
        application/vnd.mapbox-vector-tile pbf;
    }}
    
    # CORS headers
    add_header 'Access-Control-Allow-Origin' '*' always;
    add_header Cache-Control public;
    add_header X-Robots-Tag "noindex, nofollow" always;
    
    add_header x-ofm-debug 'contour tiles';
}}
'''
    return config


def create_tilejson(metadata: dict, base_url: str, output_path: Path):
    """Create a TileJSON file for the contour tiles."""
    
    # Parse vector_layers from metadata if present
    vector_layers = []
    if 'json' in metadata:
        try:
            json_meta = json.loads(metadata['json'])
            vector_layers = json_meta.get('vector_layers', [])
        except json.JSONDecodeError:
            pass
    
    tilejson = {
        "tilejson": "3.0.0",
        "name": metadata.get('name', 'Contour Tiles'),
        "description": metadata.get('description', 'Elevation contour lines'),
        "version": metadata.get('version', '1.0.0'),
        "attribution": metadata.get('attribution', ''),
        "scheme": "xyz",
        "tiles": [f"{base_url}/{{z}}/{{x}}/{{y}}.pbf"],
        "minzoom": int(metadata.get('minzoom', 0)),
        "maxzoom": int(metadata.get('maxzoom', 14)),
        "bounds": [float(x) for x in metadata.get('bounds', '-180,-85,180,85').split(',')],
        "center": [float(x) for x in metadata.get('center', '0,0,2').split(',')],
        "vector_layers": vector_layers
    }
    
    with open(output_path, 'w') as f:
        json.dump(tilejson, f, indent=2)
    
    print(f'Wrote TileJSON to {output_path}')


def main():
    parser = argparse.ArgumentParser(
        description='Setup contour tiles for nginx serving',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Extract and setup for local testing
  python setup_contour_nginx.py contours.mbtiles --output-dir ./contours_tiles

  # Setup for production server
  python setup_contour_nginx.py contours.mbtiles --output-dir /data/ofm/contours --base-url https://tiles.example.com/contours
'''
    )
    
    parser.add_argument('mbtiles_path', type=Path, help='Path to the mbtiles file')
    parser.add_argument('--output-dir', type=Path, required=True, help='Output directory for tiles')
    parser.add_argument('--url-path', default='/contours', help='URL path for nginx (default: /contours)')
    parser.add_argument('--base-url', default='http://localhost:8080/contours', help='Base URL for TileJSON')
    parser.add_argument('--force', action='store_true', help='Overwrite existing output directory')
    
    args = parser.parse_args()
    
    if not args.mbtiles_path.exists():
        sys.exit(f'Error: mbtiles file not found: {args.mbtiles_path}')
    
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        if args.force:
            print(f'Removing existing directory: {args.output_dir}')
            import shutil
            shutil.rmtree(args.output_dir)
        else:
            sys.exit(f'Error: output directory not empty: {args.output_dir}\nUse --force to overwrite')
    
    args.output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f'=== Setting up contour tiles ===')
    print(f'Input: {args.mbtiles_path}')
    print(f'Output: {args.output_dir}')
    print()
    
    # Extract tiles
    print('Step 1: Extracting tiles...')
    metadata = extract_mbtiles_standard(args.mbtiles_path, args.output_dir)
    print()
    
    # Create nginx config
    print('Step 2: Creating nginx config...')
    nginx_config = create_nginx_config(args.output_dir, args.url_path)
    nginx_config_path = args.output_dir / 'nginx.conf'
    with open(nginx_config_path, 'w') as f:
        f.write(nginx_config)
    print(f'Wrote nginx config to {nginx_config_path}')
    print()
    
    # Create TileJSON
    print('Step 3: Creating TileJSON...')
    tilejson_path = args.output_dir / 'tilejson.json'
    create_tilejson(metadata, args.base_url, tilejson_path)
    print()
    
    # Print summary
    print('=== Setup Complete ===')
    print()
    print('To serve with nginx:')
    print(f'  1. Include {nginx_config_path} in your nginx server block')
    print('  2. Reload nginx: sudo nginx -t && sudo systemctl reload nginx')
    print()
    print('To test locally with Python:')
    print(f'  cd {args.output_dir}')
    print(f'  python3 -m http.server 8080')
    print(f'  # Then access tiles at http://localhost:8080/tiles/{{z}}/{{x}}/{{y}}.pbf')
    print()
    print(f'TileJSON endpoint: {args.base_url}')


if __name__ == '__main__':
    main()
