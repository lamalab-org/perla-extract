import os
import requests
from loguru import logger
import io
import zipfile
import shutil
from perla_extract.configuration import papersbot_runs_path
from perla_extract.export import get_authentication_token

NOMAD_USERNAME = os.environ.get("NOMAD_USERNAME")
NOMAD_PASSWORD = os.environ.get("NOMAD_PASSWORD")
NOMAD_URL = os.environ.get("NOMAD_URL", "https://nomad-lab.eu/prod/v1/")
NOMAD_STATS_ID = os.environ.get("NOMAD_STATS_ID", "TZL3dKwGT8O5Rjr5_13g4g")


def download_archive(upload_id: str = NOMAD_STATS_ID, dest_path=papersbot_runs_path):
    token = get_authentication_token(NOMAD_URL, NOMAD_USERNAME, NOMAD_PASSWORD)
    if not token:
        raise RuntimeError("Failed to authenticate with NOMAD")
    res = requests.get(
        f"{NOMAD_URL}uploads/{upload_id}/raw/runs?offset=0&length=-1&decompress=false&ignore_mime_type=false&compress=true",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/octet-stream",
        },
        timeout=120,
    )
    res.raise_for_status()
    zip_in_memory = io.BytesIO(res.content)
    with zipfile.ZipFile(zip_in_memory) as zip_ref:
        zip_ref.extractall(dest_path)
    logger.info(f"Downloaded and extracted archive to {dest_path}")


def upload_archive(upload_id: str = NOMAD_STATS_ID, source_path=papersbot_runs_path):
    token = get_authentication_token(NOMAD_URL, NOMAD_USERNAME, NOMAD_PASSWORD)
    zip_path = f"{source_path.parent}/temp_runs"
    if not token:
        raise RuntimeError("Failed to authenticate with NOMAD")
    try:
        shutil.make_archive(zip_path, "zip", source_path)
        with open(f"{zip_path}.zip", "rb") as zip_buffer:
            zip_buffer.seek(0)
            res = requests.put(
                f"{NOMAD_URL}uploads/{upload_id}/raw/runs/",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                files={"file": ("runs.zip", zip_buffer, "application/zip")},
                timeout=120,
            )
        res.raise_for_status()
    finally:
        if os.path.exists(f"{zip_path}.zip"):
            os.remove(f"{zip_path}.zip")
    logger.info(f"Uploaded archive from {source_path} to NOMAD upload ID {upload_id}")


def clear_local_runs(path=papersbot_runs_path):
    if os.path.exists(path.parent):
        shutil.rmtree(path.parent)


class CLI:
    def __init__(self):
        self.download = download_archive
        self.upload = upload_archive
        self.clear = clear_local_runs


def main_cli():
    import fire

    fire.Fire(CLI)


if __name__ == "__main__":
    main_cli()
