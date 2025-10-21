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
import bs4
import feedparser
import pickle
import csv
from utils import get_doi

base_path = "runs"
os.makedirs(base_path, exist_ok=True)

RELAXED_REGEX_1 = re.compile(
    r"""
    (?=.*\b(?:perovskite[s]?|PSC[s]?)\b)  # Must contain perovskite or PSC
    (?=.*\b(?:
        # Solar/photovoltaic terms
        solar[\s-]?cell[s]?|photovoltaic[s]?|PV|
        # Performance metrics
        efficiency|PCE|power[\s-]conversion[\s-]efficiency|
        V(?:OC|oc)|open[\s-]circuit[\s-]voltage|
        J(?:SC|sc)|short[\s-]circuit[\s-]current|
        fill[\s-]factor|FF|
        # Device terms
        device[s]?|cell[s]?|
        # Performance indicators
        [\d]+(?:\.\d+)?%|[\d]+(?:\.\d+)?[\s]*mA[\/\s]*cm[2²]|[\d]+(?:\.\d+)?[\s]*V|
        mW[\s]*cm|W[\s]*g|
        # Stability terms
        stability|lifetime|degradation|aging|operational|
        # Characterization terms
        hysteresis|quantum[\s-]efficiency|EQE|IPCE|
        # Processing terms
        fabrication|processing|preparation|synthesis|
        # Material terms that indicate solar cell context
        hole[\s-]transport|electron[\s-]transport|HTL|ETL|
        absorber|active[\s-]layer|photoactive|
        # Electrical terms
        conductivity|carrier|charge|recombination|extraction|
        # Environmental terms in solar context
        moisture|thermal|illumination|light|AM1\.5|sun|
        # Application terms
        commercialization|applications?|energy[\s-]conversion
    )\b)
    .*?
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

RELAXED_REGEX_2 = re.compile(
    r"""
    \b(?:perovskite[s]?|PSC[s]?)\b
    .*?
    (?:
        # Solar/PV terms
        \b(?:solar|photovoltaic|PV|energy[\s-]conversion)\b|
        # Performance metrics with numbers
        (?:\d+(?:\.\d+)?[\s]*%)|
        (?:\d+(?:\.\d+)?[\s]*(?:mA[\/\s]*cm|V|mW|W[\s]*g))|
        # Key device terms
        \b(?:efficiency|PCE|device[s]?|cell[s]?|stability|performance)\b|
        # Transport/electrical terms
        \b(?:transport|conductivity|carrier|charge|recombination|extraction|HTL|ETL)\b|
        # Environmental/operational terms
        \b(?:illumination|light|thermal|moisture|operational|aging|degradation)\b
    )
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

RELAXED_REGEX_3 = re.compile(
    r"""
    (?=.*\b(?:perovskite[s]?|halide|PSC[s]?|CsPb|MAPb|FAPb|CH3NH3|formamidinium)\b)
    (?=.*\b(?:
        solar|photovoltaic[s]?|PV|cell[s]?|device[s]?|
        efficiency|PCE|performance|conversion|
        [\d]+\.?\d*\s*%|
        energy|power|voltage|current|
        stability|degradation|
        fabrication|synthesis|processing
    )\b)
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

# This is the regular expression that selects the papers of interest
STRICT_REGEX = re.compile(
    r"""
  (
    # Perovskite cell variations
    \b(perovskite(?:[\s-](?:solar|photovoltaic|PV))?(?:\s*cell[s]?|\s*device[s]?)|PSC[s]?)\b
    # Single junction specific terms
    |single[\s-]?(?:junction|layer|absorber|heterojunction|stack)
    # Architecture variations for single junction
    |(?:planar|mesoscopic|inverted|flexible|rigid|printable)[\s-]?perovskite
    # Exclude explicit mentions of tandem/multi-junction
    (?<!tandem[\s-])(?<!multi[\s-])(?<!double[\s-])(?<!triple[\s-])
  )
  .*?
  (
    # Performance metrics
    \b(?:efficiency|PCE|power[\s-]conversion[\s-]efficiency
    |V(?:OC|oc)|open[\s-]circuit[\s-]voltage
    |J(?:SC|sc)|short[\s-]circuit[\s-]current(?:[\s-]density)?
    |fill[\s-]factor|FF
    |stability|lifetime|degradation|performance
    |I[- ]?V(?:[\s-]curve)?|J[- ]?V(?:[\s-]curve)?|current[\s-](?:voltage|density)
    |hysteresis|quantum[\s-]efficiency|(?:internal|external)[\s-]quantum[\s-]efficiency|(?:IPCE|EQE)
    |[\d]+(?:\.\d+)?%|[\d]+(?:\.\d+)?[\s]?mA\/cm2|[\d]+(?:\.\d+)?[\s]?V)\b
  )
""",
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

REGEXES = [STRICT_REGEX, RELAXED_REGEX_1, RELAXED_REGEX_2, RELAXED_REGEX_3]


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


# Convert string from HTML to plain text
def htmlToText(s):
    return bs4.BeautifulSoup(s, "html.parser").get_text()


# Read our list of feeds from file
def readFeedsList():
    with open("feeds.txt", "r") as f:
        feeds = [s.partition("#")[0].strip() for s in f]
        return [s for s in feeds if s]


# Read list of feed items already posted
def readPosted():
    try:
        with open(f"{base_path}/posted.dat", "r") as f:
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
        with open(f"{base_path}/posted.dat", "a+") as f:
            print(url, file=f)
        self.posted.append(url)

    def saveEntries(self, entry):
        with open(f"{base_path}/entries.pkl", "ab") as f:
            pickle.dump(entry, f)

    def saveEntryStats(self, entry_stats):
        with open(f"{base_path}/entry_stats.csv", "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=entry_stats.keys())
            writer.writerow(entry_stats)

    def printStats(self, update_current=True):
        if update_current:
            with open(f"{base_path}/current_stats.txt", "w") as f:
                f.write(f"Number of relevant papers: {self.n_seen}\n")
                f.write(f"Number of papers matched: {self.total_matched}\n")
                f.write(f"Number of papers seen before: {self.seen_before}\n")
                f.write(f"Total number of papers processed: {self.total}\n")
        else:
            end_time = time.strftime("%Y-%m-%d %H:%M:%S %Z")
            with open(f"{base_path}/stats.txt", "a") as f:
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
                    entry_stats[f"relaxed_regex_{r}" if r != 0 else "strict_regex"] = (
                        status
                    )

                self.total_matched += any_match
                entry_stats["match"] = any_match
                entry_stats["processed"] = False if any_match else True
                self.addToPosted(entry.id)
                self.saveEntries(entry)
                self.saveEntryStats(entry_stats)
                self.printStats()


def run_papersbot():
    bot = PapersBot(False)
    bot.run()
    bot.printStats(update_current=False)


if __name__ == "__main__":
    run_papersbot()
