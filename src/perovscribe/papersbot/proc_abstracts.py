import pandas as pd
from perovscribe.papersbot.utils import get_doi_summary
from perovscribe.configuration import papersbot_runs_path
import pickle
import time
from collections import defaultdict
from loguru import logger


def save_summaries(summaries, current=True):
    if current:
        with open(f"{papersbot_runs_path}/curr_summaries.pkl", "wb") as f:
            pickle.dump(summaries, f)
    else:
        old_summaries = pickle.load(open(f"{papersbot_runs_path}/summaries.pkl", "rb"))
        old_summaries.update(summaries)
        with open(f"{papersbot_runs_path}/summaries.pkl", "wb") as f:
            pickle.dump(old_summaries, f)

retry_dates = [30, 90, 180, 360]  # days
def should_reprocess(sample: dict) -> bool:
    """
    Determine if a sample should be reprocessed based on its fields.
    """

    if sample['pdf_available'] \
        or not sample["match"] \
        or sample['retry_count'] >= len(retry_dates) \
        or (sample['abstract_found'] and (not sample['doi_good_to_go'] or not sample['abstract_match'])):
        return False
    
    parsed_time = sample['parsed_time']
    current_time = time.time()
    days_since_parsed = (current_time - parsed_time) / (24 * 3600)
    if days_since_parsed < retry_dates[sample['retry_count']]:
        return False
    return True

def check_relaxed_match_doi():
    def printStats(stats):
        end_time = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        with open(f"{papersbot_runs_path}/stats.txt", "a") as f:
            f.write(f"Relaxed REGEX DOI check Run: {end_time}\n")
            f.write(f"Number of missing DOIs: {stats['missing_doi']}\n")
            f.write(f"Number of papers with DOI: {stats['total'] - stats['missing_doi']}\n")
            f.write(f"Total number of papers processed: {stats['total']}\n\n\n")
    df = pd.read_csv(f"{papersbot_runs_path}/entry_stats.csv")
    df = df.replace({float("nan"): None})
    df_new = []
    stats = defaultdict(int)
    for i, sample in df.iterrows():
        if not sample["match"] or sample["processed"]:
            continue
        doi = sample["doi"]
        if not doi or "error" in "doi":
            df.at[i, "processed"] = True
            stats["missing_doi"] += 1
            stats["total"] += 1
            continue
        
        df.at[i, "processed"] = True

        stats["total"] += 1
        sample["abstract_found"] = False
        sample["match_checked"] = False
        sample["abstract_match"] = False
        sample["doi_good_to_go"] = False
        sample["pdf_checked"] = False
        sample["pdf_available"] = False
        sample["pdf_url"] = ""
        sample["main_index"] = i
        sample["processed"] = False
        sample["retry_count"] = 0
        df_new.append(sample)
    df.to_csv(f"{papersbot_runs_path}/entry_stats.csv", index=False)
    df_new = pd.DataFrame(df_new)
    df_new.to_csv(f"{papersbot_runs_path}/post_proc.csv", mode="a+", index=False, header=False)
    printStats(stats)

def update_for_retry():
    stats = defaultdict(int)
    df = pd.read_csv(f"{papersbot_runs_path}/post_proc.csv")
    df = df.replace({float("nan"): None})
    for i, sample in df.iterrows():
        should_process = should_reprocess(sample)
        if should_process:
            df.at[i, "processed"] = False
            df.at[i, "match_checked"] = False
            df.at[i, "pdf_checked"] = False
            df.at[i, "retry_count"] += 1
            stats["to_retry"] += 1
    df.to_csv(f"{papersbot_runs_path}/post_proc.csv", index=False)
    logger.info(f"{stats['to_retry']} papers will be reprocessed.")
    return stats['to_retry']


def get_abstracts():
    def printStats(stats):
        end_time = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        with open(f"{papersbot_runs_path}/stats.txt", "a") as f:
            f.write(f"Getting abstracts Run: {end_time}\n")
            f.write(f"Number of abstracts found: {stats['abstract_found']}\n")
            f.write(f"Total number of papers processed: {stats['total']}\n\n\n")

    df = pd.read_csv(f"{papersbot_runs_path}/post_proc.csv").replace({float("nan"): None})
    summaries = {}
    stats = defaultdict(int)
    for i, sample in df.iterrows():
        if not sample["match"] or sample["processed"]:
            continue
        doi = sample["doi"]
        
        s = get_doi_summary(doi)

        abstract_found = bool(s.get("consolidated", {}).get("abstract"))
        s["rss_feed_summary"] = sample["summary"]
        s["title"] = sample["title"]
        s["doi"] = sample["doi"]
        summaries[sample["id"]] = s

        df.at[i, "processed"] = True
        df.at[i, "abstract_found"] = abstract_found
        save_summaries(summaries)
        stats["total"] += 1
        stats["abstract_found"] += int(abstract_found)
    save_summaries(summaries, current=False)
    df.to_csv(f"{papersbot_runs_path}/post_proc.csv", index=False)
    printStats(stats)
    return stats['abstract_found']
