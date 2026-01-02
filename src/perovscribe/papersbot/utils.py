import requests
import os

import requests
from loguru import logger
import os
import xml.etree.ElementTree as ET


def get_doi_summary(doi: str) -> dict:
    """
    Fetches paper metadata from various sources using its DOI.
    Args:
        doi: The Digital Object Identifier (DOI) of the paper.
    Returns:
        A dictionary with metadata or an error message string.
    """
    doi = doi.lower().strip()
    summaries = {"crossref": {}, "openalex": {}, "semantic_scholar": {}}
    doi_summary_funcs = {
        "crossref": get_doi_summary_crossref,
        "openalex": get_doi_summary_openalex,
        "semantic_scholar": get_doi_summary_semantic_scholar,
    }
    for source in ["crossref", "openalex", "semantic_scholar"]:
        summary = doi_summary_funcs[source](doi)
        summaries[source] = summary
        if "error" not in summary and summary.get("abstract", "") != "":
            return summary
    return {}

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
            source=data.get('primary_location',{}).get('source',{})
            journal = source.get("display_name", "")
            publisher = source.get("host_organization_name","")

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
    response=requests.get(f'https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/?tool=my_tool&email=my_email@example.com&ids={doi}')
    if response.status_code == 200:
        try:
            record = ET.fromstring(response.content).find('record')
            pmid = record.attrib['pmid'] if record is not None else None 
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

    fetch_response = requests.get(f"{base_url}efetch.fcgi", params={"db": "pubmed", "id": pmid, "retmode": "xml"})
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
        pub_date_str = "/".join([pub_date_node.findtext(i) for i in ["Day","Month","Year"]]) if pub_date_node is not None else ""

        abstract_element = article.find("Abstract")
        abstract_texts = []

        if abstract_element is not None:
            for text_node in abstract_element.findall("AbstractText"):
                abstract_texts.append(text_node.text or "")
            full_abstract = "\n\n".join(abstract_texts)
        else:
            full_abstract = ""

        authors = []
        for i in article.find('AuthorList'):
            authors.append(f'{i.findtext("ForeName") or ""} {i.findtext("LastName") or ""}')

        return {
                "doi": pmid,
                "title": title,
                "abstract": full_abstract,
                "journal": journal,
                "publisher": publisher,
                "published_date": pub_date_str,
                "source": "pubmed",
                "authors": authors
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
        return doi
    except Exception as e:
        return f"error getting doi: {e}"


def get_pdf_url(doi: str) -> tuple[bool, str|None]:
    """
    Fetches the PDF URL using multiple services in order: Unpaywall, OpenAlex.

    Args:
        doi (str): The DOI of the paper.

    Returns:
        str: The PDF URL if available, otherwise None.
    """
    return_msg = ""
    for get_pdf_url_func in [get_pdf_url_unpaywall, get_pdf_url_openalex]:
        error, pdf_url = get_pdf_url_func(doi)
        if pdf_url and not error:
            return error, pdf_url
        return_msg += pdf_url if pdf_url else ""
    logger.error(f"No PDF available for this DOI: {doi}.")
    return True, return_msg if return_msg else None

def get_pdf_url_unpaywall(doi: str) -> tuple[bool, str|None]:
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

        # Check for Open Access and PDF URL
        if (
            data.get("is_oa")
            and data.get("best_oa_location")
            and data["best_oa_location"].get("url_for_pdf", None)
        ):
            return False, data["best_oa_location"].get("url_for_pdf")
        else:
            logger.error(f"Unpaywall:No PDF available for this DOI: {doi}.")
            return False, None

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data from Unpaywall: {e}")
        return True, f"Error fetching data from Unpaywall: {e}"

def get_pdf_url_openalex(doi: str) -> tuple[bool, str|None]:
    doi = doi.lower().strip()
    try:
        api_url = f"https://api.openalex.org/works/https://doi.org/{doi}"
        response = requests.get(api_url)
        data = response.json()
        if (
        data.get("best_oa_location")
        and data["best_oa_location"].get("is_oa")
        and data["best_oa_location"].get("pdf_url", None)
             ):
            return False, data["best_oa_location"].get("pdf_url")
        else:
            logger.error(f"Openalex: No PDF available for this DOI : {doi}")
            return False, None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data from Openalex: {e}")
        return True, f"Error fetching data from Openalex: {e}"


HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
}

def download_pdf(url: str, filepath: str):
    """
    Downloads a PDF from a given URL and saves it to a folder.

    Args:
        url (str): The URL of the PDF to download.
        download_folder (str): The folder to save the PDF in.
    """

    try:
        # --- Make the request with headers, stream=True, and a timeout ---
        with requests.get(url, headers=HEADERS, stream=True, timeout=30, allow_redirects=True) as response:
            # Check if the request was successful
            response.raise_for_status()

            # --- Save the file in chunks ---
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            logger.info(f"Successfully downloaded and saved to {filepath}\n")

    except requests.exceptions.HTTPError as e:
        # Specifically handles HTTP errors like 404, 403, 500 etc.
        logger.error(f"Error downloading PDF: {e} for url: {url}\n")
    except requests.exceptions.RequestException as e:
        # Handles other network-related errors (e.g., connection aborted, timeout)
        logger.error(f"Error downloading PDF: {e} for url: {url}\n")
    except Exception as e:
        # Handles any other unexpected errors
        logger.error(f"An unexpected error occurred: {e} for url: {url}\n")



