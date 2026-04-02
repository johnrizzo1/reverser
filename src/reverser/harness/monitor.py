"""S3 bucket monitor for new binary uploads."""

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

from .config import Config
from .state import StateDB

log = logging.getLogger(__name__)


@dataclass
class S3Object:
    key: str
    etag: str
    size: int


class S3Monitor:
    def __init__(self, config: Config, state_db: StateDB):
        self.config = config
        self.state_db = state_db
        self._client = boto3.client("s3", region_name=config.s3_region)
        self._backoff = 5  # initial backoff seconds

    def poll(self) -> list[S3Object]:
        """List new objects in the S3 bucket, filtering out already-processed ones."""
        try:
            objects = self._list_objects()
            self._backoff = 5  # reset on success
        except (ClientError, EndpointConnectionError, NoCredentialsError) as e:
            log.warning("S3 poll failed (retrying in %ds): %s", self._backoff, e)
            time.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, 300)
            return []

        new_objects = []
        for obj in objects:
            if not self.state_db.is_processed(obj.key, obj.etag):
                new_objects.append(obj)

        if new_objects:
            log.info("Found %d new object(s) in s3://%s/%s",
                     len(new_objects), self.config.s3_bucket, self.config.s3_prefix)
        return new_objects

    def _list_objects(self) -> list[S3Object]:
        """Paginate through all objects under the configured prefix."""
        objects = []
        paginator = self._client.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket=self.config.s3_bucket,
            Prefix=self.config.s3_prefix,
        )
        for page in pages:
            for item in page.get("Contents", []):
                # Skip "directory" markers
                if item["Key"].endswith("/"):
                    continue
                # Skip objects under the results prefix
                if self.config.results_s3_prefix and item["Key"].startswith(self.config.results_s3_prefix):
                    continue
                objects.append(S3Object(
                    key=item["Key"],
                    etag=item["ETag"].strip('"'),
                    size=item.get("Size", 0),
                ))
        return objects

    def download(self, s3_key: str, dest_dir: str) -> Path:
        """Download an S3 object to a local staging directory."""
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)

        # Preserve the filename from the key
        filename = Path(s3_key).name
        local_path = dest / filename

        log.info("Downloading s3://%s/%s -> %s", self.config.s3_bucket, s3_key, local_path)
        self._client.download_file(self.config.s3_bucket, s3_key, str(local_path))
        return local_path

    def upload_results(self, local_dir: Path, s3_prefix: str):
        """Upload all files in local_dir to S3 under the given prefix."""
        local_dir = Path(local_dir)
        if not local_dir.exists():
            return

        for file_path in local_dir.rglob("*"):
            if file_path.is_file():
                relative = file_path.relative_to(local_dir)
                s3_key = f"{s3_prefix.rstrip('/')}/{relative}"
                log.info("Uploading %s -> s3://%s/%s", file_path, self.config.s3_bucket, s3_key)
                self._client.upload_file(str(file_path), self.config.s3_bucket, s3_key)
