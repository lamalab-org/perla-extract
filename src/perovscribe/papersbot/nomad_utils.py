import os
import requests
import sys
from loguru import logger
import io
import zipfile
import shutil
from perovscribe.configuration import papersbot_runs_path
from perovscribe.export import get_authentication_token

NOMAD_USERNAME = os.environ.get("NOMAD_USERNAME")
NOMAD_PASSWORD = os.environ.get("NOMAD_PASSWORD")
NOMAD_URL = os.environ.get("NOMAD_URL", "https://nomad-lab.eu/prod/v1/")
NOMAD_STATS_ID = os.environ.get("NOMAD_STATS_ID", "TZL3dKwGT8O5Rjr5_13g4g")


def download_archive(
    upload_id: str = NOMAD_STATS_ID, dest_path: str = papersbot_runs_path
):
    token = get_authentication_token(NOMAD_URL, NOMAD_USERNAME, NOMAD_PASSWORD)
    res = requests.get(
        f"{NOMAD_URL}uploads/{upload_id}/raw/runs?offset=0&length=-1&decompress=false&ignore_mime_type=false&compress=true",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/octet-stream",
        },
    )
    res.raise_for_status()
    zip_in_memory = io.BytesIO(res.content)
    with zipfile.ZipFile(zip_in_memory) as zip_ref:
        zip_ref.extractall(dest_path)
    logger.info(f"Downloaded and extracted archive to {dest_path}")


def upload_archive(
    upload_id: str = NOMAD_STATS_ID, source_path: str = papersbot_runs_path
):
    token = get_authentication_token(NOMAD_URL, NOMAD_USERNAME, NOMAD_PASSWORD)
    shutil.make_archive("runs", "zip", source_path)
    with open("runs.zip", "rb") as zip_buffer:
        zip_buffer.seek(0)
        res = requests.put(
            f"{NOMAD_URL}uploads/{upload_id}/raw/runs/",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            files={"file": ("runs.zip", zip_buffer, "application/zip")},
            timeout=60,
        )
        os.remove("runs.zip")
    res.raise_for_status()
    logger.info(f"Uploaded archive from {source_path} to NOMAD upload ID {upload_id}")


if __name__ == "__main__":
    args = sys.argv
    globals()[args[1]]()
