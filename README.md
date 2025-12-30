# Perovscribe

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Perovscribe** is an AI-powered tool for extracting structured data about perovskite solar cells from scientific papers. It uses large language models (LLMs) to automatically extract device parameters, material compositions, performance metrics, and other relevant information from PDF documents.

## Features

- 🔬 **Intelligent Extraction**: Automatically extracts structured data about perovskite solar cells from scientific papers
- 📄 **Multiple PDF Processors**: Supports multiple PDF preprocessing methods (PyMuPDF, Nougat, Marker)
- 🤖 **LLM Integration**: Works with various LLM providers via LiteLLM (Claude, GPT-4, GPT-5, and more)
- ✅ **Structured Output**: Validates and structures data using Pydantic models
- 🔄 **Post-processing**: Automatic unit normalization and data validation
- 📊 **Evaluation Metrics**: Built-in precision and recall evaluation against ground truth
- 📤 **Export Formats**: Export to JSON or NOMAD archive format
- 🤖 **Automated Discovery**: Papersbot integration for automated paper discovery and processing

## Installation

### Prerequisites

- Python 3.10 or higher
- pip

### Basic Installation

```bash
pip install perovscribe
```

### Optional Dependencies

For specific PDF processors:

```bash
# For Nougat OCR processing
pip install perovscribe[nougat]

# For Marker PDF processing
pip install perovscribe[marker]

# For development dependencies
pip install perovscribe[dev]
```

## Quick Start

### Default Pipeline

Run Perovscribe without any arguments to execute the default pipeline: download papers, extract data, and clean up.

```bash
perovscribe
```

This will:
1. Download PDFs using Papersbot
2. Extract data from all PDFs
3. Clean up downloaded files

### Extract Data from a PDF

Extract data from a single PDF file:

```bash
perovscribe extract pdfs/paper.pdf
```

Extract with a specific model and output directory:

```bash
perovscribe extract --model_name=gpt-4o-mini pdfs/paper.pdf --output pdfs/
```

Extract from a directory of PDFs:

```bash
perovscribe extract pdfs/ --output extractions/
```

### Evaluate Extractions

Compare extraction results against ground truth data:

```bash
perovscribe evaluate src/perovscribe/data/extractions/claude-opus-4-1-20250805/ src/perovscribe/data/ground_truth/test/
```

This will compute precision, recall, and other evaluation metrics for all matching files in the directories.

You can also evaluate how humans perform by comparing human extractions against ground truth:

```bash
perovscribe evaluate src/perovscribe/data/extractions/humans/Consensus/ src/perovscribe/data/ground_truth/test/
```

## Command Reference

### `perovscribe`

Run the default pipeline (download papers, extract, clean up).

```bash
perovscribe
```

### `perovscribe extract`

Extract data from PDF files.

**Basic usage:**
```bash
perovscribe extract <filepath>
```

**Options:**
- `--model_name`: LLM model to use - can be any model supported by LiteLLM (default: `claude-sonnet-4-20250514`). Examples: `gpt-4o-mini`, `claude-3-5-sonnet-20240620`, `gpt-4`, etc.
- `--preprocessor`: PDF processor to use - `pymupdf`, `nougat`, or `marker` (default: `pymupdf`)
- `--output`: Output directory for extraction results (default: `./extractions`)
- `--cache_dir`: Cache directory for API calls
- `--use_cache`: Enable caching (default: `False`)
- `--nomad`: Enable automatic upload to NOMAD (default: `False`)
- `--nomad_upload_id`: Optional NOMAD upload ID to append to existing upload (default: `None`)

**Examples:**
```bash
# Extract from a single PDF
perovscribe extract paper.pdf

# Extract with specific model
perovscribe extract --model_name=gpt-4o-mini paper.pdf --output results/

# Extract from directory
perovscribe extract pdfs/ --output extractions/
```

### `perovscribe evaluate`

Evaluate extraction results against ground truth.

**Usage:**
```bash
perovscribe evaluate <extraction_dir> <truth_dir>
```

**Arguments:**
- `extraction_dir`: Directory containing extraction JSON files
- `truth_dir`: Directory containing ground truth JSON files

**Examples:**
```bash
# Evaluate model extractions against ground truth
perovscribe evaluate src/perovscribe/data/extractions/claude-opus-4-1-20250805/ src/perovscribe/data/ground_truth/test/

# Evaluate human performance (human extractions vs ground truth)
perovscribe evaluate src/perovscribe/data/extractions/humans/Consensus/ src/perovscribe/data/ground_truth/test/
```

### `perovscribe papersbot`

Download papers automatically using Papersbot.

```bash
perovscribe papersbot
```

**Note:** Requires `UNPAYWALL_EMAIL` environment variable to be set.

### `perovscribe optimizer`

Run prompt optimization pipeline.

```bash
perovscribe optimizer --model_name=claude-sonnet-4-20250514
```

## Uploading to NOMAD

Perovscribe can automatically upload extraction results to [NOMAD](https://nomad-lab.eu/), a materials science data repository. This allows you to share and archive your extracted perovskite solar cell data.

### Setup

Before uploading to NOMAD, you need to set up authentication using environment variables:

```bash
export NOMAD_USERNAME="your-username"
export NOMAD_PASSWORD="your-password"
export NOMAD_URL="https://nomad-lab.eu/prod/v1/"  # Optional, defaults to this URL
```

### Basic Upload

To upload extractions to NOMAD, use the `--nomad` flag:

```bash
perovscribe extract --nomad pdfs/paper.pdf
```

This will:
1. Extract data from the PDF
2. Convert the extraction to NOMAD format
3. Upload each device/cell as a separate entry to NOMAD
4. Create a new upload for each extraction

### Upload to Existing Upload

To append extractions to an existing NOMAD upload, specify the upload ID:

```bash
perovscribe extract --nomad --nomad_upload_id="your-upload-id" pdfs/paper.pdf
```

This is useful when you want to group multiple extractions into a single upload.

### Batch Upload

You can also upload multiple PDFs at once:

```bash
perovscribe extract --nomad pdfs/ --output extractions/
```

Each PDF will be processed and uploaded separately, with each device in the extraction creating a new entry in NOMAD.

### Notes

- Each device/cell in an extraction is uploaded as a separate NOMAD entry
- The DOI from the paper is automatically included in each entry
- Data is automatically converted to NOMAD's schema format
- Uploads are processed asynchronously; the tool waits for processing to complete
- If an extraction contains no valid cells, it will be skipped with a warning

## Authors

- **Sherjeel Shabih** - sherjeel.shabih@hu-berlin.de
- **Pepe Marquez** - jose.marquez@physik.hu-berlin.de
- **Kevin Jablonka** - mail@kjablonka.com
TODO: add authors

## Citation

If you use Perovscribe in your research, please cite:

```bibtex
TODO:
```

