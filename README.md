# perovskite-extraction

## Getting started

At this point, this repository is relatively simplistic and only a collection of modules from initial exploration. Once we find that a certain approach works, we should move it into a package.

## Installation

To install the dependencies, run the following command:

```bash
pip install -r requirements.txt
```

## Usage

The main script to run an extraction is `extract.py`. It takes as arguments the path to the PDF file to extract from as well as the output directory. For example:

```bash
python extract.py papers/aenm.201801509.pdf output
```

By default, it will use [Nougat](https://github.com/facebookresearch/nougat) to convert the PDF into Markdown. Alternatively, you can use `--vision` to pass images of the paper to a vision language model.
