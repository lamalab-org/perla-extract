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
        try:
            full_text = pdf_to_md(filepath)
            print("Calling Anthropic API")
            response = anthropic_call(PerovskiteSolarCells, text=full_text)
        except Exception as e:
            print(f"Error processing {filepath}: {str(e)}")

    stem = Path(filepath).stem
    timestr = time.strftime("%Y%m%d-%H%M%S")
    with open(f"{output_folder}/{stem}_{timestr}.json", "w") as f:
        f.write(response.model_dump_json()) if response is not None else f.write(
            "Error"
        )
    return response


def extract_all_pdfs(input_folder, output_folder, vision_model=False):
    pdf_files = Path(input_folder).glob("*.pdf")
    for pdf_file in pdf_files:
        extract_one_pdf(pdf_file, output_folder, vision_model)


if __name__ == "__main__":
    Fire(extract_all_pdfs)
