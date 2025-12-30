#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# PapersBot
#
# purpose:  read journal RSS feeds and tweet selected entries
# license:  MIT License
# author:   François-Xavier Coudert
# e-mail:   fxcoudert@gmail.com
#

import os
import random
import re
import time
import yaml
import feedparser
import pickle
import csv
from importlib.resources import files

from perovscribe.papersbot.utils import get_doi
from perovscribe.papersbot.match_pdf import check_pdfs, check_matches, download_pdfs
from perovscribe.papersbot.proc_abstracts import get_abstracts
from perovscribe.configuration import papersbot_runs_path, RELAXED_REGEX, STRICT_REGEX

REGEXES = [STRICT_REGEX, RELAXED_REGEX]


def entryMatches(entry, regex):
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
def readFeedsList():
    with open(files("perovscribe").joinpath("papersbot/feeds.txt"), "r") as f:
        feeds = [s.partition("#")[0].strip() for s in f]
        return [s for s in feeds if s]


# Read list of feed items already posted
def readPosted():
    try:
        with open(f"{papersbot_runs_path}/posted.dat", "r") as f:
            return f.read().splitlines()
    except OSError:
        return []


class PapersBot:
    posted = []
    n_seen = 0
    seen_before = 0
    total = 0
    total_matched = 0

    def __init__(self, doTweet=True):
        self.feeds = readFeedsList()
        self.posted = readPosted()

        # Read parameters from configuration file
        try:
            with open("config.yml", "r") as f:
                config = yaml.safe_load(f)
        except OSError:
            config = {}
        self.throttle = config.get("throttle", 0)
        self.wait_time = config.get("wait_time", 5)
        self.shuffle_feeds = config.get("shuffle_feeds", True)
        self.blacklist = config.get("blacklist", [])
        self.blacklist = [re.compile(s) for s in self.blacklist]

        # Shuffle feeds list
        if self.shuffle_feeds:
            random.shuffle(self.feeds)

        # Maximum shortened URL length (previously short_url_length_https)
        urllen = 23
        # Maximum URL length for media (previously characters_reserved_per_media)
        imglen = 24
        # Determine maximum tweet length
        self.maxlength = 280 - (urllen + 1) - imglen

        # Start-up banner
        print(f"This is PapersBot running at {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"Feed list has {len(self.feeds)} feeds\n")

    # Add to tweets posted
    def addToPosted(self, url):
        with open(f"{papersbot_runs_path}/posted.dat", "a+") as f:
            print(url, file=f)
        self.posted.append(url)

    def saveEntries(self, entry):
        with open(f"{papersbot_runs_path}/entries.pkl", "ab") as f:
            pickle.dump(entry, f)

    def saveEntryStats(self, entry_stats):
        with open(f"{papersbot_runs_path}/entry_stats.csv", "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=entry_stats.keys())
            writer.writerow(entry_stats)

    def printStats(self, update_current=True):
        if update_current:
            with open(f"{papersbot_runs_path}/current_stats.txt", "w") as f:
                f.write(f"Number of relevant papers: {self.n_seen}\n")
                f.write(f"Number of papers matched: {self.total_matched}\n")
                f.write(f"Number of papers seen before: {self.seen_before}\n")
                f.write(f"Total number of papers processed: {self.total}\n")
        else:
            end_time = time.strftime("%Y-%m-%d %H:%M:%S %Z")
            with open(f"{papersbot_runs_path}/stats.txt", "a") as f:
                f.write(f"Run: {end_time}\n")
                f.write(f"Number of relevant papers: {self.n_seen}\n")
                f.write(f"Number of papers matched: {self.total_matched}\n")
                f.write(f"Number of papers seen before: {self.seen_before}\n")
                f.write(f"Total number of papers processed: {self.total}\n\n\n")
                print("\n\n")

    # Main function, iterating over feeds and posting new items
    def run(self):
        for feed in self.feeds:
            try:
                parsed_feed = feedparser.parse(feed)
            except ConnectionResetError as e:
                # Print information about which feed is failing, and what is the error
                print("Failure to load feed at URL", feed)
                print("Exception info:", str(e))
                # sys.exit(1)

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
                    "summary": entry.get("summary", ""),
                    "parsed_time": time.time(),
                    "doi": get_doi(entry),
                }
                for r in range(len(REGEXES)):
                    match, status = entryMatches(entry, REGEXES[r])
                    any_match |= match
                    entry_stats["relaxed_regex" if r != 0 else "strict_regex"] = (
                        status
                    )

                self.total_matched += any_match
                entry_stats["match"] = any_match
                entry_stats["processed"] = False if any_match else True
                self.addToPosted(entry.id)
                # self.saveEntries(entry)
                self.saveEntryStats(entry_stats)
                self.printStats()


def run_papersbot(download_dir: str = "downloaded_papers"):
    """Run the complete papersbot workflow.
    
    Args:
        download_dir: Directory to download PDFs to
        
    Returns:
        PapersbotResult with execution results
    """
    from perovscribe.pipeline import PapersbotResult
    
    try:
        if not os.path.isfile(f"{papersbot_runs_path}/summaries.pkl"):
            with open(f"{papersbot_runs_path}/summaries.pkl", "wb") as f:
                pickle.dump({}, f)
        
        bot = PapersBot(False)
        bot.run()
        papers_found = bot.total_matched
        bot.printStats(update_current=False)

        get_abstracts()
        check_matches()
        check_pdfs()
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
            papers_found=papers_found,
            pdfs_downloaded=pdfs_downloaded,
            downloaded_files=downloaded_files
        )
    except Exception as e:
        return PapersbotResult(
            success=False,
            error=f"Papersbot workflow failed: {str(e)}"
        )


if __name__ == "__main__":
    run_papersbot()
