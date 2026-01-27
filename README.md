# PERLA Extract

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**PERLA Extract** is an automated data extraction tool that uses large language models (LLMs) to identify and structure key information on perovskite solar cells from scientific papers. This includes device parameters, material compositions, and performance metrics, all of which are collected and stored in the **[PERLA](https://fairmat-nfdi.github.io/perla/)** database.

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
pip install perla-extract
```

### Optional Dependencies

For specific PDF processors:

```bash
# For Nougat OCR processing
pip install perla-extract[nougat]

# For Marker PDF processing
pip install perla-extract[marker]

# For Redis-based caching (requires Redis server)
pip install perla-extract[cache]

# For development dependencies
pip install perla-extract[dev]
```

**Note on Caching**: By default, Perla Extract uses disk-based caching for LLM calls. If you have a Redis server available, you can install the `cache` extra and configure Redis via environment variables (`REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_TTL`) for persistent caching across sessions with better performance.

## Data Directory

The data directory (`src/perla_extract/data/`) contains:
- **Extractions**: Results from multiple LLM models and human annotators (including consensus annotations)
- **Ground Truth**: Manually checked and corrected datasets (dev set for optimization, test set for evaluation)

See [`src/perla_extract/data/README.md`](src/perla_extract/data/README.md) for detailed information about the data structure and organization.

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

The simplest way to see Perla Extract in action:

```bash
perla-extract
```

This will:
1. Download papers using Papersbot
2. Extract data from all PDFs using the default model
3. Clean up downloaded files

### Extract Data from a PDF

```bash
# Single PDF
perla-extract extract pdfs/paper.pdf

# With specific model
perla-extract extract --model_name=gpt-4o-mini pdfs/paper.pdf --output results/

# Directory of PDFs
perla-extract extract pdfs/ --output extractions/
```

### Evaluate Extractions

```bash
# Evaluate model against ground truth
perla-extract evaluate src/perla_extract/data/extractions/claude-opus-4-1-20250805/ src/perla_extract/data/ground_truth/test/

# Evaluate human performance
perla-extract evaluate src/perla_extract/data/extractions/humans/Consensus/ src/perla_extract/data/ground_truth/test/
```

## Command Reference

### `perla-extract extract`

Extract data from PDF files.

```bash
perla-extract extract <filepath> [--model_name=MODEL] [--preprocessor=PROCESSOR] [--output=DIR] [--nomad] [--nomad_upload_id=ID]
```

**Key options:**
- `--model_name`: LLM model (default: `claude-sonnet-4-20250514`). Supports any LiteLLM model (e.g., `gpt-4o-mini`, `claude-3-5-sonnet-20240620`)
- `--preprocessor`: PDF processor - `pymupdf`, `nougat`, or `marker` (default: `pymupdf`)
- `--output`: Output directory (default: `./extractions`)
- `--nomad`: Upload to NOMAD repository
- `--use_cache`: Enable API call caching

### `perla-extract evaluate`

Evaluate extraction results against ground truth.

```bash
perla-extract evaluate <extraction_dir> <truth_dir>
```

### `perla-extract papersbot`

Download papers automatically. Requires `UNPAYWALL_EMAIL` environment variable (see Quick Start for setup).

### `perla-extract optimizer`

Run prompt optimization pipeline.

## Uploading to NOMAD

Perla Extract can automatically upload extraction results to [NOMAD](https://nomad-lab.eu/), a materials science data repository.

**Setup:**
```bash
export NOMAD_USERNAME="your-username"
export NOMAD_PASSWORD="your-password"
export NOMAD_URL="https://nomad-lab.eu/prod/v1/"  # Optional
```

**Usage:**
```bash
# Upload to new upload
perla-extract extract --nomad pdfs/paper.pdf

# Append to existing upload
perla-extract extract --nomad --nomad_upload_id="upload-id" pdfs/paper.pdf
```

Each device/cell is uploaded as a separate NOMAD entry with automatic format conversion.

## Authors

- **Sherjeel Shabih** - sherjeel.shabih@hu-berlin.de
- **Pepe Marquez** - jose.marquez@physik.hu-berlin.de
- **Kevin Jablonka** - mail@kjablonka.com
- **Sharat Patil** - sharat.patil@physik.hu-berlin.de

## Citation

If you use Perla Extract in your research, please cite:

```bibtex
TODO:
```

