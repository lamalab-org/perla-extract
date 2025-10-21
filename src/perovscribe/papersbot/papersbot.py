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
import sys
import time
import yaml
import requests

import bs4
import feedparser


def download_pdf(url: str, filepath: str):
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()  # Raise error for bad status codes

        if "application/pdf" in response.headers.get("Content-Type", ""):
            with open(filepath, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:  # filter out keep-alive chunks
                        f.write(chunk)
            print(f"PDF downloaded successfully to {filepath}")
        else:
            print("URL did not point to a PDF file.")
    except requests.RequestException as e:
        print(f"Error downloading PDF: {e}")


# This is the regular expression that selects the papers of interest
regex = re.compile(
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


# We select entries based on title or summary (abstract, for some feeds)
def entryMatches(entry):
    # Malformed entry
    if "title" not in entry:
        return False

    if regex.search(entry.title):
        return True
    if "summary" in entry:
        return regex.search(entry.summary)
    else:
        return False


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
        with open("posted.dat", "r") as f:
            return f.read().splitlines()
    except OSError:
        return []


class PapersBot:
    posted = []
    n_seen = 0
    n_tweeted = 0

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
        with open("posted.dat", "a+") as f:
            print(url, file=f)
        self.posted.append(url)

    # Main function, iterating over feeds and posting new items
    def run(self):
        def get_pdf_url(doi: str):
            """
            Fetches the PDF URL from Unpaywall using the provided DOI.

            Args:
                doi (str): The DOI of the paper.

            Returns:
                str: The PDF URL if available, otherwise None.
            """
            try:
                api_url = f"https://api.unpaywall.org/v2/{doi}?email={os.environ['UNPAYWALL_EMAIL']}"
                response = requests.get(api_url)
                response.raise_for_status()
                data = response.json()

                # Check for Open Access and PDF URL
                if (
                    data.get("is_oa")
                    and data.get("best_oa_location")
                    and data["best_oa_location"].get("url_for_pdf", None)
                ):
                    return data["best_oa_location"].get("url_for_pdf")
                else:
                    print("No PDF available for this DOI.")
                    return None

            except requests.exceptions.RequestException as e:
                print(f"Error fetching data from Unpaywall: {e}")
                return None

        for feed in self.feeds:
            try:
                parsed_feed = feedparser.parse(feed)
            except ConnectionResetError as e:
                # Print information about which feed is failing, and what is the error
                print("Failure to load feed at URL", feed)
                print("Exception info:", str(e))
                sys.exit(1)

            for entry in parsed_feed.entries:
                if entry.id in self.posted:
                    continue
                if entryMatches(entry):
                    print("found", entry)
                    if "prism_doi" in entry:
                        doi = entry["prism_doi"]
                    elif "summary" in entry:
                        doi = entry["summary"][
                            entry["summary"].find("DOI</b>: ") + 9 : entry[
                                "summary"
                            ].find(",", entry["summary"].find("DOI</b>: ") + 9)
                        ]
                    else:
                        continue
                    self.n_seen += 1
                    download_pdf(get_pdf_url(doi), "./downloaded_papers")
                    self.addToPosted(entry.id)

    # Print statistics of a given run
    def printStats(self):
        print(f"Number of relevant papers: {self.n_seen}")
        print(f"Number of papers tweeted: {self.n_tweeted}")


def main():
    bot = PapersBot(False)
    bot.run()
    bot.printStats()


if __name__ == "__main__":
    main()
