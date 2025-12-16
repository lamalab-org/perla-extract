import pandas as pd
from perovscribe.papersbot.utils import (
    get_doi_summary_crossref,
    get_doi_summary_openalex,
    get_doi_summary_semantic_scholar,
)
from perovscribe.configuration import papersbot_runs_path
import pickle
import time
from collections import defaultdict


def save_summaries(summaries, current=True):
    if current:
        with open(f"{papersbot_runs_path}/curr_summaries.pkl", "wb") as f:
            pickle.dump(summaries, f)
    else:
        old_summaries = pickle.load(open(f"{papersbot_runs_path}/summaries.pkl", "rb"))
        old_summaries.update(summaries)
        with open(f"{papersbot_runs_path}/summaries.pkl", "wb") as f:
            pickle.dump(old_summaries, f)


def get_abstracts():
    def printStats(stats):
        end_time = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        with open(f"{papersbot_runs_path}/stats.txt", "a") as f:
            f.write(f"Getting abstracts Run: {end_time}\n")
            f.write(f"Number of abstracts found: {stats['abstract_found']}\n")
            f.write(f"Number of papers with no dois: {stats['missing_doi']}\n")
            f.write(
                f"Number of papers with metadata: {stats['total'] - stats['missing_doi']}\n"
            )
            f.write(f"Total number of papers processed: {stats['total']}\n\n\n")

    df = pd.read_csv(f"{papersbot_runs_path}/entry_stats.csv")
    df = df.replace({float("nan"): None})
    summaries = {}
    df_new = []
    stats = defaultdict(int)
    for i, sample in df.iterrows():
        if not sample.get("match") or sample.get("processed"):
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
        get_doi_summary = {
            "crossref": get_doi_summary_crossref,
            "openalex": get_doi_summary_openalex,
            "semantic_scholar": get_doi_summary_semantic_scholar,
        }
        for source in ["crossref", "openalex", "semantic_scholar"]:
            summary = get_doi_summary[source](doi)
            s[source] = summary
            if "error" not in summary and summary["abstract"] != "":
                break
        
        
        sample["abstract_found"] = len("".join([s[k].get("abstract", "") for k in s])) > 0
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
        stats["abstract_found"] += int(sample["abstract_found"])
    save_summaries(summaries, current=False)
    df.to_csv(f"{papersbot_runs_path}/entry_stats.csv", index=False)
    df_new = pd.DataFrame(df_new)
    df_new.to_csv(f"{papersbot_runs_path}/post_proc.csv", mode="a+", index=False, header=False)
    printStats(stats)
