from abc import abstractmethod
from typing import Union
from pathlib import Path
from diskcache import Cache
import os
from perovscribe.preprocessing.utils import get_hash


class BasePreprocessor:
    def __init__(
        self, name: str, cache_dir_root: Union[Path, str], use_cache: bool = True
    ):
        self.cache_dir = os.path.join(cache_dir_root, name) if cache_dir_root else None
        self.name = name
        self.use_cache = use_cache
        self.cache = None
        if self.use_cache:
            self.cache = (
                Cache(self.cache_dir)
                if self.cache_dir
                else Cache(Path.home() / "perovskite_extraction_cache")
            )

    @abstractmethod
    def _pdf_to_text(
        self, pdf_path: Union[Path, str]
    ) -> str:  # The abstract method that subclasses must implement
        raise NotImplementedError()

    def pdf_to_text(
        self, pdf_path: Union[Path, str]
    ) -> str:  # The public method with caching logic
        if self.use_cache and self.cache is not None:
            filehash = get_hash(pdf_path)
            if cached := self.cache.get(str(filehash)):
                return cached
            else:
                output = self._pdf_to_text(pdf_path)
                self.cache.set(str(filehash), output)
                return output
        return self._pdf_to_text(pdf_path)
