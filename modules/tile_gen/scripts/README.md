# Tile Generation Scripts

These are self-contained Python scripts for tile generation utilities.

## Scripts

| Script | Purpose |
|--------|---------|
| `extract_mbtiles.py` | Extract mbtiles to directory structure with deduplication |
| `shrink_btrfs.py` | Shrink btrfs images to minimize storage |

## Contour Tile Generation

Contour tiles are generated using the main `tile_gen.py` CLI:

```bash
# Generate contour tiles for Monaco (small test area)
tile_gen.py make-contour-tiles monaco

# Generate with options
tile_gen.py make-contour-tiles monaco --skip-download --upload