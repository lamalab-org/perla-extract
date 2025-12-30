# Perovscribe

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

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
- 📦 **Evaluation Dataset**: Includes ground truth data and extractions from multiple LLM models and human annotators for benchmarking

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

## Data Directory

The data directory (`src/perovscribe/data/`) contains:
- **Extractions**: Results from multiple LLM models and human annotators (including consensus annotations)
- **Ground Truth**: Manually checked and corrected datasets (dev set for optimization, test set for evaluation)

See [`src/perovscribe/data/README.md`](src/perovscribe/data/README.md) for detailed information about the data structure and organization.

## Quick Start

### Setup

Set up the required environment variables for LLM API access and paper downloading:

```bash
# For Claude models (default)
export ANTHROPIC_API_KEY="your-anthropic-api-key"

# For OpenAI models (alternative)
export OPENAI_API_KEY="your-openai-api-key"

# For downloading papers via Papersbot
export UNPAYWALL_EMAIL="your-email@example.com"
```

LiteLLM supports many providers. Set the appropriate API key environment variable for your chosen model:
- `ANTHROPIC_API_KEY` for Claude models
- `OPENAI_API_KEY` for GPT models
- `GOOGLE_API_KEY` for Gemini models
- See [LiteLLM documentation](https://docs.litellm.ai/docs/providers) for other providers

### Run the Default Pipeline

The simplest way to see Perovscribe in action:

```bash
perovscribe
```

This will:
1. Download papers using Papersbot
2. Extract data from all PDFs using the default model
3. Clean up downloaded files

### Extract Data from a PDF

```bash
# Single PDF
perovscribe extract pdfs/paper.pdf

# With specific model
perovscribe extract --model_name=gpt-4o-mini pdfs/paper.pdf --output results/

# Directory of PDFs
perovscribe extract pdfs/ --output extractions/
```

### Evaluate Extractions

```bash
# Evaluate model against ground truth
perovscribe evaluate src/perovscribe/data/extractions/claude-opus-4-1-20250805/ src/perovscribe/data/ground_truth/test/

# Evaluate human performance
perovscribe evaluate src/perovscribe/data/extractions/humans/Consensus/ src/perovscribe/data/ground_truth/test/
```

## Command Reference

### `perovscribe extract`

Extract data from PDF files.

```bash
perovscribe extract <filepath> [--model_name=MODEL] [--preprocessor=PROCESSOR] [--output=DIR] [--nomad] [--nomad_upload_id=ID]
```

**Key options:**
- `--model_name`: LLM model (default: `claude-sonnet-4-20250514`). Supports any LiteLLM model (e.g., `gpt-4o-mini`, `claude-3-5-sonnet-20240620`)
- `--preprocessor`: PDF processor - `pymupdf`, `nougat`, or `marker` (default: `pymupdf`)
- `--output`: Output directory (default: `./extractions`)
- `--nomad`: Upload to NOMAD repository
- `--use_cache`: Enable API call caching

### `perovscribe evaluate`

Evaluate extraction results against ground truth.

```bash
perovscribe evaluate <extraction_dir> <truth_dir>
```

### `perovscribe papersbot`

Download papers automatically. Requires `UNPAYWALL_EMAIL` environment variable (see Quick Start for setup).

### `perovscribe optimizer`

Run prompt optimization pipeline.

## Uploading to NOMAD

Perovscribe can automatically upload extraction results to [NOMAD](https://nomad-lab.eu/), a materials science data repository.

**Setup:**
```bash
export NOMAD_USERNAME="your-username"
export NOMAD_PASSWORD="your-password"
export NOMAD_URL="https://nomad-lab.eu/prod/v1/"  # Optional
```

**Usage:**
```bash
# Upload to new upload
perovscribe extract --nomad pdfs/paper.pdf

# Append to existing upload
perovscribe extract --nomad --nomad_upload_id="upload-id" pdfs/paper.pdf
```

Each device/cell is uploaded as a separate NOMAD entry with automatic format conversion.

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

