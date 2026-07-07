import os
import pickle
import requests
from perla_extract.configuration import papersbot_runs_path
from loguru import logger
import xml.etree.ElementTree as ET

UNPAYWALL_EMAIL = os.environ.get("UNPAYWALL_EMAIL")


def save_summaries(summaries, current=True):
    if current:
        with open(f"{papersbot_runs_path}/curr_summaries.pkl", "wb") as f:
            pickle.dump(summaries, f)
    else:
        old_summaries = pickle.load(open(f"{papersbot_runs_path}/summaries.pkl", "rb"))
        old_summaries.update(summaries)
        with open(f"{papersbot_runs_path}/summaries.pkl", "wb") as f:
            pickle.dump(old_summaries, f)


def get_doi_summary(doi: str) -> dict:
    """
    Fetches paper metadata from various sources using its DOI.
    Args:
        doi: The Digital Object Identifier (DOI) of the paper.
    Returns:
        A dictionary with metadata or an error message string.
    """
    doi = doi.lower().strip()
    summaries = {}
    doi_summary_funcs = {
        "crossref": get_doi_summary_crossref,
        "openalex": get_doi_summary_openalex,
        "semantic_scholar": get_doi_summary_semantic_scholar,
        "pubmed": get_doi_summary_pubmed,
    }
    for source in doi_summary_funcs:
        summary = doi_summary_funcs[source](doi)
        summaries[source] = summary
        if "error" not in summary and summary.get("abstract", "") != "":
            break

    metadata = {}
    for k in summaries:
        abstract = summaries[k].get("abstract", "")
        journal = summaries[k].get("journal", "")
        publisher = summaries[k].get("publisher", "")
        metadata["abstract"] = abstract if abstract else metadata.get("abstract", "")
        metadata["journal"] = journal if journal else metadata.get("journal", "")
        metadata["publisher"] = (
            publisher if publisher else metadata.get("publisher", "")
        )
    summaries["consolidated"] = metadata
    return summaries


def get_doi_summary_crossref(doi: str) -> dict:
    """
    Fetches paper metadata from CrossRef using its DOI.
    Args:
        doi: The Digital Object Identifier (DOI) of the paper.
    Returns:
        A dictionary with metadata or an error message string.
    """
    doi = doi.lower().strip()
    url = f"https://api.crossref.org/works/{doi}"
    headers = {"Accept": "application/json"}

    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        try:
            data = response.json()
            work = data["message"]
            return {
                "title": work.get("title", [""])[0],
                "abstract": work.get("abstract", ""),
                "authors": [
                    f"{author.get('given', '')} {author.get('family', '')}"
                    for author in work.get("author", [])
                ],
                "published": work.get("published-print", {}).get("date-parts", [[]])[0],
                "journal": work.get("container-title", [""])[0],
                "publisher": work.get("publisher", ""),
                "doi": work.get("DOI", ""),
                "source": "crossref",
            }
        except Exception as e:
            return {"error": f"Error: {e}"}
    return {"error": f"Error: Status code {response.status_code}"}


def get_doi_summary_openalex(doi: str) -> dict:
    doi = doi.lower().strip()
    api_url = f"https://api.openalex.org/works/https://doi.org/{doi}"
    response = requests.get(api_url)

    if response.status_code == 200:
        try:
            data = response.json()
            title = data.get("title")
            # Abstract (reconstructed from inverted index)
            abstract = ""
            if data.get("abstract_inverted_index"):
                inverted_index = data["abstract_inverted_index"]
                word_list = [""] * (max(inverted_index.values())[0] + 1)
                for word, positions in inverted_index.items():
                    for pos in positions:
                        word_list[pos] = word
                abstract = " ".join(word_list)

            # Authors
            authors = [
                author["author"]["display_name"]
                for author in data.get("authorships", [])
            ]

            # Journal (called 'source' in OpenAlex)
            source = data.get("primary_location", {}).get("source", {})
            journal = source.get("display_name", "")
            publisher = source.get("host_organization_name", "")

            # Publication Date
            published_date = data.get("publication_date")

            return {
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "journal": journal,
                "publisher": publisher,
                "published_date": published_date,
                "doi": doi,
                "source": "openalex",
            }
        except Exception as e:
            return {"error": f"Error: {e}"}
    return {"error": f"Error: Status code {response.status_code}"}


def get_doi_summary_semantic_scholar(doi: str, api_key: str = "") -> dict:
    """
    Fetches paper metadata from Semantic Scholar using its DOI.

    Args:
        doi: The Digital Object Identifier (DOI) of the paper.
        api_key: Your Semantic Scholar API key (optional but recommended for higher rate limits).

    Returns:
        A dictionary with metadata or None if the paper isn't found.
    """
    # Sanitize DOI input
    doi = doi.lower().strip()

    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    # Specify the fields we want to retrieve
    fields = "title,abstract,authors.name,journal,publicationDate"
    api_url = (
        f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields={fields}"
    )
    response = requests.get(api_url, headers=headers)

    if response.status_code == 200:
        try:
            data = response.json()
            journal_info = data.get("journal")
            journal = journal_info.get("name") if journal_info else None
            abstract = data.get("abstract", "")
            if abstract is None:
                abstract = ""
            return {
                "title": data.get("title", ""),
                "abstract": abstract,
                "authors": [author["name"] for author in data.get("authors", [])],
                "journal": journal,
                "publisher": "",
                "published_date": data.get("publicationDate", ""),
                "doi": doi,
                "source": "semantic_scholar",
            }
        except Exception as e:
            return {"error": f"Error: {e}"}
    return {"error": f"Error: Status code {response.status_code}"}


def get_pmid_from_doi(doi: str) -> dict:
    """
    Fetches the PubMed ID (PMID) for a given DOI using the NCBI E-utilities API.

    Args:
        doi (str): The DOI of the paper.
    Returns:
        The corresponding PMID if found.
    """
    response = requests.get(
        f"https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/?tool=my_tool&email=my_email@example.com&ids={doi}"
    )
    if response.status_code == 200:
        try:
            record = ET.fromstring(response.content).find("record")
            pmid = record.attrib["pmid"] if record is not None else None
        except Exception as e:
            return {"error": f"Error: {e}"}
        return {"pmid": pmid} if pmid else {"error": "PMID not found"}
    return {"error": f"Error: Status code {response.status_code}"}


def get_doi_summary_pubmed(doi):
    """
    Fetches paper metadata from PubMed using a DOI.

    Args:
        doi (str): The DOI of the paper.
        email (str): Your email address (required by NCBI).

    Returns:
        dict: A dictionary containing the paper's metadata.
    """
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    pmid = get_pmid_from_doi(doi)
    if "error" in pmid:
        return {"error": pmid["error"]}
    pmid = pmid["pmid"]

    fetch_response = requests.get(
        f"{base_url}efetch.fcgi", params={"db": "pubmed", "id": pmid, "retmode": "xml"}
    )
    if fetch_response.status_code != 200:
        return {"error": f"Error: Status code {fetch_response.status_code}"}
    # Parse the XML
    try:
        root = ET.fromstring(fetch_response.content)
        article = root.find(".//PubmedArticle/MedlineCitation/Article")

        title = article.findtext("ArticleTitle")
        journal = article.findtext("Journal/Title")
        publisher = root.findtext(".//Publisher/PublisherName")

        pub_date_node = article.find("Journal/JournalIssue/PubDate")
        pub_date_str = (
            "/".join([pub_date_node.findtext(i) for i in ["Day", "Month", "Year"]])
            if pub_date_node is not None
            else ""
        )

        abstract_element = article.find("Abstract")
        abstract_texts = []

        if abstract_element is not None:
            for text_node in abstract_element.findall("AbstractText"):
                abstract_texts.append(text_node.text or "")
            full_abstract = "\n\n".join(abstract_texts)
        else:
            full_abstract = ""

        authors = []
        for i in article.find("AuthorList"):
            authors.append(
                f"{i.findtext('ForeName') or ''} {i.findtext('LastName') or ''}"
            )

        return {
            "doi": doi,
            "title": title,
            "abstract": full_abstract,
            "journal": journal,
            "publisher": publisher,
            "published_date": pub_date_str,
            "source": "pubmed",
            "authors": authors,
        }
    except Exception as e:
        return {"error": f"Error: {e}"}


def get_doi(entry):
    try:
        if "prism_doi" in entry:
            doi = entry["prism_doi"]  # + '  direct'
        elif "dc_identifier" in entry:
            doi = entry["dc_identifier"]
        elif "id" in entry and "arXiv" in entry["id"]:
            doi = entry["id"]
            doi = "10.48550/arXiv." + doi.split(":")[-1].split("v")[0]
        elif "summary" in entry and "DOI" in entry["summary"]:
            if "DOI</b>: " in entry["summary"]:
                r = "DOI</b>: "
                sep = ","
            elif "DOI: " in entry["summary"]:
                r = "DOI: "
                sep = "<"
            n = len(r)
            doi = entry["summary"][
                entry["summary"].find(r) + n : entry["summary"].find(
                    sep, entry["summary"].find(r) + n
                )
            ]  # + '  from summary' + entry['summary']
        elif "link" in entry and "doi/" in entry["link"]:
            doi = entry["link"].split("doi/")[-1]  # + '  from link'
        else:
            doi = ""
        return doi.strip().lower()
    except Exception as e:
        return f"error getting doi: {e}"



def fetch_openalex_works_by_date(start_date: str, end_date: str, email: str = None):
    """
    Fetches articles from OpenAlex for specific topics within a date range.
    
    :param start_date: Start date in 'YYYY-MM-DD' format.
    :param end_date: End date in 'YYYY-MM-DD' format.
    :param email: Optional but recommended. Puts you in the OpenAlex 'polite pool' for faster limits.
    :return: A list of dictionaries containing the selected fields for each work.
    """
    
    base_url = "https://api.openalex.org/works"
    
    # Using the polite pool makes your requests faster and more reliable
    headers = {"User-Agent": f"mailto:{email}"} if email else {}
    
    # We add from_publication_date and to_publication_date to the filter
    filters = (
        "topics.id:T10247|T10624|T12309,"
        "type:article,"
        f"from_publication_date:{start_date},"
        f"to_publication_date:{end_date}"
    )
    
    # Base parameters reflecting your URL
    params = {
        "filter": filters,
        "sort": "publication_date:desc",
        "select": "id,doi,title,publication_date,publication_year,abstract_inverted_index",
        "per_page": 100,
        "page": 1
    }
    
    all_results = []
    
    print(f"Fetching works from {start_date} to {end_date}...")
    
    while True:
        response = requests.get(base_url, params=params, headers=headers)
        response.raise_for_status()  # Stop and raise an error if the request fails
        
        data = response.json()
        results = data.get("results", [])
        
        # If there are no results on this page, we've reached the end
        if not results:
            break
            
        all_results.extend(results)
        
        # Check pagination metadata
        meta = data.get("meta", {})
        total_count = meta.get("count", 0)
        
        logger.info(f"Fetched page {params['page']} - Got {len(all_results)} of {total_count} total results")
        
        # If we have collected all available items, break the loop
        if len(all_results) >= total_count:
            break
            
        # Increment the page number for the next request
        params["page"] += 1
    results=[]
    for result in all_results:
        doi = result.get("doi", "")
        title = result.get("title", "")
        publication_date = result.get("publication_date", "")
        publication_year = result.get("publication_year", "")
        abstract_inverted_index = result.get("abstract_inverted_index", {})
        
        # Reconstruct the abstract from the inverted index
        abstract = ""
        if abstract_inverted_index:
            word_list = [""] * (max(abstract_inverted_index.values())[0] + 1)
            for word, positions in abstract_inverted_index.items():
                for pos in positions:
                    word_list[pos] = word
            abstract = " ".join(word_list)
        
        results.append({
            "doi": doi,
            "title": title,
            "publication_date": publication_date,
            "publication_year": publication_year,
            "abstract": abstract
        })
    return results

def get_pdf_url(doi: str):
    """
    Fetches the PDF URL using multiple services in order: Unpaywall, OpenAlex.

    Args:
        doi (str): The DOI of the paper.

    Returns:
        tuple[bool, str | None]: A tuple containing an error flag and the PDF URL if available, otherwise None.
    """
    return_msg = ""
    for get_pdf_url_func in [get_pdf_url_unpaywall, get_pdf_url_openalex]:
        error, is_oa, key, pdf_url = get_pdf_url_func(doi)
        if pdf_url and not error:
            return error, is_oa, key, pdf_url
        return_msg += pdf_url if pdf_url else ""
    logger.error(f"No PDF available for this DOI: {doi}. OA status: {is_oa}. Details: {return_msg}")
    return error, is_oa, key, return_msg if return_msg else None


def get_pdf_url_unpaywall(doi: str) -> tuple[bool, bool, str | None, str | None]:
    """
    Fetches the PDF URL from Unpaywall using the provided DOI.

    Args:
        doi (str): The DOI of the paper.

    Returns:
        str: The PDF URL if available, otherwise None.
    """
    doi = doi.lower().strip()
    try:
        api_url = f"https://api.unpaywall.org/v2/{doi}?email={UNPAYWALL_EMAIL}"
        response = requests.get(api_url)
        response.raise_for_status()
        data = response.json()
        is_oa = data.get("is_oa", False)
        if (
            is_oa
            and data.get("best_oa_location")
        ):
            for key in ["url_for_pdf", "url", "url_for_landing_page"]:
                url = data["best_oa_location"].get(key, None)
                if url:
                    break
            return False, is_oa, key, url
        else:
            logger.error(f"Unpaywall:No PDF available for this DOI: {doi}.")
            return False, is_oa, None, None

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data from Unpaywall: {e}")
        return True, False, None, f"Error fetching data from Unpaywall: {e}"


def get_pdf_url_openalex(doi: str) -> tuple[bool, bool, str| None, str | None]:
    doi = doi.lower().strip()
    try:
        api_url = f"https://api.openalex.org/works/https://doi.org/{doi}"
        response = requests.get(api_url)
        data = response.json()
        is_oa = data.get("best_oa_location", {}).get("is_oa", False)
        if (
            is_oa
            and data.get("best_oa_location")
        ):
            for key in ["pdf_url", "landing_page_url"]:
                url = data["best_oa_location"].get(key, None)
                if url:
                    break
            return False, is_oa, key, url
        else:
            logger.error(f"Openalex: No PDF available for this DOI : {doi}")
            return False, is_oa, None, None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data from Openalex: {e}")
        return True, False, None, f"Error fetching data from Openalex: {e}"


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def download_pdf(url: str, filepath: str) -> bool:
    """
    Downloads a PDF from a given URL and saves it to a folder.

    Args:
        url (str): The URL of the PDF to download.
        filepath (str): The path where the PDF should be saved.
    """

    try:
        # --- Make the request with headers, stream=True, and a timeout ---
        with requests.get(
            url, headers=HEADERS, stream=True, timeout=30, allow_redirects=True
        ) as response:
            # Check if the request was successful
            response.raise_for_status()

            # --- Save the file in chunks ---
            with open(filepath, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            logger.info(f"Successfully downloaded and saved to {filepath}\n")
            return True

    except requests.exceptions.HTTPError as e:
        # Specifically handles HTTP errors like 404, 403, 500 etc.
        logger.error(f"Error downloading PDF: {e} for url: {url}\n")
    except requests.exceptions.RequestException as e:
        # Handles other network-related errors (e.g., connection aborted, timeout)
        logger.error(f"Error downloading PDF: {e} for url: {url}\n")
    except Exception as e:
        # Handles any other unexpected errors
        logger.error(f"An unexpected error occurred: {e} for url: {url}\n")
    return False


async def playwright_download_pdf(url: str, filepath: str) -> bool:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=False)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()
        page.set_default_timeout(60000)
        async with page.expect_download() as download_info:
            try:
                # 2. Go to the URL (this will trigger the download and the error)
                await page.goto(url)
            except Exception as e:
                # 3. Catch the specific error you got and let the script continue
                if "Download is starting" in str(e):
                    logger.info(
                        f"Playwright download started successfully for url: {url}"
                    )
                else:
                    logger.error(f"Playwright error navigating to {url}: {e}")
                    await browser.close()
                    return False
        download = await download_info.value

        await download.save_as(filepath)

        logger.info(f"Playwright download completed. File saved to: {filepath}")

        await browser.close()
        return True


async def interact_with_pdf_links(url):
    from playwright.sync_api import sync_playwright
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        # Launch browser (headless=False so you can see the action)
        browser =  await p.firefox.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(60000)
        await page.goto(url)

        pdf_locator = await page.locator('a[href*="pdf" i]:visible')

        # 2. Count how many matching links were found
        max_attempts = 5
        wait_time_ms = 2000  # 2 seconds

        for attempt in range(max_attempts):
            count = await pdf_locator.count()
            
            if count > 0:
                print(f"Success! Found {count} links on attempt {attempt + 1}.")
                break  # Exit the loop once we find them
            else:
                print(f"Attempt {attempt + 1}: No links found. Sleeping...")
                await page.wait_for_timeout(wait_time_ms)
        else:
            # This 'else' triggers if the loop finishes without hitting 'break'
            print("Gave up! Links never appeared after maximum attempts.")
            await browser.close()
            return
        links=[]
        # 3. Iterate through and print the href of each link
        for i in range(count):
            link = await pdf_locator.nth(i)
            full_url = await link.evaluate("node => node.href")
            links.append(full_url)
        logger.info(f"Found {len(links)} PDF links on the page.")
        await browser.close()
        filtered_links = []
        for link in links:
            if not any(phrase in link.lower() for phrase in ["suppl","static-content","/epdf","/pb-assets/"]):
               filtered_links.append(link)
        logger.info(f"Filtered down to {len(filtered_links)} PDF links after removing unwanted phrases.")
        return filtered_links[0] if filtered_links else None
