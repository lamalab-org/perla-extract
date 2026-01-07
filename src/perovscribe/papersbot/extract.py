from perovscribe.pipeline import ExtractionPipeline
from nomad.units import ureg
from perovskite_solar_cell_database.llm_extraction_schema import LLMExtractedPerovskiteSolarCell
import json
import os
from utils import get_authentication_token, push_to_nomad
from loguru import logger
from papersbot_new import base_path

model_name = os.environ['LLM_MODEL_NAME']
api_token = os.environ['LLM_API_TOKEN']
try:
    upload_id = os.environ['UPLOAD_ID']
except KeyError:
    upload_id = None

def extract_from_pdfs():
    """
    Extract data from PDFs listed in found_pdf_urls.json
    """    
    with open(f"{base_path}/found_pdf_urls.json", "r") as f:
            found_urls = json.load(f)
    for doi, item in found_urls.items():
        # Define a filepath for the downloaded PDF
        if 'extraction_processed' in item and item['extraction_processed']:
            continue
        filepath = f"downloaded_papers/{doi.replace('/', '--')}.pdf"
        if os.path.isfile(filepath):
            logger.info(f"Running extraction for {doi}: {filepath}")
            try:
                out=ExtractionPipeline('claude-4-sonnet-20250514', 'pymupdf', 'NONE', '', False).extract_from_pdf_nomad(filepath, doi, api_token, LLMExtractedPerovskiteSolarCell, ureg)
                json.dump(out, open(f'{filepath[:-4]}.json', 'w'), indent=4)
                found_urls[doi]['extracted'] = True
            except Exception as e:
                logger.error(f"Error processing DOI:{doi} {filepath}: {e}")
                found_urls[doi]['extracted'] = False
            found_urls[doi]['extraction_processed'] = True
    with open(f"{base_path}/found_pdf_urls.json", "w") as f:
        json.dump(found_urls, f, indent=4)

def export_to_nomad():
    """
    Export extracted data to NOMAD
    """
    token = get_authentication_token()
    if token is None:
        logger.error('Could not get authentication token, exiting')
        return
    with open(f"{base_path}/found_pdf_urls.json", "r") as f:
            found_urls = json.load(f)
    for doi, item in found_urls.items():
        if 'nomad_processed' in item and item['nomad_processed']:
            continue
        # Define a filepath for the downloaded PDF
        filepath = f"downloaded_papers/{doi.replace('/', '--')}.json"
        if os.path.isfile(filepath):
            logger.info(f"Exporting to nomad {doi} {filepath}")
            try:
                response = json.load(open(filepath, 'r'))
                push_to_nomad(doi, response, token, upload_id)
                found_urls[doi]['nomad_upload_processed'] = True
            except Exception as e:
                logger.error(f"Error processing {filepath}: {e}")
                found_urls[doi]['nomad_upload_processed'] = False
            found_urls[doi]['nomad_processed'] = True
    with open(f"{base_path}/found_pdf_urls.json", "w") as f:
        json.dump(found_urls, f, indent=4)


