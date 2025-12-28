#!/usr/bin/env python3
"""
Standalone contour tile generation test script.
This script tests the full pipeline without the OpenFreeMap directory structure.

Usage:
    python test_contour_standalone.py [--area monaco|luxembourg|alps-sample]
"""

import os
import shutil
import subprocess
import sys
import math
from datetime import datetime, timezone
from pathlib import Path

# AWS Terrain Tiles - publicly accessible
AWS_TERRAIN_BUCKET = 'https://s3.amazonaws.com/elevation-tiles-prod'

# Test areas with bounding boxes (west, south, east, north)
AREAS = {
    'monaco': (7.38, 43.71, 7.45, 43.76),
    'luxembourg': (5.73, 49.44, 6.53, 50.18),
    'alps-sample': (6.5, 45.5, 7.5, 46.5),
}


def lat_lon_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """Convert lat/lon to tile coordinates at given zoom level."""
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def get_tiles_for_bbox(bbox: tuple, zoom: int) -> list[tuple[int, int, int]]:
    """Get all tile coordinates (z, x, y) that cover a bounding box."""
    west, south, east, north = bbox
    
    min_x, _ = lat_lon_to_tile(north, west, zoom)
    max_x, _ = lat_lon_to_tile(south, east, zoom)
    _, min_y = lat_lon_to_tile(north, west, zoom)
    _, max_y = lat_lon_to_tile(south, east, zoom)
    
    # Ensure min <= max
    if min_x > max_x:
        min_x, max_x = max_x, min_x
    if min_y > max_y:
        min_y, max_y = max_y, min_y
    
    tiles = []
    for x in range(min_x, max_x + 1):
        for y in range(min_y, max_y + 1):
            tiles.append((zoom, x, y))
    
    return tiles


def download_terrain_tiles(bbox: tuple, output_dir: Path, zoom: int = 10) -> list[Path]:
    """Download GeoTIFF terrain tiles from AWS for the given bounding box."""
    tiles_dir = output_dir / 'terrain_tiles'
    tiles_dir.mkdir(parents=True, exist_ok=True)
    
    tiles = get_tiles_for_bbox(bbox, zoom)
    downloaded = []
    
    print(f'Downloading {len(tiles)} terrain tiles at zoom {zoom}...')
    
    for z, x, y in tiles:
        url = f'{AWS_TERRAIN_BUCKET}/geotiff/{z}/{x}/{y}.tif'
        output_file = tiles_dir / f'{z}_{x}_{y}.tif'
        
        if output_file.exists():
            print(f'  Skipping {output_file.name} (cached)')
            downloaded.append(output_file)
            continue
        
        print(f'  Downloading tile {z}/{x}/{y}...')
        try:
            result = subprocess.run(
                ['curl', '-f', '-s', '-o', str(output_file), url],
                check=True,
                capture_output=True,
                text=True
            )
            downloaded.append(output_file)
        except subprocess.CalledProcessError as e:
            print(f'  Warning: Failed to download {url}')
            if output_file.exists():
                output_file.unlink()
    
    print(f'Downloaded {len(downloaded)} tiles')
    return downloaded


def merge_and_reproject(tile_files: list[Path], output_dir: Path) -> Path:
    """Create a virtual mosaic and reproject to WGS84."""
    print('Creating virtual mosaic and reprojecting to WGS84...')
    
    # Create VRT
    vrt_path = output_dir / 'terrain.vrt'
    cmd = ['gdalbuildvrt', str(vrt_path)] + [str(f) for f in tile_files]
    subprocess.run(cmd, check=True, capture_output=True)
    
    # Reproject to WGS84
    wgs84_path = output_dir / 'terrain_wgs84.tif'
    reproject_cmd = [
        'gdalwarp',
        '-t_srs', 'EPSG:4326',
        '-r', 'bilinear',
        '-overwrite',
        str(vrt_path),
        str(wgs84_path)
    ]
    subprocess.run(reproject_cmd, check=True, capture_output=True)
    
    return wgs84_path


def generate_contours(input_raster: Path, output_dir: Path, intervals: list[int]) -> list[Path]:
    """Generate contour lines from a DEM raster."""
    output_dir.mkdir(parents=True, exist_ok=True)
    geojson_files = []
    
    for interval in intervals:
        output_file = output_dir / f'contours_{interval}m.geojson'
        print(f'Generating {interval}m contours...')
        
        cmd = [
            'gdal_contour',
            '-a', 'ele',
            '-i', str(interval),
            '-f', 'GeoJSON',
            str(input_raster),
            str(output_file)
        ]
        
        subprocess.run(cmd, check=True, capture_output=True)
        geojson_files.append(output_file)
        
        size_kb = output_file.stat().st_size / 1024
        print(f'  Created {output_file.name} ({size_kb:.1f} KB)')
    
    return geojson_files


def run_tippecanoe(geojson_files: list[Path], output_mbtiles: Path) -> Path:
    """Convert GeoJSON contours to vector tiles."""
    print(f'Running Tippecanoe on {len(geojson_files)} GeoJSON files...')
    
    cmd = [
        'tippecanoe',
        '-o', str(output_mbtiles),
        '-z14', '-Z8',
        '-l', 'contour',
        '-y', 'ele',
        '--drop-densest-as-needed',
        '--extend-zooms-if-still-dropping',
        '--force',
        '--attribution', 'Terrain: AWS/SRTM/USGS',
    ]
    cmd.extend([str(f) for f in geojson_files])
    
    subprocess.run(cmd, check=True, capture_output=True)
    
    size_kb = output_mbtiles.stat().st_size / 1024
    print(f'Created {output_mbtiles} ({size_kb:.1f} KB)')
    
    return output_mbtiles


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Generate contour line tiles')
    parser.add_argument('--area', choices=list(AREAS.keys()), default='monaco',
                        help='Area to generate contours for')
    parser.add_argument('--output', default='/tmp/contour_gen',
                        help='Output directory')
    parser.add_argument('--intervals', default='10,50',
                        help='Contour intervals in meters (comma-separated)')
    args = parser.parse_args()
    
    bbox = AREAS[args.area]
    intervals = [int(i) for i in args.intervals.split(',')]
    output_dir = Path(args.output) / args.area
    
    # Clean up previous run
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    
    print(f'\n{"="*50}')
    print(f'Contour Generation: {args.area}')
    print(f'Bounding box: {bbox}')
    print(f'Intervals: {intervals}m')
    print(f'Output: {output_dir}')
    print(f'{"="*50}\n')
    
    # Step 1: Download terrain
    tile_files = download_terrain_tiles(bbox, output_dir, zoom=10)
    if not tile_files:
        print('ERROR: No terrain tiles downloaded')
        sys.exit(1)
    
    # Step 2: Merge and reproject
    wgs84_raster = merge_and_reproject(tile_files, output_dir)
    
    # Step 3: Generate contours
    contours_dir = output_dir / 'contours'
    geojson_files = generate_contours(wgs84_raster, contours_dir, intervals)
    
    # Step 4: Create vector tiles
    mbtiles_path = output_dir / 'contours.mbtiles'
    run_tippecanoe(geojson_files, mbtiles_path)
    
    print(f'\n{"="*50}')
    print('SUCCESS!')
    print(f'{"="*50}')
    print(f'\nOutput files:')
    print(f'  - {mbtiles_path}')
    print(f'\nTo view the tiles:')
    print(f'  1. Open in QGIS: Layer > Add Layer > Add Vector Tile Layer')
    print(f'  2. Or use: tippecanoe-decode {mbtiles_path} 12 535 378')
    print()


if __name__ == '__main__':
    main()
