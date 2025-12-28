#!/bin/bash
# =============================================================================
# OpenFreeMap Contour Tiles - Simple Self-Hosting Setup
# =============================================================================
#
# This script sets up a bare Ubuntu machine to serve contour tiles via nginx.
# Run as root or with sudo on a fresh Ubuntu 22+ machine.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/YOUR_REPO/setup_contours.sh | sudo bash
#   # Or download and run:
#   chmod +x setup_contours.sh
#   sudo ./setup_contours.sh
#
# =============================================================================

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_step() {
    echo -e "\n${BLUE}==>${NC} ${GREEN}$1${NC}"
}

print_warning() {
    echo -e "${YELLOW}Warning:${NC} $1"
}

print_error() {
    echo -e "${RED}Error:${NC} $1"
}

# =============================================================================
# Configuration
# =============================================================================

# Area to generate contours for (monaco is small and good for testing)
AREA="monaco"

# Bounding box: west, south, east, north
BBOX_WEST=7.38
BBOX_SOUTH=43.71
BBOX_EAST=7.45
BBOX_NORTH=43.76

# Zoom levels for contour tiles
MIN_ZOOM=8
MAX_ZOOM=14

# Directories
DATA_DIR="/data/ofm"
CONTOURS_DIR="${DATA_DIR}/contours"
TILES_DIR="${CONTOURS_DIR}/tiles"
WORK_DIR="${DATA_DIR}/work"
NGINX_SITES="/etc/nginx/sites-enabled"

# Terrain tiles source
TERRAIN_URL="https://s3.amazonaws.com/elevation-tiles-prod"

# =============================================================================
# Check if running as root
# =============================================================================

if [ "$EUID" -ne 0 ]; then
    print_error "Please run as root (sudo ./setup_contours.sh)"
    exit 1
fi

# =============================================================================
# Step 1: Install system dependencies
# =============================================================================

print_step "Installing system dependencies..."

apt-get update
apt-get install -y \
    build-essential \
    git \
    curl \
    wget \
    sqlite3 \
    libsqlite3-dev \
    zlib1g-dev \
    gdal-bin \
    python3-gdal \
    python3-pip \
    python3-venv \
    nginx \
    jq

# =============================================================================
# Step 2: Install tippecanoe
# =============================================================================

print_step "Installing tippecanoe..."

if command -v tippecanoe &> /dev/null; then
    echo "tippecanoe already installed: $(tippecanoe --version 2>&1 | head -1)"
else
    cd /tmp
    rm -rf tippecanoe
    git clone https://github.com/felt/tippecanoe.git
    cd tippecanoe
    make -j$(nproc)
    make install
    cd /
    rm -rf /tmp/tippecanoe
    echo "tippecanoe installed: $(tippecanoe --version 2>&1 | head -1)"
fi

# =============================================================================
# Step 3: Create directory structure
# =============================================================================

print_step "Creating directory structure..."

mkdir -p "${DATA_DIR}"
mkdir -p "${CONTOURS_DIR}"
mkdir -p "${TILES_DIR}"
mkdir -p "${WORK_DIR}"
mkdir -p "${WORK_DIR}/terrain"
mkdir -p "${WORK_DIR}/geojson"

# =============================================================================
# Step 4: Download terrain tiles
# =============================================================================

print_step "Downloading terrain elevation data for ${AREA}..."

# Function to convert lat/lon to tile coordinates
lat_lon_to_tile() {
    local lat=$1
    local lon=$2
    local zoom=$3
    
    local n=$(echo "2^$zoom" | bc)
    local x=$(echo "($lon + 180) / 360 * $n" | bc)
    local lat_rad=$(echo "$lat * 3.14159265359 / 180" | bc -l)
    local y=$(echo "(1 - l(s($lat_rad) + 1/c($lat_rad)) / 3.14159265359) / 2 * $n" | bc -l)
    
    echo "${x%.*} ${y%.*}"
}

# Download terrain tiles at zoom 10 (good resolution for contours)
TERRAIN_ZOOM=10

# Calculate tile range
n=$((2**TERRAIN_ZOOM))
min_x=$(echo "($BBOX_WEST + 180) / 360 * $n" | bc)
max_x=$(echo "($BBOX_EAST + 180) / 360 * $n" | bc)

# For Y, north has lower tile number
lat_rad_n=$(echo "$BBOX_NORTH * 3.14159265359 / 180" | bc -l)
lat_rad_s=$(echo "$BBOX_SOUTH * 3.14159265359 / 180" | bc -l)

min_y=$(python3 -c "import math; print(int((1 - math.asinh(math.tan(math.radians($BBOX_NORTH))) / math.pi) / 2 * $n))")
max_y=$(python3 -c "import math; print(int((1 - math.asinh(math.tan(math.radians($BBOX_SOUTH))) / math.pi) / 2 * $n))")

echo "Downloading tiles: z=$TERRAIN_ZOOM x=$min_x-$max_x y=$min_y-$max_y"

for x in $(seq $min_x $max_x); do
    for y in $(seq $min_y $max_y); do
        tile_file="${WORK_DIR}/terrain/${TERRAIN_ZOOM}_${x}_${y}.tif"
        if [ ! -f "$tile_file" ]; then
            url="${TERRAIN_URL}/geotiff/${TERRAIN_ZOOM}/${x}/${y}.tif"
            echo "  Downloading ${TERRAIN_ZOOM}/${x}/${y}.tif..."
            curl -sf -o "$tile_file" "$url" || echo "  (tile not available)"
        else
            echo "  Cached: ${TERRAIN_ZOOM}/${x}/${y}.tif"
        fi
    done
done

# =============================================================================
# Step 5: Process elevation data
# =============================================================================

print_step "Processing elevation data..."

cd "${WORK_DIR}"

# Create virtual mosaic of all tiles
gdalbuildvrt -overwrite terrain.vrt terrain/*.tif

# Reproject to WGS84 (EPSG:4326) for consistent processing
gdalwarp -overwrite -t_srs EPSG:4326 -r bilinear terrain.vrt terrain_wgs84.tif

# =============================================================================
# Step 6: Generate contour lines
# =============================================================================

print_step "Generating contour lines..."

# Generate contours at 10m intervals
gdal_contour -a ele -i 10 -f GeoJSON terrain_wgs84.tif geojson/contours.geojson

# Check if contours were generated
if [ ! -s "geojson/contours.geojson" ]; then
    print_error "Failed to generate contours"
    exit 1
fi

CONTOUR_COUNT=$(grep -c '"type":"Feature"' geojson/contours.geojson || echo "0")
echo "Generated ${CONTOUR_COUNT} contour features"

# =============================================================================
# Step 7: Create vector tiles with tippecanoe
# =============================================================================

print_step "Creating vector tiles with tippecanoe..."

rm -f contours.mbtiles

tippecanoe \
    -o contours.mbtiles \
    -l contour \
    --minimum-zoom=${MIN_ZOOM} \
    --maximum-zoom=${MAX_ZOOM} \
    --detect-shared-borders \
    --simplification=10 \
    --force \
    geojson/contours.geojson

# Verify mbtiles
TILE_COUNT=$(sqlite3 contours.mbtiles "SELECT COUNT(*) FROM tiles")
echo "Created mbtiles with ${TILE_COUNT} tiles"

# =============================================================================
# Step 8: Extract tiles to directory structure
# =============================================================================

print_step "Extracting tiles to directory structure..."

# Remove old tiles
rm -rf "${TILES_DIR}"/*

# Extract tiles using Python (handles TMS to XYZ conversion)
python3 << 'PYTHON_SCRIPT'
import sqlite3
import os
from pathlib import Path

mbtiles_path = os.environ.get('WORK_DIR', '/data/ofm/work') + '/contours.mbtiles'
tiles_dir = Path(os.environ.get('TILES_DIR', '/data/ofm/contours/tiles'))

def flip_y(zoom, y):
    return (2 ** zoom - 1) - y

conn = sqlite3.connect(mbtiles_path)
c = conn.cursor()

# Get metadata
metadata = dict(c.execute('SELECT name, value FROM metadata').fetchall())

# Count tiles
total = c.execute('SELECT COUNT(*) FROM tiles').fetchone()[0]
print(f'Extracting {total} tiles...')

# Extract tiles
c.execute('SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles')

for i, row in enumerate(c, 1):
    z = row[0]
    x = row[1]
    y = flip_y(z, row[2])  # Convert TMS to XYZ
    tile_data = row[3]
    
    tile_path = tiles_dir / str(z) / str(x) / f'{y}.pbf'
    tile_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(tile_path, 'wb') as f:
        f.write(tile_data)
    
    if i % 100 == 0 or i == total:
        print(f'  Extracted {i}/{total} tiles')

conn.close()

# Write metadata
import json
metadata_path = tiles_dir.parent / 'metadata.json'
with open(metadata_path, 'w') as f:
    json.dump(metadata, f, indent=2)

print(f'Done! Tiles extracted to {tiles_dir}')
PYTHON_SCRIPT

# =============================================================================
# Step 9: Create TileJSON
# =============================================================================

print_step "Creating TileJSON..."

cat > "${CONTOURS_DIR}/tilejson.json" << EOF
{
    "tilejson": "3.0.0",
    "name": "Contour Tiles",
    "description": "Elevation contour lines for ${AREA}",
    "version": "1.0.0",
    "scheme": "xyz",
    "tiles": ["/contours/{z}/{x}/{y}.pbf"],
    "minzoom": ${MIN_ZOOM},
    "maxzoom": ${MAX_ZOOM},
    "bounds": [${BBOX_WEST}, ${BBOX_SOUTH}, ${BBOX_EAST}, ${BBOX_NORTH}],
    "center": [$(echo "($BBOX_WEST + $BBOX_EAST) / 2" | bc -l), $(echo "($BBOX_SOUTH + $BBOX_NORTH) / 2" | bc -l), 12],
    "vector_layers": [{
        "id": "contour",
        "description": "Elevation contour lines",
        "minzoom": ${MIN_ZOOM},
        "maxzoom": ${MAX_ZOOM},
        "fields": {
            "ele": "Number"
        }
    }]
}
EOF

echo "TileJSON created at ${CONTOURS_DIR}/tilejson.json"

# =============================================================================
# Step 10: Create viewer HTML
# =============================================================================

print_step "Creating viewer HTML..."

cat > "${CONTOURS_DIR}/viewer.html" << 'VIEWER_HTML'
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Contour Tiles Viewer</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://unpkg.com/maplibre-gl@4.1.2/dist/maplibre-gl.js"></script>
    <link href="https://unpkg.com/maplibre-gl@4.1.2/dist/maplibre-gl.css" rel="stylesheet">
    <style>
        body { margin: 0; }
        #map { position: absolute; top: 0; bottom: 0; width: 100%; }
        .info {
            position: absolute;
            top: 10px;
            left: 10px;
            background: white;
            padding: 15px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.15);
            font-family: system-ui, sans-serif;
            font-size: 14px;
        }
        .info h3 { margin: 0 0 10px 0; }
        .info p { margin: 5px 0; color: #666; }
    </style>
</head>
<body>
    <div id="map"></div>
    <div class="info">
        <h3>🏔️ Contour Tiles</h3>
        <p>Zoom: <span id="zoom">-</span></p>
        <p>Center: <span id="center">-</span></p>
    </div>
    <script>
        const map = new maplibregl.Map({
            container: 'map',
            style: {
                version: 8,
                sources: {
                    'osm': {
                        type: 'raster',
                        tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
                        tileSize: 256,
                        attribution: '© OpenStreetMap'
                    },
                    'contours': {
                        type: 'vector',
                        tiles: [window.location.origin + '/contours/{z}/{x}/{y}.pbf'],
                        minzoom: 8,
                        maxzoom: 14
                    }
                },
                layers: [
                    { id: 'osm', type: 'raster', source: 'osm' },
                    {
                        id: 'contour-100m',
                        type: 'line',
                        source: 'contours',
                        'source-layer': 'contour',
                        filter: ['==', ['%', ['to-number', ['get', 'ele']], 100], 0],
                        paint: { 'line-color': '#8B4513', 'line-width': 2, 'line-opacity': 0.8 }
                    },
                    {
                        id: 'contour-50m',
                        type: 'line',
                        source: 'contours',
                        'source-layer': 'contour',
                        filter: ['all',
                            ['==', ['%', ['to-number', ['get', 'ele']], 50], 0],
                            ['!=', ['%', ['to-number', ['get', 'ele']], 100], 0]
                        ],
                        paint: { 'line-color': '#A0522D', 'line-width': 1 },
                        minzoom: 10
                    },
                    {
                        id: 'contour-labels',
                        type: 'symbol',
                        source: 'contours',
                        'source-layer': 'contour',
                        filter: ['==', ['%', ['to-number', ['get', 'ele']], 100], 0],
                        layout: {
                            'symbol-placement': 'line',
                            'text-field': ['concat', ['get', 'ele'], 'm'],
                            'text-size': 10
                        },
                        paint: {
                            'text-color': '#5D4037',
                            'text-halo-color': 'white',
                            'text-halo-width': 1
                        },
                        minzoom: 11
                    }
                ]
            },
            center: [7.42, 43.73],
            zoom: 12
        });
        
        map.addControl(new maplibregl.NavigationControl());
        
        function updateInfo() {
            document.getElementById('zoom').textContent = map.getZoom().toFixed(1);
            const c = map.getCenter();
            document.getElementById('center').textContent = c.lat.toFixed(4) + ', ' + c.lng.toFixed(4);
        }
        
        map.on('moveend', updateInfo);
        map.on('load', updateInfo);
    </script>
</body>
</html>
VIEWER_HTML

echo "Viewer created at ${CONTOURS_DIR}/viewer.html"

# =============================================================================
# Step 11: Configure nginx
# =============================================================================

print_step "Configuring nginx..."

# Create nginx config for contours
cat > /etc/nginx/sites-available/contours << 'NGINX_CONF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    
    server_name _;
    
    root /data/ofm/contours;
    
    # Viewer
    location = / {
        try_files /viewer.html =404;
    }
    
    location = /viewer.html {
        add_header 'Access-Control-Allow-Origin' '*' always;
    }
    
    # TileJSON
    location = /contours {
        alias /data/ofm/contours/tilejson.json;
        default_type application/json;
        add_header 'Access-Control-Allow-Origin' '*' always;
        add_header Cache-Control "public, max-age=3600";
    }
    
    # Tiles
    location /contours/ {
        alias /data/ofm/contours/tiles/;
        try_files $uri @empty_tile;
        
        add_header Content-Encoding gzip;
        expires 1y;
        
        types {
            application/vnd.mapbox-vector-tile pbf;
        }
        
        add_header 'Access-Control-Allow-Origin' '*' always;
        add_header Cache-Control "public, max-age=31536000";
    }
    
    # Empty tile fallback
    location @empty_tile {
        return 200 '';
        add_header Content-Type 'application/vnd.mapbox-vector-tile';
        add_header 'Access-Control-Allow-Origin' '*' always;
    }
}
NGINX_CONF

# Enable the site
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/contours /etc/nginx/sites-enabled/contours

# Test and reload nginx
nginx -t
systemctl reload nginx

# =============================================================================
# Step 12: Cleanup and summary
# =============================================================================

print_step "Cleaning up..."

# Optional: remove work files to save space
# rm -rf "${WORK_DIR}"

# Get server IP
SERVER_IP=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${GREEN}=============================================${NC}"
echo -e "${GREEN}   Contour Tiles Setup Complete!            ${NC}"
echo -e "${GREEN}=============================================${NC}"
echo ""
echo "Tiles directory: ${TILES_DIR}"
echo "Tile count: $(find ${TILES_DIR} -name '*.pbf' | wc -l)"
echo ""
echo -e "${BLUE}Test URLs:${NC}"
echo "  Viewer:    http://${SERVER_IP}/"
echo "  TileJSON:  http://${SERVER_IP}/contours"
echo "  Tile:      http://${SERVER_IP}/contours/12/2132/1493.pbf"
echo ""
echo -e "${BLUE}Test commands:${NC}"
echo "  curl -sI http://localhost/contours | head -5"
echo "  curl -sI http://localhost/contours/12/2132/1493.pbf | head -5"
echo ""
echo -e "${YELLOW}Note:${NC} If you have a firewall, make sure port 80 is open."
echo ""
