import asyncio
import json
import os
import pickle
import time
from pathlib import Path

import pandas as pd
from loguru import logger
from tqdm import tqdm

from perla_extract.configuration import (
    STRICT_REGEX,
    papersbot_runs_path,
    playwright_installed,
)
from perla_extract.papersbot.utils import (
    download_pdf,
    get_links,
    get_pdf_url,
    playwright_download_pdf,
    save_summaries,
)


def check_matches():
    """
    Check for matches using strict regex on abstracts and titles
    """
    from perla_extract.pipeline import is_doi_good_to_go

    def print_stats(stats: dict):
        end_time = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        with open(f"{papersbot_runs_path}/stats.txt", "a") as f:
            f.write(f"Strict REGEX against abstract Run: {end_time}\n")
            f.write(f"Number of papers matched: {stats['matches']}\n")
            f.write(f"Number of papers with errors: {stats['error']}\n")
            f.write(f"Total number of papers processed: {stats['total']}\n\n\n")

    post_proc_df = pd.read_csv(f"{papersbot_runs_path}/post_proc.csv")
    with open(f"{papersbot_runs_path}/summaries.pkl", "rb") as f:
        summaries = pickle.load(f)
    stats = {"matches": 0, "is_doi_good": 0, "total": 0, "error": 0}
    r_df = post_proc_df[
        ~post_proc_df["match_checked"] & post_proc_df["id"].isin(summaries.keys())
    ]
    logger.info(f"Checking strict regex matches for {len(r_df)} papers.")
    for i, sample in tqdm(r_df.iterrows(), total=len(r_df)):
        match = False
        doi_good = False
        error = None
        s = summaries[sample["id"]]
        metadata = s["consolidated"]
        metadata["title"] = s.get("title")
        metadata["rss_feed_summary"] = s.get("rss_feed_summary")
        if not metadata["abstract"]:
            metadata["abstract"] = metadata["rss_feed_summary"]
        for text in metadata.values():
            if STRICT_REGEX.search(text):
                stats["matches"] += 1
                match = True
                if is_doi_good_to_go(sample["doi"], "", metadata):
                    stats["is_doi_good"] += 1
                    doi_good = True
                break
        stats["total"] += 1
        post_proc_df.at[i, "match_checked"] = f"Error:{error}" if error else True
        post_proc_df.at[i, "abstract_match"] = match
        post_proc_df.at[i, "doi_good_to_go" if doi_good else "pdf_checked"] = True

    post_proc_df.to_csv(f"{papersbot_runs_path}/post_proc.csv", index=False)
    print_stats(stats)
    return stats["matches"]


def check_pdfs():
    """
    Check for pdfs using unpaywall API
    """

    def print_stats(stats: dict):
        end_time = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        with open(f"{papersbot_runs_path}/stats.txt", "a") as f:
            f.write(f"Checking unpaywall for pdfs Run: {end_time}\n")
            f.write(f"Number of pdfs found: {stats['urls_found']}\n")
            f.write(f"Number of papers with errors: {stats['error']}\n")
            f.write(f"Number of papers with no Pdfs: {stats['none']}\n")
            f.write(f"Total number of papers processed: {stats['total']}\n\n\n")

    post_proc_df = pd.read_csv(f"{papersbot_runs_path}/post_proc.csv").replace(
        {float("nan"): None}
    )
    with open(f"{papersbot_runs_path}/summaries.pkl", "rb") as f:
        summaries = pickle.load(f)
    r_df = post_proc_df[
        ~post_proc_df["pdf_checked"]
        & post_proc_df["doi_good_to_go"]
        & post_proc_df["abstract_match"]
    ]
    stats = {"urls_found": 0, "error": 0, "none": 0, "total": 0}
    found_urls = {}
    logger.info(f"Checking for PDF URLs for {len(r_df)} strict matched papers.")
    for i, sample in tqdm(r_df.iterrows(), total=len(r_df)):
        doi = sample["doi"].strip().lower()
        error, is_oa, pdf_url = get_pdf_url(doi)
        post_proc_df.at[i, "pdf_checked"] = True
        if "msg" not in pdf_url and len(pdf_url) > 0:
            url_type = "pdf_url"
            if "pdf_url" not in pdf_url and pdf_url.get("landing_page_url"):
                if sample["landing_page_url"] == pdf_url["landing_page_url"]:
                    continue
                post_proc_df.at[i, "landing_page_url"] = pdf_url["landing_page_url"]
                filtered_link, links = get_links(pdf_url["landing_page_url"])
                summaries[sample["id"]]["links"] = [filtered_link, links]
                pdf_url["pdf_url"] = filtered_link if filtered_link else ""
                url_type = "filtered_landing_page"
            post_proc_df.at[i, "pdf_available"] = (
                bool(pdf_url.get("pdf_url"))
            )
            stats["urls_found"] += 1
            found_urls[doi] = {
                "doi": doi,
                "tries": 0,
            }
            found_urls[doi].update(pdf_url)
            print(doi, pdf_url)
            msg = pdf_url["pdf_url"]
        else:
            url_type = "none" if not error else "error"
            stats[url_type] += 1
            msg = pdf_url["msg"]
        post_proc_df.at[i, "pdf_url"] = msg
        post_proc_df.at[i, "pdf_url_type"] = url_type
        post_proc_df.at[i, "is_oa"] = is_oa
        stats["total"] += 1
    print_stats(stats)

    if os.path.isfile(f"{papersbot_runs_path}/found_pdf_urls.json"):
        with open(f"{papersbot_runs_path}/found_pdf_urls.json", "r") as f:
            old_found_urls = json.load(f)
    else:
        old_found_urls = {}

    for k, v in found_urls.items():
        if k in old_found_urls:
            old_found_urls[k].update(v)
        else:
            old_found_urls[k] = v
    with open(f"{papersbot_runs_path}/found_pdf_urls.json", "w") as f:
        json.dump(old_found_urls, f, indent=4)
    post_proc_df.to_csv(f"{papersbot_runs_path}/post_proc.csv", index=False)
    try:
        save_summaries(summaries, current=False)
    except Exception as e:
        logger.error(f"Failed to save summaries: {e}")
    return stats["urls_found"]


def download(pdf_url: str, doi: str, filepath: str | Path) -> bool:
    filepath = Path(filepath)
    logger.info(f"Downloading PDF for {doi}: {filepath} from {pdf_url}")
    try:
        download_success = download_pdf(pdf_url, str(filepath))
        if not download_success and playwright_installed:
            download_success = asyncio.run(
                playwright_download_pdf(pdf_url, str(filepath))
            )
        if filepath.exists():
            return True
        else:
            logger.error(f"Failed to download PDF for {doi} from {pdf_url}")
    except Exception as e:
        logger.error(f"Error downloading {doi}: {e}")
    return False


def download_pdfs(download_dir: str | Path = "downloaded_papers") -> list[Path]:
    """
    Download PDFs from the found URLs.

    Args:
        download_dir: Directory to download PDFs to

    Returns:
        List of Path objects for downloaded files
    """
    download_path = Path(download_dir)
    download_path.mkdir(parents=True, exist_ok=True)

    downloaded_files: list[Path] = []

    try:
        with open(f"{papersbot_runs_path}/found_pdf_urls.json", "r") as f:
            found_urls = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Error reading found_pdf_urls.json: {e}")
        return downloaded_files

    for doi, item in found_urls.items():
        if item.get("processed"):
            continue

        filepath = download_path / f"{doi.replace('/', '--')}.pdf"
        if filepath.exists():
            logger.warning(f"File {filepath} already exists. Skipping download.")
            downloaded_files.append(filepath)
            continue
        pdf_url = item.get("pdf_url")
        if not pdf_url and "landing_page_url" in item and playwright_installed:
            landing_page_url = item["landing_page_url"]
            logger.info(
                f"Attempting to download PDF from landing page for {doi}: {landing_page_url}"
            )
            try:
                if landing_page_url.rstrip()[-4:].lower() == ".pdf":
                    download_success = download(landing_page_url, doi, filepath)
                    if download_success and filepath.exists():
                        found_urls[doi]["pdf_url"] = landing_page_url
                        downloaded_files.append(filepath)
                        continue
                pdf_url = get_links(landing_page_url)[0]
                if pdf_url:
                    item["pdf_url"] = pdf_url
                    logger.info(f"Found PDF URL for {doi}: {pdf_url}")
                else:
                    logger.warning(f"No PDF URL found on landing page for {doi}.")
            except Exception as e:
                logger.error(
                    f"Error extracting PDF URL from landing page for {doi}: {e}"
                )

        if pdf_url:
            try:
                download_success = download(pdf_url, doi, filepath)
                if download_success and filepath.exists():
                    downloaded_files.append(filepath)
                else:
                    logger.error(f"Failed to download PDF for {doi} from {pdf_url}")
            except Exception as e:
                logger.error(f"Error downloading {doi}: {e}")

        found_urls[doi]["tries"] += 1
        if item["tries"] >= 3:
            logger.warning(f"Max tries reached for {doi}.")
            found_urls[doi]["processed"] = True

    try:
        with open(f"{papersbot_runs_path}/found_pdf_urls.json", "w") as f:
            json.dump(found_urls, f, indent=4)
    except Exception as e:
        logger.error(f"Error writing found_pdf_urls.json: {e}")

    return downloaded_files
