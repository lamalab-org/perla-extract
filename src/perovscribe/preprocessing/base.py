from abc import abstractmethod
from perovscribe.types import PathType
from diskcache import Cache
import os
from perovscribe.preprocessing.utils import get_hash

class BasePreprocessor:
    def __init__(self, name: str, cache_dir_root: PathType, use_cache: bool = True):
        self.cache_dir = os.path.join(cache_dir_root, name) if cache_dir_root else None
        self.name = name
        self.cache = Cache(self.cache_dir) if self.cache_dir else None
        self.use_cache = use_cache

    @abstractmethod
    def _pdf_to_text(self, pdf_path: PathType) -> str:  # The abstract method that subclasses must implement
        raise NotImplementedError()

    def pdf_to_text(self, pdf_path: PathType) -> str:  # The public method with caching logic
        if self.use_cache and self.cache is not None:
            filehash = get_hash(pdf_path)
            if cached := self.cache.get(str(filehash)):
                return cached
            else:
                output = self._pdf_to_text(pdf_path)
                self.cache.set(str(filehash), output)
                return output
        else:
            return self._pdf_to_text(pdf_path)
