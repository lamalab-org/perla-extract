from perovscribe.papersbot.utils import get_pdf_url, download_pdf, download_pdf_wiley
import pickle
from perovscribe.configuration import papersbot_runs_path, STRICT_REGEX
import pandas as pd
import time
import json
import os
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
        pdf_url = get_pdf_url(sample["doi"])
        post_proc_df.at[i, "pdf_checked"] = True
        if pdf_url is not None:
            if "Error fetching data from Unpaywall:" not in pdf_url:
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
    

def download_pdfs():
    """
    Download PDFs from the found URLs.
    """
    os.makedirs("downloads", exist_ok=True)
    with open(f"{papersbot_runs_path}/found_pdf_urls.json", "r") as f:
        found_urls = json.load(f)
    for i, item in enumerate(found_urls):
        if 'processed' in item and item['processed']:
            continue
        doi = item["doi"]
        doi = doi.lower().strip()
        pdf_url = item["pdf_url"]
        # Define a filepath for the downloaded PDF
        filepath = f"downloaded_papers/{doi.replace('/', '-')}.pdf"
        logger.info(f"Downloading PDF for {doi}: {filepath}")
        if os.path.isfile(filepath):
            logger.warning(f"File {filepath} already exists. Skipping download.")
            continue
        if "wiley" in pdf_url:
            download_pdf_wiley(doi)
        else:
            download_pdf(pdf_url, filepath)
        found_urls[i]['processed'] = True
        found_urls[i]['downloaded'] = os.path.isfile(filepath)
    with open(f"{papersbot_runs_path}/found_pdf_urls.json", "w") as f:
        json.dump(found_urls, f, indent=4)

if __name__ == "__main__":
    check_matches()
    check_pdfs()
