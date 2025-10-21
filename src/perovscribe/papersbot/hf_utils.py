import os
from huggingface_hub import snapshot_download
from huggingface_hub import HfApi

token = os.environ["HF_TOKEN"]
repo_id = os.environ["HF_REPO_ID"]
api = HfApi(token=token)


def download_files():
    snapshot_path = snapshot_download(
        repo_id=repo_id,
        token=token,
        local_dir="runs",
        repo_type="dataset",
    )
    return snapshot_path


def upload_files():
    api.upload_folder(
        folder_path="runs",
        repo_id=repo_id,
        repo_type="dataset",
    )
