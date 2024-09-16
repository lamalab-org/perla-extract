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

def extract_one_pdf(filepath, output_folder, vision_model=False) -> PerovskiteSolarCells:
    if vision_model:
        with tempfile.TemporaryDirectory() as tmp_output_folder:
            image_paths = process_pdf(filepath, tmp_output_folder)
            print("Calling Anthropic API")
            response = anthropic_call(PerovskiteSolarCells, images=image_paths, vision_model=True)
    else: 
        # convert PDF with marker 
        full_text, images, out_meta = convert_single_pdf(filepath, model_lst)
        print("Calling Anthropic API")
        response = anthropic_call(PerovskiteSolarCells, text=full_text, images=images)
    stem = Path(filepath).stem
    timestr = time.strftime("%Y%m%d-%H%M%S")
    with open(f"{output_folder}/{stem}_{timestr}.json", "w") as f:
        f.write(response.model_dump_json())
    return response

if __name__ == "__main__":
    Fire(extract_one_pdf)