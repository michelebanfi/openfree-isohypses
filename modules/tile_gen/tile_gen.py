#!/usr/bin/env python3
from datetime import datetime, timezone

import click
from tile_gen_lib.btrfs import make_btrfs
from tile_gen_lib.planetiler import run_planetiler
from tile_gen_lib.rclone import make_indexes_for_bucket, upload_area
from tile_gen_lib.set_version import check_and_set_version
from tile_gen_lib.contour_gen import run_contour_generation, CONTOUR_AREAS


now = datetime.now(timezone.utc)


@click.group()
def cli():
    """
    Generates tiles and uploads to CloudFlare
    """


@cli.command()
@click.argument('area', required=True)
@click.option('--upload', is_flag=True, help='Upload after generation is complete')
def make_tiles(area, upload):
    """
    Generate tiles for a given area, optionally upload it to the btrfs bucket
    """

    print(f'---\n{now}\nStarting make-tiles {area} upload: {upload}')

    run_folder = run_planetiler(area)
    make_btrfs(run_folder)

    if upload:
        upload_area(area)


@cli.command(name='upload-area')
@click.argument('area', required=True)
def upload_area_(area):
    """
    Upload all runs from a given area to the btrfs bucket
    """

    print(f'---\n{now}\nStarting upload-area {area}')

    upload_area(area)


@cli.command()
def make_indexes():
    """
    Make indexes for all buckets
    """

    print(f'---\n{now}\nStarting make-indexes')

    for bucket in ['ofm-btrfs', 'ofm-assets']:
        make_indexes_for_bucket(bucket)


@cli.command()
@click.argument('area', required=True)
@click.option(
    '--version', default='latest', help='Optional version string, like "20231227_043106_pt"'
)
def set_version(area, version):
    """
    Set versions for a given area
    """

    print(f'---\n{now}\nStarting set-version {area}')

    check_and_set_version(area, version)


@cli.command()
@click.argument('area', required=True, type=click.Choice(list(CONTOUR_AREAS.keys())))
@click.option('--skip-download', is_flag=True, help='Skip terrain download, use cached tiles')
@click.option('--skip-btrfs', is_flag=True, help='Skip btrfs image creation (for testing)')
@click.option('--upload', is_flag=True, help='Upload after generation is complete')
def make_contour_tiles(area, skip_download, skip_btrfs, upload):
    """
    Generate contour line tiles for a given area.
    
    Downloads terrain data from AWS, generates contour lines using GDAL,
    and converts them to vector tiles using Tippecanoe.
    
    Available areas: monaco, luxembourg, alps-sample
    """
    
    print(f'---\n{now}\nStarting make-contour-tiles {area}')
    print(f'Options: skip_download={skip_download}, skip_btrfs={skip_btrfs}, upload={upload}')
    
    run_folder = run_contour_generation(area, skip_download=skip_download)
    
    if not skip_btrfs:
        make_btrfs(run_folder)
    
    if upload:
        upload_area(f'contour_{area}')
    
    print(f'\n=== Done! ===')
    print(f'Run folder: {run_folder}')


if __name__ == '__main__':
    cli()
