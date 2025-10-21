import pandas as pd
from utils import (
    get_doi_summary_crossref,
    get_doi_summary_openalex,
    get_doi_summary_semantic_scholar,
)
import pickle
import time
from papersbot_new import base_path
from collections import defaultdict


def save_summaries(summaries, current=True):
    if current:
        with open(f"{base_path}/curr_summaries.pkl", "wb") as f:
            pickle.dump(summaries, f)
    else:
        old_summaries = pickle.load(open(f"{base_path}/summaries.pkl", "rb"))
        old_summaries.update(summaries)
        with open(f"{base_path}/summaries.pkl", "wb") as f:
            pickle.dump(old_summaries, f)


def get_abstracts():
    def printStats(stats):
        end_time = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        with open(f"{base_path}/stats.txt", "a") as f:
            f.write(f"Getting abstracts Run: {end_time}\n")
            f.write(f"Number of abstracts found: {stats['abstract_found']}\n")
            f.write(f"Number of papers with no dois: {stats['missing_doi']}\n")
            f.write(
                f"Number of papers with metadata: {stats['total'] - stats['missing_doi']}\n"
            )
            f.write(f"Total number of papers processed: {stats['total']}\n\n\n")

    df = pd.read_csv(f"{base_path}/entry_stats.csv")
    df = df.replace({float("nan"): None})
    summaries = {}
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
        s = {"crossref": {}, "openalex": {}, "semantic_scholar": {}}
        summary = get_doi_summary_crossref(doi)
        s["crossref"] = summary
        if "error" in summary or summary["abstract"] == "":
            oa_summary = get_doi_summary_openalex(doi)
            s["openalex"] = oa_summary
            if "error" in oa_summary or oa_summary["abstract"] == "":
                ss_summary = get_doi_summary_semantic_scholar(doi)
                s["semantic_scholar"] = ss_summary

        stats["abstract_found"] += int(
            len("".join([s[k].get("abstract", "") for k in s])) > 0
        )
        s["rss_feed_summary"] = sample["summary"]
        s["title"] = sample["title"]
        s["doi"] = sample["doi"]
        summaries[sample["id"]] = s

        df.at[i, "processed"] = True
        sample["match_checked"] = False
        sample["abstract_match"] = False
        sample["pdf_checked"] = False
        sample["pdf_available"] = False
        sample["pdf_url"] = ""
        sample["main_index"] = i
        sample["processed"] = True
        df_new.append(sample)
        save_summaries(summaries)
        stats["total"] += 1
    save_summaries(summaries, current=False)
    df.to_csv(f"{base_path}/entry_stats.csv", index=False)
    df_new = pd.DataFrame(df_new)
    df_new.to_csv(f"{base_path}/post_proc.csv", mode="a+", index=False, header=False)
    printStats(stats)
