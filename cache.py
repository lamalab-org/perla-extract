import diskcache
from pydantic import BaseModel
import functools
import inspect
cache = diskcache.Cache("./extraction_cache")


def instructor_cache(func):
    """Cache a function that returns a Pydantic model"""
    return_type = inspect.signature(func).return_annotation
    if not issubclass(return_type, BaseModel):
        raise ValueError("The return type must be a Pydantic model")

    is_async = inspect.iscoroutinefunction(func)

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        key = f"{func.__name__}-{functools._make_key(args, kwargs, typed=False)}"
        # Check if the result is already cached
        if (cached := cache.get(key)) is not None:
            # Deserialize from JSON based on the return type
            if issubclass(return_type, BaseModel):
                return return_type.model_validate_json(cached)

        # Call the function and cache its result
        result = func(*args, **kwargs)
        serialized_result = result.model_dump_json()
        cache.set(key, serialized_result)

        return result

    @functools.wraps(func)
    async def awrapper(*args, **kwargs):
        key = f"{func.__name__}-{functools._make_key(args, kwargs, typed=False)}"
        # Check if the result is already cached
        if (cached := cache.get(key)) is not None:
            # Deserialize from JSON based on the return type
            if issubclass(return_type, BaseModel):
                return return_type.model_validate_json(cached)

        # Call the function and cache its result
        result = await func(*args, **kwargs)
        serialized_result = result.model_dump_json()
        cache.set(key, serialized_result)

        return result

    return wrapper if not is_async else awrapper
