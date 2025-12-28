# Tile Generation Scripts

These are self contained Python scripts that can be run outside of this project's environment.

## Contour Tile Generation & Serving

### Quick Start

1. **Generate contour tiles** (requires GDAL and tippecanoe):
   ```bash
   python test_contour_standalone.py --area monaco
   ```

2. **Extract mbtiles for nginx-style serving**:
   ```bash
   python setup_contour_nginx.py contours.mbtiles --output-dir ./contours_tiles
   ```

3. **Test locally with nginx-like server**:
   ```bash
   python nginx_test_server.py --tiles-dir ./contours_tiles/tiles --static-dir ./contours_tiles
   # Open http://127.0.0.1:8080 in browser
   ```

### Scripts Overview

| Script | Purpose |
|--------|---------|
| `test_contour_standalone.py` | Generate contour tiles from AWS elevation data |
| `setup_contour_nginx.py` | Extract mbtiles to directory structure + nginx config |
| `nginx_test_server.py` | Local nginx-like server for testing |
| `viewer.html` | Clean MapLibre GL viewer for contour tiles |
| `extract_mbtiles.py` | Extract deduplicated mbtiles (planetiler format) |
| `shrink_btrfs.py` | Shrink btrfs images |

### Production Deployment

The `setup_contour_nginx.py` script generates:
- `tiles/` - Directory structure (z/x/y.pbf) for nginx serving
- `nginx.conf` - Location block to include in nginx config
- `tilejson.json` - TileJSON metadata for map clients
- `metadata.json` - Original mbtiles metadata

Add the nginx location block to your server configuration:
```bash
include /path/to/contours_tiles/nginx.conf;
```

### Requirements

- Python 3.8+
- GDAL (for contour generation): `brew install gdal` / `apt install gdal-bin`
- tippecanoe (for tile generation): https://github.com/felt/tippecanoe
