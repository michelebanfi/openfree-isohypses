# Contour Tiles - Self-hosting Howto

This guide explains how to deploy contour tiles alongside your existing OpenFreeMap http-host setup.

## Overview

Contour tiles show elevation lines on maps. They are generated from terrain elevation data and served as vector tiles, just like the main OpenFreeMap tiles.

**Requirements:**
- Existing http-host setup (see [self_hosting.md](self_hosting.md))
- Additional ~1-10 GB disk space depending on coverage area
- GDAL and tippecanoe (for generating new tiles)

---

## Quick Start (Using Pre-generated Tiles)

If you have pre-generated contour mbtiles, follow these steps:

### 1. Extract mbtiles to directory structure

On your **local machine** (or the server), run:

```bash
cd openfreemap/modules/tile_gen/scripts

# Extract tiles for nginx serving
python setup_contour_nginx.py /path/to/contours.mbtiles \
    --output-dir /data/ofm/contours \
    --base-url https://your-domain.com/contours
```

This creates:
- `tiles/` - Directory structure (z/x/y.pbf) for nginx
- `nginx.conf` - Location block to include
- `tilejson.json` - TileJSON metadata
- `metadata.json` - Original mbtiles metadata

### 2. Add nginx configuration

Add the contours location block to your nginx config:

```bash
# Copy the generated nginx config
sudo cp /data/ofm/contours/nginx.conf /data/nginx/sites/contours.conf

# Or include it in your existing config
# Add this line inside your server block:
#   include /data/ofm/contours/nginx.conf;

# Test and reload nginx
sudo nginx -t && sudo systemctl reload nginx
```

### 3. Verify deployment

```bash
# Test tilejson endpoint
curl -sI https://your-domain.com/contours | head -5

# Test a tile
curl -sI https://your-domain.com/contours/12/2132/1493.pbf | head -5

# Expected response:
# HTTP/2 200
# content-type: application/vnd.mapbox-vector-tile
# content-encoding: gzip
# access-control-allow-origin: *
```

---

## Generating Contour Tiles

If you need to generate contour tiles from scratch:

### Prerequisites

```bash
# Ubuntu/Debian
sudo apt install gdal-bin python3-gdal

# Build tippecanoe
git clone https://github.com/felt/tippecanoe.git
cd tippecanoe
make -j
sudo make install
```

### Generate tiles

```bash
cd openfreemap/modules/tile_gen/scripts

# Generate contours for a specific area
python test_contour_standalone.py --area monaco

# Or for a custom bounding box (west, south, east, north)
# Edit the AREAS dict in test_contour_standalone.py

# Output: contours_{area}.mbtiles
```

### Full pipeline

```bash
# 1. Generate mbtiles
python test_contour_standalone.py --area monaco

# 2. Extract for nginx
python setup_contour_nginx.py contours_monaco.mbtiles \
    --output-dir /data/ofm/contours/monaco \
    --base-url https://your-domain.com/contours/monaco

# 3. Deploy nginx config (on server)
sudo cp /data/ofm/contours/monaco/nginx.conf /data/nginx/sites/contours_monaco.conf
sudo nginx -t && sudo systemctl reload nginx
```

---

## Local Testing

Before deploying to production, test locally:

```bash
cd openfreemap/modules/tile_gen/scripts

# Start nginx-like test server
python nginx_test_server.py \
    --tiles-dir /path/to/contours/tiles \
    --static-dir /path/to/contours \
    --port 8080

# Open http://127.0.0.1:8080 in browser
```

The test server mimics nginx behavior:
- Serves tiles from file system (z/x/y.pbf)
- Returns empty tile for missing tiles
- Proper CORS and caching headers
- Includes built-in MapLibre viewer

---

## Using Contour Tiles in Your Map

### TileJSON endpoint

```
https://your-domain.com/contours
```

### Direct tile URL

```
https://your-domain.com/contours/{z}/{x}/{y}.pbf
```

### MapLibre GL JS Example

```javascript
map.addSource('contours', {
    type: 'vector',
    url: 'https://your-domain.com/contours'  // TileJSON
    // Or direct tiles:
    // tiles: ['https://your-domain.com/contours/{z}/{x}/{y}.pbf'],
    // minzoom: 8,
    // maxzoom: 14
});

map.addLayer({
    id: 'contour-lines',
    type: 'line',
    source: 'contours',
    'source-layer': 'contour',
    paint: {
        'line-color': '#8B4513',
        'line-width': 1
    }
});
```

---

## Directory Structure

After deployment, your server should have:

```
/data/ofm/
├── http_host/          # Main OpenFreeMap tiles (existing)
│   ├── runs/
│   └── assets/
├── contours/           # Contour tiles (new)
│   ├── tiles/          # z/x/y.pbf structure
│   ├── nginx.conf      # Nginx location block
│   ├── tilejson.json   # TileJSON metadata
│   └── metadata.json   # Original metadata
└── config/             # Configuration files

/mnt/ofm/               # Mounted btrfs images (main tiles)

/data/nginx/
├── sites/
│   ├── ofm_direct.conf      # Main tiles config
│   └── contours.conf        # Contour tiles config (new)
└── certs/
```

---

## Nginx Configuration Details

The generated `nginx.conf` contains:

```nginx
location /contours/ {
    alias /data/ofm/contours/tiles/;
    try_files $uri @empty_tile;
    
    add_header Content-Encoding gzip;
    expires 10y;
    
    types {
        application/vnd.mapbox-vector-tile pbf;
    }
    
    add_header 'Access-Control-Allow-Origin' '*' always;
    add_header Cache-Control public;
    add_header x-ofm-debug 'contour tiles';
}
```

This configuration:
- Serves pre-compressed gzip tiles efficiently
- Returns empty responses for missing tiles (no 404 errors)
- Sets long cache expiry (tiles rarely change)
- Enables CORS for web map clients

---

## Troubleshooting

### Tiles not loading

1. Check nginx config syntax: `sudo nginx -t`
2. Check file permissions: `ls -la /data/ofm/contours/tiles/`
3. Check nginx error log: `tail -f /var/log/nginx/error.log`

### Wrong content type

Ensure nginx has the correct MIME type:
```nginx
types {
    application/vnd.mapbox-vector-tile pbf;
}
```

### CORS errors

Verify the `Access-Control-Allow-Origin` header is present:
```bash
curl -sI https://your-domain.com/contours/12/2132/1493.pbf | grep -i access
```

### Empty map (no contours visible)

1. Check zoom level (contours typically visible at zoom 8+)
2. Verify source-layer name matches (`contour`)
3. Check browser console for tile loading errors
