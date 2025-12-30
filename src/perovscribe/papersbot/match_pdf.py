from perovscribe.papersbot.utils import get_pdf_url, download_pdf, download_pdf_wiley
import pickle
from perovscribe.configuration import papersbot_runs_path, STRICT_REGEX
import pandas as pd
import time
import json
import os
from pathlib import Path
from typing import List
from loguru import logger

def check_matches():
    """
    Check for matches using strict regex on abstracts and titles
    """

    def printStats(stats: dict):
        end_time = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        with open(f"{papersbot_runs_path}/stats.txt", "a") as f:
            f.write(f"Strict REGEX against abstract Run: {end_time}\n")
            f.write(f"Number of papers matched: {stats['matches']}\n")
            f.write(f"Number of papers with errors: {stats['error']}\n")
            f.write(f"Total number of papers processed: {stats['total']}\n\n\n")

    post_proc_df = pd.read_csv(f"{papersbot_runs_path}/post_proc.csv")
    summaries = pickle.load(open(f"{papersbot_runs_path}/summaries.pkl", "rb"))
    stats = {"matches": 0, "total": 0, "error": 0}
    for i, sample in post_proc_df.iterrows():
        if sample["match_checked"] or sample["id"] not in summaries:
            continue
        match = 0
        error = None
        s = summaries[sample["id"]]
        reference_text = [s.get("title"), s.get("rss_feed_summary")]
        try:
            for k in ["crossref", "openalex", "semantic_scholar"]:
                if s[k] != {} and "error" not in s[k]:
                    reference_text.extend([s[k]["title"], s[k]["abstract"]])
            for text in reference_text:
                if STRICT_REGEX.search(text):
                    stats["matches"] += 1
                    match = 1
                    break
        except Exception as e:
            error = e
            stats["error"] += 1
        stats["total"] += 1
        post_proc_df.at[i, "match_checked"] = f"Error:{error}" if error else True
        post_proc_df.at[i, "abstract_match" if match else "pdf_checked"] = True

    post_proc_df.to_csv(f"{papersbot_runs_path}/post_proc.csv", index=False)
    printStats(stats)


def check_pdfs():
    """
    Check for pdfs using unpaywall API
    """

    def printStats(stats: dict):
        end_time = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        with open(f"{papersbot_runs_path}/stats.txt", "a") as f:
            f.write(f"Checking unpaywall for pdfs Run: {end_time}\n")
            f.write(f"Number of pdfs found: {stats['urls_found']}\n")
            f.write(f"Number of papers with errors: {stats['error']}\n")
            f.write(f"Number of papers with no Pdfs: {stats['none']}\n")
            f.write(f"Total number of papers processed: {stats['total']}\n\n\n")

    post_proc_df = pd.read_csv(f"{papersbot_runs_path}/post_proc.csv")
    stats = {"urls_found": 0, "error": 0, "none": 0, "total": 0}
    found_urls = []
    for i, sample in post_proc_df.iterrows():
        if sample["pdf_checked"] or not sample["abstract_match"]:
            continue
        error, pdf_url = get_pdf_url(sample["doi"])
        post_proc_df.at[i, "pdf_checked"] = True
        if pdf_url is not None:
            if not error:
                post_proc_df.at[i, "pdf_available"] = True
                stats["urls_found"] += 1
                found_urls.append({"doi": sample["doi"], "pdf_url": pdf_url})
            else:
                stats["error"] += 1
        else:
            stats["none"] += 1
        post_proc_df.at[i, "pdf_url"] = pdf_url if pdf_url is not None else ""
        stats["total"] += 1
    post_proc_df.to_csv(f"{papersbot_runs_path}/post_proc.csv", index=False)
    printStats(stats)
    old_found_urls = json.load(open(f"{papersbot_runs_path}/found_pdf_urls.json", "r")) if os.path.isfile(f"{papersbot_runs_path}/found_pdf_urls.json") else []
    old_found_urls.extend(found_urls)
    with open(f"{papersbot_runs_path}/found_pdf_urls.json", "w") as f:
        json.dump(old_found_urls, f, indent=4)
    

def download_pdfs(download_dir: str = "downloaded_papers") -> List[Path]:
    """
    Download PDFs from the found URLs.
    
    Args:
        download_dir: Directory to download PDFs to
        
    Returns:
        List of Path objects for downloaded files
    """
    download_path = Path(download_dir)
    download_path.mkdir(parents=True, exist_ok=True)
    
    downloaded_files = []
    
    try:
        with open(f"{papersbot_runs_path}/found_pdf_urls.json", "r") as f:
            found_urls = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Error reading found_pdf_urls.json: {e}")
        return downloaded_files
    
    for i, item in enumerate(found_urls):
        if 'processed' in item and item['processed']:
            continue
        doi = item["doi"]
        doi = doi.lower().strip()
        pdf_url = item["pdf_url"]
        # Use consistent '--' replacement for DOI
        filepath = download_path / f"{doi.replace('/', '--')}.pdf"
        logger.info(f"Downloading PDF for {doi}: {filepath}")
        
        if filepath.exists():
            logger.warning(f"File {filepath} already exists. Skipping download.")
            downloaded_files.append(filepath)
        else:
            try:
                download_pdf(pdf_url, str(filepath))
                if filepath.exists():
                    downloaded_files.append(filepath)
            except Exception as e:
                logger.error(f"Error downloading {doi}: {e}")
        
        found_urls[i]['processed'] = True
        found_urls[i]['downloaded'] = filepath.exists()
    
    try:
        with open(f"{papersbot_runs_path}/found_pdf_urls.json", "w") as f:
            json.dump(found_urls, f, indent=4)
    except Exception as e:
        logger.error(f"Error writing found_pdf_urls.json: {e}")
    
    return downloaded_files

if __name__ == "__main__":
    check_matches()
    check_pdfs()
