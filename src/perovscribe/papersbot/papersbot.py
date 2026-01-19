#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# PapersBot
#
# purpose:  read journal RSS feeds and tweet selected entries
# license:  MIT License
# author:   François-Xavier Coudert
# e-mail:   fxcoudert@gmail.com

import os
import time
import feedparser
import pickle
import csv
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional
from importlib.resources import files
from loguru import logger
from tqdm import tqdm
from perovscribe.papersbot.utils import get_doi, save_summaries
from perovscribe.papersbot.match_pdf import check_pdfs, check_matches, download_pdfs
from perovscribe.papersbot.proc_abstracts import (
    get_abstracts,
    update_for_retry,
    check_relaxed_match_doi,
)
from perovscribe.configuration import papersbot_runs_path, RELAXED_REGEX, STRICT_REGEX

REGEXES = [STRICT_REGEX, RELAXED_REGEX]


def entry_matches(entry, regex):
    # Malformed entry
    if "title" not in entry:
        return False, 0
    title_match = False
    summary_match = False
    if regex.search(entry.title):
        title_match = True
    if "summary" in entry:
        if regex.search(entry.summary):
            summary_match = True
        elif not title_match:
            return False, 2
        else:
            return True, 3
        if summary_match:
            if title_match:
                return True, 5
            else:
                return True, 4
    else:
        # If no summary, we consider it a malformed entry
        if title_match:
            return True, 3
        return False, 1


# Read our list of feeds from file
def read_feeds_list():
    with open(files("perovscribe").joinpath("papersbot/feeds.txt"), "r") as f:
        feeds = [s.partition("#")[0].strip() for s in f]
        return [s for s in feeds if s]


# Read list of feed items already posted
def read_posted():
    try:
        with open(f"{papersbot_runs_path}/posted.dat", "r") as f:
            return f.read().splitlines()
    except OSError:
        return []


@dataclass
class PapersbotResult:
    """Result from papersbot execution."""

    success: bool
    papers_found: int = 0
    pdfs_downloaded: int = 0
    downloaded_files: List[Path] = None
    error: Optional[str] = None

    def __post_init__(self):
        if self.downloaded_files is None:
            self.downloaded_files = []


class PapersBot:
    posted = []
    n_seen = 0
    seen_before = 0
    total = 0
    total_matched = 0

    def __init__(self):
        self.feeds = read_feeds_list()
        self.posted = read_posted()

        # Start-up banner
        logger.info(
            f"This is PapersBot running at {time.strftime('%Y-%m-%d %H:%M:%S %Z')}"
        )
        logger.info(f"Feed list has {len(self.feeds)} feeds\n")

    # Add to list of seen items
    def add_to_posted(self, url):
        with open(f"{papersbot_runs_path}/posted.dat", "a+") as f:
            print(url, file=f)
        self.posted.append(url)

    def save_entry_stats(self, entry_stats):
        with open(f"{papersbot_runs_path}/entry_stats.csv", "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=entry_stats.keys())
            writer.writerow(entry_stats)

    def print_stats(self, update_current=True):
        file_name = "current_stats.txt" if update_current else "stats.txt"
        setting = "w" if update_current else "a"
        with open(f"{papersbot_runs_path}/{file_name}", setting) as f:
            if not update_current:
                end_time = time.strftime("%Y-%m-%d %H:%M:%S %Z")
                f.write(f"Run: {end_time}\n")
            f.write(f"Number of relevant papers: {self.n_seen}\n")
            f.write(f"Number of papers matched: {self.total_matched}\n")
            f.write(f"Number of papers seen before: {self.seen_before}\n")
            f.write(f"Total number of papers processed: {self.total}\n")
            f.write("\n")

    # Main function, iterating over feeds and posting new items
    def run(self):
        for feed in tqdm(self.feeds):
            try:
                parsed_feed = feedparser.parse(feed)
            except ConnectionResetError as e:
                # Print information about which feed is failing, and what is the error
                logger.error(f"Failure to load feed at URL {feed}")
                logger.error(f"Exception info: {str(e)}")

            for entry in parsed_feed.entries:
                any_match = 0
                self.total += 1
                if entry.id in self.posted:
                    self.seen_before += 1
                    continue
                self.n_seen += 1
                entry_stats = {
                    "id": entry.get("id", ""),
                    "title": entry.get("title", ""),
                    "parsed_time": time.time(),
                    "doi": get_doi(entry),
                }
                for r in range(len(REGEXES)):
                    match, status = entry_matches(entry, REGEXES[r])
                    any_match |= match
                    entry_stats["relaxed_regex" if r != 0 else "strict_regex"] = status

                self.total_matched += any_match
                entry_stats["match"] = any_match
                entry_stats["processed"] = False if any_match else True
                if any_match and (
                    entry_stats["doi"] and "error" not in entry_stats["doi"]
                ):
                    save_summaries(
                        {
                            entry_stats["id"]: {
                                "title": entry_stats["title"],
                                "rss_feed_summary": entry.get("summary", ""),
                                "doi": entry_stats["doi"],
                            }
                        },
                        current=False,
                    )
                self.save_entry_stats(entry_stats)
                self.add_to_posted(entry.id)
                self.print_stats()


def run_papersbot(download_dir: str = "downloaded_papers"):
    """Run the complete papersbot workflow.

    Args:
        download_dir: Directory to download PDFs to

    Returns:
        PapersbotResult with execution results
    """

    try:
        if not os.path.isfile(f"{papersbot_runs_path}/summaries.pkl"):
            with open(f"{papersbot_runs_path}/summaries.pkl", "wb") as f:
                pickle.dump({}, f)

        bot = PapersBot()

        # Run the bot to process feeds
        bot.run()
        bot.print_stats(update_current=False)

        # Process the matched papers
        check_relaxed_match_doi()  # Checks if DOI is correct
        update_for_retry()  # Update entries for retrying processing
        get_abstracts()  # Get abstracts for matched entries
        check_matches()  # Check matches after getting abstracts
        pdf_urls_found = check_pdfs()  # Check for Open access PDF URLs

        with open(f"{papersbot_runs_path}/stats.txt", "a+") as f:
            print(
                "************************************************************\n************************************************************\n",
                file=f,
            )

        # Download PDFs and get results
        downloaded_files = download_pdfs(download_dir=download_dir)
        pdfs_downloaded = len([f for f in downloaded_files if f.exists()])

        return PapersbotResult(
            success=True,
            papers_found=pdf_urls_found,
            pdfs_downloaded=pdfs_downloaded,
            downloaded_files=downloaded_files,
        )
    except Exception as e:
        return PapersbotResult(
            success=False, error=f"Papersbot workflow failed: {str(e)}"
        )


if __name__ == "__main__":
    run_papersbot()
