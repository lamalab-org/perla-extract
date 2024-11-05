from pathlib import Path
import time
import tempfile
import hydra
import os
from typing import Optional
from omegaconf import DictConfig
from perovscribe.preprocessing import PDFPreprocessor
from perovscribe.process_pdf import process_pdf
from perovscribe.llm_call import anthropic_call
from perovscribe.pydantic_model_reduced import PerovskiteSolarCells


class ExtractionPipeline:
    """Handle the extraction pipeline for perovskite solar cell data."""

    def __init__(
        self,
        config: DictConfig,
    ):
        """
        Initialize the extraction pipeline.

        Args:
            config: Configuration containing all settings
        """
        self.config = config
        self.preprocessor = PDFPreprocessor(config.cache_dir)

        if not os.getenv("ANTHROPIC_API_KEY"):
            raise ValueError("ANTHROPIC_API_KEY environment variable must be set")

    def extract_from_pdf(self, filepath: str) -> Optional[PerovskiteSolarCells]:
        """
        Extract information from a single PDF file.
        """
        stem = Path(filepath).stem
        existing_jsons = list(Path(self.config.output_folder).glob(f"{stem}_*.json"))
        if existing_jsons:
            print(f"Skipping {filepath} - already processed")
            return None

        response = None
        if self.config.vision_model:
            with tempfile.TemporaryDirectory() as tmp_output_folder:
                image_paths = process_pdf(filepath, tmp_output_folder)
                print("Calling Anthropic API")
                response = anthropic_call(
                    response_model=PerovskiteSolarCells,
                    images=image_paths,
                    vision_model=True,
                    config=self.config,
                )
        else:
            error = ""
            try:
                full_text = self.preprocessor.pdf_to_md(filepath)
                print("Calling Anthropic API")
                response = anthropic_call(
                    response_model=PerovskiteSolarCells,
                    text=full_text,
                    config=self.config,
                )
            except Exception as e:
                error = str(e)
                print(f"Error processing {filepath}: {str(e)}")

        timestr = time.strftime("%Y%m%d-%H%M%S")
        output_path = Path(self.config.output_folder) / f"{stem}_{timestr}.json"
        with open(output_path, "w") as f:
            f.write(response.model_dump_json() if response is not None else error)

        return response

    def process_all_pdfs(self) -> None:
        """Process all PDFs in the input folder."""
        Path(self.config.output_folder).mkdir(parents=True, exist_ok=True)
        pdf_files = list(Path(self.input_folder).glob("*.pdf"))
        total_pdfs = len(pdf_files)
        processed = 0
        skipped = 0

        print(f"Found {total_pdfs} PDF files")
        for pdf_file in pdf_files:
            stem = pdf_file.stem
            existing_jsons = list(
                Path(self.config.output_folder).glob(f"{stem}_*.json")
            )
            if existing_jsons:
                print(f"Skipping {pdf_file.name} - already processed")
                skipped += 1
                continue

            print(f"Processing {pdf_file.name} ({processed + 1}/{total_pdfs})")
            self.extract_from_pdf(pdf_file)
            processed += 1

        print(f"\nProcessing complete:")
        print(f"Total PDFs: {total_pdfs}")
        print(f"Processed: {processed}")
        print(f"Skipped: {skipped}")


@hydra.main(version_base=None, config_path="../../conf", config_name="config")
def main(cfg: DictConfig) -> None:
    """
    Main function to run the extraction pipeline.

    Args:
        cfg: Hydra configuration object
    """
    pipeline = ExtractionPipeline(cfg)
    pipeline.process_all_pdfs()


if __name__ == "__main__":
    main()
