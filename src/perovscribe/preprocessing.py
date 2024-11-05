import functools
import inspect
from pathlib import Path
from typing import Tuple, Optional
from diskcache import Cache
from pydantic import BaseModel
from marker.convert import convert_single_pdf
from marker.models import load_all_models


class PreprocessingCache:
    """Handle caching of preprocessed PDFs."""

    def __init__(self, cache_dir: str = "./extraction_cache"):
        """
        Initialize the cache.

        Args:
            cache_dir (str): Directory path for cache storage
        """
        self.cache = Cache(cache_dir)

    def cache_result(self, func):
        """
        Decorator to cache function results.

        Args:
            func: Function to cache

        Returns:
            Wrapped function with caching
        """
        return_type = inspect.signature(func).return_annotation
        if not issubclass(return_type, BaseModel):
            raise ValueError("The return type must be a Pydantic model")

        is_async = inspect.iscoroutinefunction(func)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = f"{func.__name__}-{functools._make_key(args, kwargs, typed=False)}"

            if (cached := self.cache.get(key)) is not None:
                if issubclass(return_type, BaseModel):
                    return return_type.model_validate_json(cached)

            result = func(*args, **kwargs)
            serialized_result = result.model_dump_json()
            self.cache.set(key, serialized_result)
            return result

        @functools.wraps(func)
        async def awrapper(*args, **kwargs):
            key = f"{func.__name__}-{functools._make_key(args, kwargs, typed=False)}"

            if (cached := self.cache.get(key)) is not None:
                if issubclass(return_type, BaseModel):
                    return return_type.model_validate_json(cached)

            result = await func(*args, **kwargs)
            serialized_result = result.model_dump_json()
            self.cache.set(key, serialized_result)
            return result

        return wrapper if not is_async else awrapper


class PDFPreprocessor:
    """Handle PDF preprocessing with caching."""

    def __init__(self, cache_dir: str = "./preprocessing_cache"):
        """
        Initialize the preprocessor.

        Args:
            cache_dir (str): Directory path for cache storage
        """
        self.cache = Cache(cache_dir)
        self.model_lst = load_all_models()

    def pdf_to_md(self, pdf_path: str) -> str:
        """
        Convert PDF to markdown format with caching.

        Args:
            pdf_path (str): Path to the PDF file

        Returns:
            str: Converted markdown text
        """
        if (cached := self.cache.get(pdf_path)) is not None:
            print("Using cached version")
            return cached

        full_text, images, out_meta = convert_single_pdf(pdf_path, self.model_lst)
        self.cache.set(pdf_path, full_text)
        return full_text

    def process_pdf(self, pdf_path: str) -> Tuple[str, Optional[list]]:
        """
        Process a PDF file and return its text and any images.

        Args:
            pdf_path (str): Path to the PDF file

        Returns:
            Tuple[str, Optional[list]]: Tuple of (text content, list of image paths if any)
        """
        try:
            text = self.pdf_to_md(pdf_path)
            return text, None
        except Exception as e:
            print(f"Error processing {pdf_path}: {str(e)}")
            return "", None
