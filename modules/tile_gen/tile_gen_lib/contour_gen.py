#!/usr/bin/env python3
"""
Contour line tile generation module.

Downloads terrain data from AWS Terrain Tiles, generates contour lines using GDAL,
and converts them to vector tiles using Tippecanoe.

Pipeline: AWS GeoTIFF → gdal_contour → GeoJSON → Tippecanoe → mbtiles
"""

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .btrfs import cleanup_folder
from .config import config


# AWS Terrain Tiles - publicly accessible, no credentials needed
AWS_TERRAIN_BUCKET = 'https://s3.amazonaws.com/elevation-tiles-prod'

# Predefined bounding boxes for test areas (west, south, east, north)
CONTOUR_AREAS = {
    'monaco': (7.38, 43.71, 7.45, 43.76),
    'luxembourg': (5.73, 49.44, 6.53, 50.18),
    'alps-sample': (6.5, 45.5, 7.5, 46.5),  # Small Alpine region for testing
}

# Default contour intervals in meters
DEFAULT_INTERVALS = [10, 50, 100]


def lat_lon_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """Convert lat/lon to tile coordinates at given zoom level."""
    import math
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
    """
    Download GeoTIFF terrain tiles from AWS for the given bounding box.
    
    Args:
        bbox: (west, south, east, north) in WGS84
        output_dir: Directory to save downloaded tiles
        zoom: Zoom level (10 = ~30m resolution, good for contours)
    
    Returns:
        List of downloaded file paths
    """
    tiles_dir = output_dir / 'terrain_tiles'
    tiles_dir.mkdir(parents=True, exist_ok=True)
    
    tiles = get_tiles_for_bbox(bbox, zoom)
    downloaded = []
    
    print(f'Downloading {len(tiles)} terrain tiles at zoom {zoom}...')
    
    for z, x, y in tiles:
        url = f'{AWS_TERRAIN_BUCKET}/geotiff/{z}/{x}/{y}.tif'
        output_file = tiles_dir / f'{z}_{x}_{y}.tif'
        
        if output_file.exists():
            print(f'  Skipping {output_file.name} (already exists)')
            downloaded.append(output_file)
            continue
        
        print(f'  Downloading {url}...')
        try:
            result = subprocess.run(
                ['curl', '-f', '-s', '-o', str(output_file), url],
                check=True,
                capture_output=True,
                text=True
            )
            downloaded.append(output_file)
        except subprocess.CalledProcessError as e:
            print(f'  Warning: Failed to download {url}: {e}')
            if output_file.exists():
                output_file.unlink()
    
    print(f'Downloaded {len(downloaded)} tiles')
    return downloaded


def merge_terrain_tiles(tile_files: list[Path], output_vrt: Path) -> Path:
    """
    Create a virtual mosaic (VRT) from multiple GeoTIFF files.
    
    Args:
        tile_files: List of GeoTIFF file paths
        output_vrt: Output VRT file path
    
    Returns:
        Path to the created VRT file
    """
    print(f'Creating virtual mosaic from {len(tile_files)} tiles...')
    
    # gdalbuildvrt creates a virtual dataset that references all input files
    cmd = ['gdalbuildvrt', str(output_vrt)] + [str(f) for f in tile_files]
    subprocess.run(cmd, check=True, capture_output=True)
    
    print(f'Created VRT: {output_vrt}')
    return output_vrt


def generate_contours(
    input_raster: Path,
    output_dir: Path,
    intervals: list[int] = None,
    bbox: tuple = None
) -> list[Path]:
    """
    Generate contour lines from a DEM raster using gdal_contour.
    
    Args:
        input_raster: Path to input DEM (GeoTIFF or VRT)
        output_dir: Directory for output GeoJSON files
        intervals: List of contour intervals in meters (default: [10, 50, 100])
        bbox: Optional bounding box to clip output (west, south, east, north)
    
    Returns:
        List of generated GeoJSON file paths
    """
    if intervals is None:
        intervals = DEFAULT_INTERVALS
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # First, reproject the raster from Web Mercator (EPSG:3857) to WGS84 (EPSG:4326)
    # This ensures tippecanoe receives data in the expected projection
    reprojected_raster = output_dir / 'terrain_wgs84.tif'
    print('Reprojecting terrain to WGS84...')
    
    reproject_cmd = [
        'gdalwarp',
        '-t_srs', 'EPSG:4326',
        '-r', 'bilinear',
        '-overwrite',
        str(input_raster),
        str(reprojected_raster)
    ]
    
    with open(output_dir / 'gdalwarp.log', 'w') as log:
        subprocess.run(reproject_cmd, check=True, stdout=log, stderr=log)
    
    geojson_files = []
    
    for interval in intervals:
        output_file = output_dir / f'contours_{interval}m.geojson'
        print(f'Generating {interval}m contours...')
        
        cmd = [
            'gdal_contour',
            '-a', 'ele',           # Attribute name for elevation
            '-i', str(interval),   # Contour interval
            '-f', 'GeoJSON',       # Output format
        ]
        
        # Add bounding box clipping if specified
        if bbox:
            west, south, east, north = bbox
            # Note: gdal_contour doesn't have direct bbox, we'll handle this via tippecanoe
        
        cmd.extend([str(reprojected_raster), str(output_file)])
        
        with open(output_dir / f'gdal_contour_{interval}m.log', 'w') as log:
            subprocess.run(cmd, check=True, stdout=log, stderr=log)
        
        geojson_files.append(output_file)
        
        # Get file size for logging
        size_mb = output_file.stat().st_size / (1024 * 1024)
        print(f'  Created {output_file.name} ({size_mb:.1f} MB)')
    
    return geojson_files


def run_tippecanoe(
    geojson_files: list[Path],
    output_mbtiles: Path,
    min_zoom: int = 8,
    max_zoom: int = 14
) -> Path:
    """
    Convert GeoJSON contours to vector tiles using Tippecanoe.
    
    Args:
        geojson_files: List of GeoJSON files to process
        output_mbtiles: Output mbtiles file path
        min_zoom: Minimum zoom level
        max_zoom: Maximum zoom level
    
    Returns:
        Path to the created mbtiles file
    """
    print(f'Running Tippecanoe on {len(geojson_files)} GeoJSON files...')
    
    cmd = [
        'tippecanoe',
        '-o', str(output_mbtiles),
        f'-z{max_zoom}',
        f'-Z{min_zoom}',
        '-l', 'contour',           # Layer name
        '-y', 'ele',               # Keep only elevation attribute
        '--drop-densest-as-needed',
        '--extend-zooms-if-still-dropping',
        '--force',                 # Overwrite existing file
        '--attribution', 'Terrain data: AWS Terrain Tiles (SRTM, USGS)',
    ]
    
    # Add all GeoJSON files as input
    cmd.extend([str(f) for f in geojson_files])
    
    log_file = output_mbtiles.parent / 'tippecanoe.log'
    with open(log_file, 'w') as log:
        subprocess.run(cmd, check=True, stdout=log, stderr=log)
    
    size_mb = output_mbtiles.stat().st_size / (1024 * 1024)
    print(f'Created {output_mbtiles} ({size_mb:.1f} MB)')
    
    return output_mbtiles


def add_contour_metadata(mbtiles_path: Path, area: str):
    """Add metadata to the mbtiles file for compatibility with extract_mbtiles.py"""
    import sqlite3
    
    conn = sqlite3.connect(mbtiles_path)
    c = conn.cursor()
    
    # Check if metadata table exists
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='metadata'")
    if not c.fetchone():
        c.execute('CREATE TABLE metadata (name TEXT, value TEXT)')
    
    # Add required metadata
    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    metadata = {
        'name': 'OpenFreeMap Contours',
        'description': 'Contour lines from AWS Terrain Tiles',
        'attribution': '<a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> Terrain: AWS/SRTM/USGS',
        'format': 'pbf',
        'type': 'overlay',
        'osm_date': date_str,  # Required by extract_mbtiles.py
        'contour_area': area,
    }
    
    for name, value in metadata.items():
        c.execute('INSERT OR REPLACE INTO metadata (name, value) VALUES (?, ?)', (name, value))
    
    conn.commit()
    conn.close()
    print(f'Added metadata to {mbtiles_path}')


def run_contour_generation(area: str, skip_download: bool = False) -> Path:
    """
    Full contour generation pipeline for a given area.
    
    Args:
        area: Area name (must be in CONTOUR_AREAS) or 'custom'
        skip_download: If True, skip download and use cached terrain tiles
    
    Returns:
        Path to the run folder containing tiles.mbtiles
    """
    if area not in CONTOUR_AREAS:
        raise ValueError(f'Unknown area: {area}. Available: {list(CONTOUR_AREAS.keys())}')
    
    bbox = CONTOUR_AREAS[area]
    
    date = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    
    # Use a separate directory for contour runs
    area_dir = config.runs_dir / f'contour_{area}'
    
    # Clean up previous runs
    if area_dir.is_dir():
        for subdir in area_dir.iterdir():
            cleanup_folder(subdir)
        print('Cleaning up previous runs...')
        shutil.rmtree(area_dir, ignore_errors=True)
    
    run_folder = area_dir / f'{date}_contour'
    run_folder.mkdir(parents=True, exist_ok=True)
    
    os.chdir(run_folder)
    
    print(f'\n=== Contour Generation for {area} ===')
    print(f'Bounding box: {bbox}')
    print(f'Run folder: {run_folder}\n')
    
    # Step 1: Download terrain tiles
    if not skip_download:
        tile_files = download_terrain_tiles(bbox, run_folder, zoom=10)
    else:
        tiles_dir = run_folder / 'terrain_tiles'
        tile_files = list(tiles_dir.glob('*.tif')) if tiles_dir.exists() else []
        if not tile_files:
            raise RuntimeError('No cached terrain tiles found. Run without --skip-download first.')
        print(f'Using {len(tile_files)} cached terrain tiles')
    
    if not tile_files:
        raise RuntimeError('No terrain tiles downloaded. Check your internet connection.')
    
    # Step 2: Create virtual mosaic
    vrt_path = run_folder / 'terrain.vrt'
    merge_terrain_tiles(tile_files, vrt_path)
    
    # Step 3: Generate contours
    contours_dir = run_folder / 'contours'
    geojson_files = generate_contours(vrt_path, contours_dir, intervals=[10, 50], bbox=bbox)
    
    # Step 4: Convert to vector tiles
    mbtiles_path = run_folder / 'tiles.mbtiles'
    run_tippecanoe(geojson_files, mbtiles_path)
    
    # Step 5: Add metadata
    add_contour_metadata(mbtiles_path, area)
    
    # Step 6: Create osm_date file (required by btrfs.py)
    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    (run_folder / 'osm_date').write_text(date_str)
    
    print(f'\n=== Contour generation complete ===')
    print(f'Output: {mbtiles_path}')
    
    return run_folder
