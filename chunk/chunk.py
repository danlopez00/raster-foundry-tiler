import os
import errno
import json
import math
import boto3
import mercantile
import numpy
import rasterio

from urlparse import urlparse
from collections import namedtuple

from rasterio import transform
from rasterio import warp
from rasterio.warp import calculate_default_transform
from rasterio._io import virtual_file_to_buffer

APP_NAME = "Raster Foundry Tiler Chunk"
TILE_DIM = 1024
STATUS_QUEUE_REGION = "us-east-1"


def notify(queue_url, m):
    if not queue_url:
        return

    client = boto3.client('sqs', region_name=STATUS_QUEUE_REGION)
    res = client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(m)
    )

    if res['ResponseMetadata']['HTTPStatusCode'] != 200:
        raise Exception(json.dumps(res))


def notify_start(queue_url, job_id):
    notify(queue_url, {
        "jobId": job_id,
        "stage": "chunk",
        "status": "STARTED"
    })


def notify_success(queue_url, job_id):
    notify(queue_url, {
        "jobId": job_id,
        "stage": "chunk",
        "status": "FINISHED"
    })


def notify_failure(queue_url, job_id, error_message):
    notify(queue_url, {
        "jobId": job_id,
        "stage": "chunk",
        "status": "FAILED",
        "error": error_message
    })


def get_filename(uri):
    return os.path.splitext(os.path.basename(uri))[0]


def mkdir_p(dir):
    try:
        os.makedirs(dir)
    except OSError as exc:  # Python >2.5
        if exc.errno == errno.EEXIST and os.path.isdir(dir):
            pass
        else:
            raise

UriSet = namedtuple(
    'UriSet',
    'source_uri workspace_target workspace_source_uri image_folder order')
ImageSource = namedtuple(
    'ImageSource',
    'origin_uri source_uri src_bounds src_shape src_crs zoom ll_bounds tile_bounds image_folder order')  # NOQA
ChunkTask = namedtuple('ChunkTask', "source_uri target_meta target")


def vsi_curlify(uri):
    """
    Creates a GDAL-readable path from the given URI
    """
    parsed = urlparse(uri)
    result_uri = ""
    if not parsed.scheme:
        result_uri = uri
    else:
        if parsed.scheme == "s3":
            result_uri = "/vsicurl/http://%s.s3.amazonaws.com%s" % (
                parsed.netloc, parsed.path)
        elif parsed.scheme == "http":
            result_uri = "/vsicurl/%s" % uri
        else:
            raise Exception("Unsupported scheme: %s" % parsed.schem)

    return result_uri


def write_bytes_to_target(target_uri, contents):
    parsed_target = urlparse(target_uri)
    if parsed_target.scheme == "s3":
        client = boto3.client("s3")

        bucket = parsed_target.netloc
        key = parsed_target.path[1:]

        client.put_object(
            ACL="public-read",
            Body=bytes(contents),
            Bucket=bucket,
            # CacheControl="TODO",
            ContentType="image/tiff",
            Key=key
        )
    else:
        output_path = target_uri
        mkdir_p(os.path.dirname(output_path))

        with open(output_path, "w") as f:
            f.write(contents)


def create_uri_sets(images, workspace_uri):
    result = []
    workspace_keys = []
    for (order, uri) in enumerate(images):
        source_uri = vsi_curlify(uri)

        # Get the workspace
        workspace_key = get_filename(uri)
        i = 1
        while workspace_key in workspace_keys:
            if i > 2:
                workspace_key = workspace_key[:-2] + "-" + str(i)
            else:
                workspace_key = workspace_key + "-" + str(i)
            i + 1
        workspace_keys.append(workspace_key)

        workspace_target = os.path.join(workspace_uri,
                                        "%s-workingcopy.tif" % workspace_key)
        workspace_source_uri = vsi_curlify(workspace_target)

        image_folder = os.path.join(workspace_uri, workspace_key)

        uri_set = UriSet(source_uri=source_uri,
                         workspace_target=workspace_target,
                         workspace_source_uri=workspace_source_uri,
                         image_folder=image_folder,
                         order=order)

        result.append(uri_set)

    return result


def copy_to_workspace(source_uri, dest_uri):
    """
    Translates an image from a URI to a compressed, tiled GeoTIFF
    version in the workspace
    """

    creation_options = {
        "driver": "GTiff",
        "tiled": True,
        "compress": "lzw",
        "predictor": 2,
        "sparse_ok": True,
        "blockxsize": 512,
        "blockysize": 512
    }

    with rasterio.open(source_uri, "r") as src:
        meta = src.meta.copy()
        meta.update(creation_options)

        tmp_path = "/vsimem/" + get_filename(dest_uri)

        with rasterio.open(tmp_path, "w", **meta) as tmp:
            tmp.write(src.read())

    contents = bytearray(virtual_file_to_buffer(tmp_path))

    write_bytes_to_target(dest_uri, contents)


def get_zoom(resolution, tile_dim):
    zoom = math.log((2 * math.pi * 6378137) / (resolution * tile_dim)) / math.log(2)  # NOQA
    if zoom - int(zoom) > 0.20:
        return int(zoom) + 1
    else:
        return int(zoom)


def create_image_source(origin_uri, source_uri, image_folder, order, tile_dim):
    with rasterio.drivers():
        with rasterio.open(source_uri) as src:
            (ll_transform, ll_cols, ll_rows) = \
                calculate_default_transform(src.crs,
                                            "EPSG:4326",
                                            src.shape[0],
                                            src.shape[1],
                                            src.bounds.left,
                                            src.bounds.bottom,
                                            src.bounds.right,
                                            src.bounds.top)
            w, n = ll_transform.xoff, ll_transform.yoff
            e, s = ll_transform * (ll_cols, ll_rows)
            ll_bounds = [w, s, e, n]

            (wm_transform, _, _) = \
                calculate_default_transform(src.crs,
                                            "EPSG:3857",
                                            src.shape[0],
                                            src.shape[1],
                                            src.bounds.left,
                                            src.bounds.bottom,
                                            src.bounds.right,
                                            src.bounds.top)

            resolution = max(abs(wm_transform[0]), abs(wm_transform[4]))
            zoom = get_zoom(resolution, tile_dim)
            min_tile = mercantile.tile(ll_bounds[0], ll_bounds[3], zoom)
            max_tile = mercantile.tile(ll_bounds[2], ll_bounds[1], zoom)

            return ImageSource(origin_uri=origin_uri,
                               source_uri=source_uri,
                               src_bounds=src.bounds,
                               src_shape=src.shape,
                               src_crs=src.crs,
                               zoom=zoom,
                               ll_bounds=ll_bounds,
                               tile_bounds=[min_tile.x, min_tile.y,
                                            max_tile.x, max_tile.y],
                               image_folder=image_folder,
                               order=order)


def generate_chunk_tasks(image_source, tile_dim):
    tasks = []
    zoom = image_source.zoom
    (min_col, max_col) = (image_source.tile_bounds[0],
                          image_source.tile_bounds[2])
    (min_row, max_row) = (image_source.tile_bounds[1],
                          image_source.tile_bounds[3])

    for tile_col in range(min_col, min(max_col + 1, 2 ** zoom)):
        for tile_row in range(min_row, min(max_row + 1, 2 ** zoom)):
            tile_bounds = mercantile.bounds(tile_col, tile_row, zoom)
            (wm_left, wm_bottom, wm_right, wm_top) = \
                warp.transform_bounds("EPSG:4326",
                                      "EPSG:3857",
                                      tile_bounds.west,
                                      tile_bounds.south,
                                      tile_bounds.east,
                                      tile_bounds.north)
            affine = transform.from_bounds(wm_left, wm_bottom, wm_right,
                                           wm_top, tile_dim, tile_dim)
            target_meta = {
                "transform": affine[:6],
                "width": tile_dim,
                "height": tile_dim
            }

            target = os.path.join(image_source.image_folder,
                                  "%d/%d/%d.tif" % (zoom, tile_col, tile_row))
            task = ChunkTask(source_uri=image_source.source_uri,
                             target_meta=target_meta,
                             target=target)

            tasks.append(task)

    return tasks


def process_chunk_task(task):
    """
    Chunks the image into tile_dim x tile_dim tiles,
    and saves them to the target folder (s3 or local)

    Returns the extent of the output raster.
    """

    from rasterio.warp import RESAMPLING

    creation_options = {
        "driver": "GTiff",
        "crs": "EPSG:3857",
        "tiled": True,
        "compress": "deflate",
        "predictor": 2,  # 3 for floats, 2 otherwise
        "sparse_ok": True
    }

    with rasterio.open(task.source_uri, "r") as src:
        meta = src.meta.copy()
        meta.update(creation_options)
        meta.update(task.target_meta)

        cols = meta["width"]
        rows = meta["height"]

        tmp_path = "/vsimem/" + get_filename(task.target)

        with rasterio.open(tmp_path, "w", **meta) as tmp:
            # Reproject the src dataset into image tile.
            warped = []
            for bidx in src.indexes:
                source = rasterio.band(src, bidx)
                warped.append(numpy.zeros((cols, rows), dtype=meta['dtype']))

                warp.reproject(
                    source=source,
                    src_nodata=0,
                    destination=warped[bidx - 1],
                    dst_transform=meta["transform"],
                    dst_crs=meta["crs"],
                    resampling=RESAMPLING.bilinear,
                )

            # check for chunks containing only zero values
            if not any(map(lambda b: b.any(), warped)):
                return

            # write out our warped data to the vsimem raster
            for bidx in src.indexes:
                tmp.write_band(bidx, warped[bidx - 1])

    contents = bytearray(virtual_file_to_buffer(tmp_path))

    write_bytes_to_target(task.target, contents)


def construct_image_info(image_source):
    extent = {"xmin": image_source.ll_bounds[0],
              "ymin": image_source.ll_bounds[1],
              "xmax": image_source.ll_bounds[2],
              "ymax": image_source.ll_bounds[3]}

    grid_bounds = {"colMin": image_source.tile_bounds[0],
                   "rowMin": image_source.tile_bounds[1],
                   "colMax": image_source.tile_bounds[2],
                   "rowMax": image_source.tile_bounds[3]}
    return {
        "sourceUri": image_source.origin_uri,
        "extent": extent,
        "zoom": image_source.zoom,
        "gridBounds": grid_bounds,
        "tiles": image_source.image_folder
    }


def run_spark_job(tile_dim, args):
    from pyspark import SparkConf, SparkContext
    from pyspark.accumulators import AccumulatorParam

    class ImageSourceAccumulatorParam(AccumulatorParam):
        """
        Accumulator that will collect our image data that will be
        included as part of the input to the next stage of processing.
        """
        def zero(self, dummy):
            return []

        def addInPlace(self, sources1, sources2):
            res = []
            if sources1:
                res.extend(sources1)
            if sources2:
                res.extend(sources2)
            return res

    status_queue = args['--status-queue']
    source_uris = args['<image>']
    workspace = args['--workspace']
    job_id = args['--job-id']
    target = args['--target']
    output_file = args['--output']

    notify_start(status_queue, job_id)

    try:
        uri_sets = create_uri_sets(source_uris, workspace)
        image_count = len(uri_sets)

        conf = SparkConf().setAppName(APP_NAME)
        sc = SparkContext(conf=conf)

        image_source_accumulator = \
            sc.accumulator([], ImageSourceAccumulatorParam())

        def create_image_sources(uri_set, acc):
            image_source = create_image_source(uri_set.source_uri,
                                               uri_set.workspace_source_uri,
                                               uri_set.image_folder,
                                               uri_set.order, tile_dim)
            acc += [image_source]
            return image_source

        def uri_set_copy(uri_set):
            copy_to_workspace(uri_set.source_uri, uri_set.workspace_target)
            return uri_set

        uri_set_rdd = sc.parallelize(uri_sets, image_count).map(uri_set_copy)
        image_sources = uri_set_rdd.map(
            lambda uri_set: create_image_sources(uri_set,
                                                 image_source_accumulator))
        chunk_tasks = image_sources.flatMap(
            lambda image_source: generate_chunk_tasks(image_source, tile_dim))
        chunks_count = chunk_tasks.cache().count()
        num_partitions = max(chunks_count / 10, min(50, image_count))

        chunk_tasks.repartition(num_partitions).foreach(process_chunk_task)

        image_sources = image_source_accumulator.value
        print "Processed %d images into %d chunks" % (len(image_sources),
                                                      chunks_count)

        input_info = map(construct_image_info, sorted(image_sources,
                                                      key=lambda im: im.order))

        result = {
            "jobId": job_id,
            "target": target,
            "tileSize": tile_dim,
            "input": input_info
        }

        # Save off result
        path_parsed = urlparse(output_file)
        if not path_parsed.scheme:
            # Save to local files system
            open(output_file, 'w').write(json.dumps(result))
        elif path_parsed.scheme == 's3':
            client = boto3.client('s3')
            bucket = path_parsed.netloc
            key = path_parsed.path
            client.put_object(Bucket=bucket, Key=key, Body=json.dumps(result))
    except Exception, e:
        message = "%s: %s" % (type(e).__name__, e.message)
        notify_failure(status_queue, job_id, message)
        raise

    notify_success(status_queue, job_id)

    print "Done."


def main():
    """
    Raster Foundry Tiler

    Usage:
      chunk.py --job-id=<id> --workspace=<path>  --target=<path>
               --output=<file> [--status-queue=<sqs>] <image>...
      chunk.py -h | --help
      chunk.py --version

    Arguments:
      <image>  GeoTIFF path.

    Options:
      --job-id=<id>         Unique identifier (for status messages).
      --workspace=<path>    Path where working copy of images will be stored.
      --target=<path>       Path where tiles will be generated.
      --status-queue=<sqs>  SQS endpoint where status messages will be posted.
      -h --help             Show this screen.
      --version             Show version.
    """
    from docopt import docopt
    args = docopt(main.__doc__)

    if args['--version']:
        print('0.1')
        exit()

    tile_dim = TILE_DIM
    run_spark_job(tile_dim, args)


if __name__ == "__main__":
    main()
