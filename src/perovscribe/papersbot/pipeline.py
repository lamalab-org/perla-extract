from papersbot_new import run_papersbot, base_path
from proc_abstracts import get_abstracts
from match_pdf import check_pdfs, check_matches, download_pdfs
from hf_utils import download_files


def main():
    download_files()
    run_papersbot()
    get_abstracts()
    check_matches()
    check_pdfs()
    with open(f"{base_path}/stats.txt", "a+") as f:
        print(
            "************************************************************\n************************************************************\n",
            file=f,
        )
    download_pdfs()



if __name__ == "__main__":
    main()
