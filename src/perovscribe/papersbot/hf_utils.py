import os
import sys
from huggingface_hub import snapshot_download
from huggingface_hub import HfApi
from perovscribe.configuration import papersbot_runs_path

token = os.environ["HF_TOKEN"]
repo_id = os.environ["HF_REPO_ID"]
revision = os.environ.get("REVISION", "main")
api = HfApi(token=token)


def download_files():
    snapshot_path = snapshot_download(
        repo_id=repo_id,
        token=token,
        local_dir=papersbot_runs_path,
        repo_type="dataset",
        revision=revision,
    )
    return snapshot_path


def upload_files():
    api.upload_folder(
        folder_path=papersbot_runs_path,
        repo_id=repo_id,
        repo_type="dataset",
        revision=revision,
    )


if __name__ == "__main__":
    args = sys.argv
    globals()[args[1]]()