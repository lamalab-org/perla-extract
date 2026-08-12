import os

from huggingface_hub import HfApi, snapshot_download

from perla_extract.configuration import papersbot_runs_path

repo_id = os.environ["HF_REPO_ID"]
revision = os.environ.get("REVISION", "main")
api = HfApi()


def download_files():
    snapshot_path = snapshot_download(
        repo_id=repo_id,
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


class CLI:
    def __init__(self):
        self.download = download_files
        self.upload = upload_files


def main_cli():
    import fire

    fire.Fire(CLI)


if __name__ == "__main__":
    main_cli()
