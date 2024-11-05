import instructor
from anthropic import Anthropic
from perovscribe.process_pdf import encode_image_to_base64
from typing import List, Optional, Type, TypeVar
from omegaconf import DictConfig
import hydra
import os

T = TypeVar("T")

def get_anthropic_client() -> Anthropic:
    """
    Initialize and return an Anthropic client using the API key from environment variables.

    Returns:
        Anthropic: Initialized Anthropic client

    Raises:
        ValueError: If ANTHROPIC_API_KEY environment variable is not set
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable must be set")
    return Anthropic(api_key=api_key)


def anthropic_call(
    *,  # Enforce keyword arguments
    response_model: Type[T],
    text: Optional[str] = None,
    images: Optional[List[str]] = None,
    vision_model: bool = False,
    config: Optional[DictConfig] = None,
) -> T:
    """
    Make a call to Anthropic's API to extract perovskite solar cell data from text or images.

    Args:
        response_model (Type[T]): The type to parse the response into
        text (Optional[str]): The text content to process
        images (Optional[List[str]]): List of image paths to process
        vision_model (bool): Whether to use vision capabilities
        config (Optional[DictConfig]): Configuration containing model settings and prompts

    Returns:
        T: The parsed response of the specified type
    """
    # Load default config if none provided
    if config is None:
        config = hydra.compose(config_name="config")

    # Initialize client with instructor wrapper
    client = instructor.from_anthropic(get_anthropic_client())

    # Prepare message content based on mode
    message_content = []
    if vision_model and images:
        for i, image in enumerate(images):
            image_data = encode_image_to_base64(image)
            message_content.extend([
                {"role": "user", "type": "text", "content": f"Image {i}:"},
                {
                    "role": "user",
                    "type": "image",
                    "content": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": image_data,
                    },
                },
            ])
        message_content.append(
            {"role": "user", "type": "text", "content": config.llm.prompts.vision_instruction}
        )
    elif text:
        message_content.extend([
            {"role": "user", "type": "text", "content": config.llm.prompts.text_instruction},
            {"role": "user", "type": "text", "content": text},
        ])
    else:
        raise ValueError("Either text or images must be provided")

    # Make API call
    response = client.messages.create(
        model=config.llm.model,
        max_tokens=config.llm.max_tokens,
        system=config.llm.prompts.system,
        messages=message_content,
        response_model=response_model,
        temperature=config.llm.temperature,
    )

    return response
