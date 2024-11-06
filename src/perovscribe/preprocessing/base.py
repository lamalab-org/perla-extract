from abc import ABC, abstractmethod
from perovscribe.types import PathType
from diskcache import Cache
from functools import cached_property
import os
from perovscribe.preprocessing.utils import get_hash

class BasePreprocessor:

    def __init__(self, name: str, cache_dir_root: PathType, use_cache: bool=True)
        self.cache_dir = os.path.join(self.cache_dir, name)
        self.name = name
        self.cache = Cache(self.cache_dir)
        self.use_cache = use_cache

    @abstractmethod
    def _pdf_to_text(self, pdf_path: PathType) -> str:
        raise NotImplementedError()

    def pdf_to_text(self, pdf_path: PathType) -> str:
        if self.use_cache:
            filehash = get_hash(pdf_path)
            if (cached := self.cache.get(str(filehash)))
                return cached

            else:
                output = self._pdf_to_text(pdf_path)
                self.cache.set(str(filehash), output)
        else:
            return self._pdf_to_text(pdf_path)
