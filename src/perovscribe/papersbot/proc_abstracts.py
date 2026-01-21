import pandas as pd
from perovscribe.papersbot.utils import get_doi_summary, save_summaries
from perovscribe.configuration import papersbot_runs_path, retry_dates
import pickle
import time
from collections import defaultdict
from loguru import logger
from tqdm import tqdm


def should_reprocess(sample: dict) -> bool:
    """
    Determine if a sample should be reprocessed based on its fields.
    """

    if (
        sample["pdf_available"]
        or not sample["match"]
        or sample["retry_count"] >= len(retry_dates)
        or (
            sample["abstract_found"]
            and (not sample["doi_good_to_go"] or not sample["abstract_match"])
        )
    ):
        return False

    parsed_time = sample["parsed_time"]
    current_time = time.time()
    days_since_parsed = (current_time - parsed_time) / (24 * 3600)
    if days_since_parsed < retry_dates[sample["retry_count"]]:
        return False
    return True


def check_relaxed_match_doi():
    def print_stats(stats):
        end_time = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        with open(f"{papersbot_runs_path}/stats.txt", "a") as f:
            f.write(f"Relaxed REGEX DOI check Run: {end_time}\n")
            f.write(f"Number of missing DOIs: {stats['missing_doi']}\n")
            f.write(
                f"Number of papers with DOI: {stats['total'] - stats['missing_doi']}\n"
            )
            f.write(f"Total number of papers processed: {stats['total']}\n\n\n")

    df = pd.read_csv(f"{papersbot_runs_path}/entry_stats.csv")
    df = df.replace({float("nan"): None})
    r_df = df[df["match"] & ~df["processed"]]
    df_new = []
    stats = defaultdict(int)
    logger.info(f"Checking DOIs for {len(r_df)} matched papers.")
    for i, sample in tqdm(r_df.iterrows(), total=len(r_df)):
        doi = sample["doi"]
        if not doi or "error" in doi:
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
    df_new.to_csv(
        f"{papersbot_runs_path}/post_proc.csv", mode="a+", index=False, header=False
    )
    print_stats(stats)


def update_for_retry():
    stats = defaultdict(int)
    df = pd.read_csv(f"{papersbot_runs_path}/post_proc.csv")
    df = df.replace({float("nan"): None})
    logger.info("Updating entries for retry processing.")
    for i, sample in tqdm(df.iterrows(), total=len(df)):
        should_process = should_reprocess(sample)
        if should_process:
            df.at[i, "processed"] = False
            df.at[i, "match_checked"] = False
            df.at[i, "pdf_checked"] = False
            df.at[i, "retry_count"] += 1
            stats["to_retry"] += 1
    df.to_csv(f"{papersbot_runs_path}/post_proc.csv", index=False)
    logger.info(f"{stats['to_retry']} papers will be reprocessed.")
    return stats["to_retry"]


def get_abstracts():
    def print_stats(stats):
        end_time = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        with open(f"{papersbot_runs_path}/stats.txt", "a") as f:
            f.write(f"Getting abstracts Run: {end_time}\n")
            f.write(f"Number of abstracts found: {stats['abstract_found']}\n")
            f.write(f"Total number of papers processed: {stats['total']}\n\n\n")

    df = pd.read_csv(f"{papersbot_runs_path}/post_proc.csv").replace(
        {float("nan"): None}
    )
    summaries = pickle.load(open(f"{papersbot_runs_path}/summaries.pkl", "rb"))
    stats = defaultdict(int)
    r_df = df[df["match"] & ~df["processed"]]
    logger.info(f"Getting abstracts for {len(r_df)} papers.")
    for i, sample in tqdm(r_df.iterrows(), total=len(r_df)):
        doi = sample["doi"]

        s = get_doi_summary(doi)

        abstract_found = bool(s.get("consolidated", {}).get("abstract"))
        base_info = summaries.get(sample["id"], {})
        base_info.update(s)
        summaries[sample["id"]] = base_info

        df.at[i, "processed"] = True
        df.at[i, "abstract_found"] = abstract_found
        stats["total"] += 1
        stats["abstract_found"] += int(abstract_found)

    try:
        save_summaries(summaries, current=False)
    except Exception as e:
        logger.error(f"Failed to save summaries: {e}")
    df.to_csv(f"{papersbot_runs_path}/post_proc.csv", index=False)
    print_stats(stats)
    return stats["abstract_found"]
