from model_reduced import PerovskiteSolarCells
from process_pdf import process_pdf
import tempfile
from fire import Fire
from llm_call import anthropic_call
import time
from pathlib import Path
from marker.convert import convert_single_pdf
from marker.models import load_all_models

model_lst = load_all_models()
from diskcache import Cache

cache = Cache(".cache")


def pdf_to_md(pdf_path):
    # check if in cache
    if (cached := cache.get(pdf_path)) is not None:
        print(cached)
        return cached
    else:
        full_text, images, out_meta = convert_single_pdf(pdf_path, model_lst)
        cache.set(pdf_path, full_text)
        return full_text


def extract_one_pdf(
    filepath, output_folder, vision_model=False
) -> PerovskiteSolarCells:
    # Check if PDF has already been processed
    stem = Path(filepath).stem
    existing_jsons = list(Path(output_folder).glob(f"{stem}_*.json"))

    if existing_jsons:
        print(f"Skipping {filepath} - already processed")
        return None

    response = None
    if vision_model:
        with tempfile.TemporaryDirectory() as tmp_output_folder:
            image_paths = process_pdf(filepath, tmp_output_folder)
            print("Calling Anthropic API")
            response = anthropic_call(
                PerovskiteSolarCells, images=image_paths, vision_model=True
            )
    else:
        # convert PDF with marker
        error = ""
        try:
            full_text = pdf_to_md(filepath)
            print("Calling Anthropic API")
            response = anthropic_call(PerovskiteSolarCells, text=full_text)
        except Exception as e:
            error = str(e)
            print(f"Error processing {filepath}: {str(e)}")

    timestr = time.strftime("%Y%m%d-%H%M%S")
    output_path = Path(output_folder) / f"{stem}_{timestr}.json"

    with open(output_path, "w") as f:
        f.write(response.model_dump_json()) if response is not None else f.write(error)

    return response


def extract_all_pdfs(input_folder, output_folder, vision_model=False):
    # Create output folder if it doesn't exist
    Path(output_folder).mkdir(parents=True, exist_ok=True)

    pdf_files = list(Path(input_folder).glob("*.pdf"))
    total_pdfs = len(pdf_files)
    processed = 0
    skipped = 0

    print(f"Found {total_pdfs} PDF files")

    for pdf_file in pdf_files:
        # Check if already processed
        stem = pdf_file.stem
        existing_jsons = list(Path(output_folder).glob(f"{stem}_*.json"))

        if existing_jsons:
            print(f"Skipping {pdf_file.name} - already processed")
            skipped += 1
            continue

        print(f"Processing {pdf_file.name} ({processed + 1}/{total_pdfs})")
        extract_one_pdf(pdf_file, output_folder, vision_model)
        processed += 1

    print(f"\nProcessing complete:")
    print(f"Total PDFs: {total_pdfs}")
    print(f"Processed: {processed}")
    print(f"Skipped: {skipped}")


if __name__ == "__main__":
    Fire(extract_all_pdfs)
