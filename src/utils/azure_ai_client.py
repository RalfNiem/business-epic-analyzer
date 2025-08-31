"""
Azure AI Client
===============

This module provides a unified client for various Azure AI services.
It offers a consistent interface for calling different model families,
such as Azure OpenAI and Azure AI Foundation Models, abstracting away the
differences in their underlying SDKs and capabilities (e.g., multimodal vs. text-only).

This version is designed to work with the native structured output capabilities
of the 'openai' library (v1.0+) and does not require third-party libraries like 'instructor'.
"""

import os
import base64
from typing import Dict, Optional, Any, List

# The official OpenAI library for interacting with Azure OpenAI
from openai import AzureOpenAI

# Specific clients for Azure AI Foundation Models
from azure.ai.inference import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.core.credentials import AzureKeyCredential


class AzureAIClient:
    """
    A unified client to interact with Azure OpenAI and Azure AI Foundation models.

    This class routes requests to the appropriate Azure service based on the
    specified model name. It handles specific model capabilities, such as
    multimodal input for OpenAI models and JSON formatting for foundation models.
    """
    # Lists of supported models, categorized by the backend service
    AZURE_OPENAI_MODELS = [
        "gpt-5-mini",
        "gpt-5-chat",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4o",
        "o3-mini",
        "o4-mini"]

    AZURE_AI_FOUNDATION_MODELS = [
        "DeepSeek-V3-0324",
        "DeepSeek-R1-0528",
        "gpt-oss-120b",
        "Llama-3.3-70B-Instruct",
        "Llama-4-Maverick-17B-128E-Instruct-FP8",
        "mistral-medium-2505",
        "Phi-4"]

    # Define OpenAI reasoning models which need special handling
    OPENAI_REASONING_MODELS = [
        "gpt-5-mini",
        "o3-mini",
        "o4-mini"]

    def __init__(self, system_prompt: str = "You are a helpful assistant."):
        """
        Initializes the AzureAIClient with a default system prompt.

        Args:
            system_prompt (str): The default system message to send to the AI.
        """
        self.system_prompt = system_prompt
        # Client instances are lazily initialized on first use
        self.openai_client: Optional[AzureOpenAI] = None
        self.foundation_client: Optional[ChatCompletionsClient] = None

        # Immediately initialize the primary OpenAI client upon creation.
        self._initialize_openai_client()

    def get_available_models(self) -> Dict[str, List[str]]:
        """
        Gets all available models grouped by their API type.

        Returns:
            Dict[str, List[str]]: A dictionary mapping API families to a list of
                                  supported model names.
        """
        return {
            "Azure OpenAI (multimodal)": self.AZURE_OPENAI_MODELS,
            "Azure AI Foundation (text-only)": self.AZURE_AI_FOUNDATION_MODELS
        }

    def _initialize_openai_client(self):
        """Initializes the Azure OpenAI client if it hasn't been already."""
        if self.openai_client is None:
            self.openai_client = AzureOpenAI(
                api_key=os.environ.get("AZURE_OPENAI_API_KEY"),
                api_version=os.environ.get("AZURE_OPENAI_API_VERSION"),
                azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT"),
                timeout=60.0
            )

    def _initialize_foundation_client(self):
        """Initializes the Azure AI Foundation client if it hasn't been already."""
        if self.foundation_client is None:
            self.foundation_client = ChatCompletionsClient(
                endpoint=os.environ.get("AZURE_AIFOUNDRY_ENDPOINT"),
                credential=AzureKeyCredential(os.environ.get("AZURE_AIFOUNDRY_API_KEY")),
            )

    def _encode_image(self, image_path: str) -> str:
        """
        Reads an image file and encodes it into a base64 string.

        Args:
            image_path (str): The local file path to the image.

        Returns:
            str: The base64-encoded string representation of the image.
        """
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def _is_reasoning_model(self, model_name: str) -> bool:
        """
        Checks if the model is designated as a high-reasoning model.

        Args:
            model_name (str): The name of the model to check.

        Returns:
            bool: True if the model is in the reasoning list, False otherwise.
        """
        return any(model_name.startswith(rm) for rm in self.OPENAI_REASONING_MODELS)

    def completion(self,
                model_name: str,
                user_prompt: str,
                image_path: Optional[str] = None,
                temperature: float = 0,
                max_tokens: int = 2048,
                response_format: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Generates a standard completion, routing to the correct API.

        This method is intended for use cases that do not require structured
        Pydantic objects and instead expect a simple text response.

        Args:
            model_name (str): The name of the model to use for the completion.
            user_prompt (str): The text prompt to send to the model.
            image_path (Optional[str]): The file path for an image to include in
                the prompt (only for multimodal models). Defaults to None.
            temperature (float): The sampling temperature for the completion.
            max_tokens (int): The maximum number of tokens to generate.
            response_format (Optional[Dict[str, str]]): A dictionary specifying
                the response format (e.g., `{"type": "json_object"}`).

        Raises:
            ValueError: If the model name is unknown or if an image is provided
                to a model that does not support it.

        Returns:
            Dict[str, Any]: A dictionary containing the model's response text
                and token usage statistics.
        """
        if model_name in self.AZURE_OPENAI_MODELS or self._is_reasoning_model(model_name):
            return self._generate_openai(model_name, user_prompt, image_path, temperature, max_tokens, response_format)
        elif model_name in self.AZURE_AI_FOUNDATION_MODELS:
            if image_path:
                raise ValueError(f"Model {model_name} does not support images.")
            return self._generate_foundation(model_name, user_prompt, temperature, max_tokens, response_format)
        else:
            available_models = self.AZURE_OPENAI_MODELS + self.AZURE_AI_FOUNDATION_MODELS
            raise ValueError(f"Unknown model: {model_name}. Available models: {available_models}")


    def _generate_openai(self, model_name, user_prompt, image_path=None, temperature=0, max_tokens=2048, response_format=None):
        """
        Generates a completion using the Azure OpenAI service.
        Handles both text-only and multimodal (text + image) inputs.
        """
        messages = [{"role": "system", "content": self.system_prompt}]
        if image_path:
            base64_image = self._encode_image(image_path)
            messages.append({"role": "user", "content": [{"type": "text", "text": user_prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}", "detail": "high"}}]})
        else:
            messages.append({"role": "user", "content": user_prompt})

        # Dynamically build arguments for the API call
        kwargs = {"model": model_name, "messages": messages} # Initialize without temperature
        if response_format:
            kwargs["response_format"] = response_format

        # Note: 'max_tokens' and 'temperature' are handled differently by different model families
        if self._is_reasoning_model(model_name):
            kwargs["max_completion_tokens"] = max_tokens
            # Do not add 'temperature' as it is not supported by these models
        else:
            kwargs["max_tokens"] = max_tokens
            # Only add 'temperature' for models that support it
            kwargs["temperature"] = temperature

        response = self.openai_client.chat.completions.create(**kwargs)
        return {
            "text": response.choices[0].message.content,
            "usage": response.usage
        }


    def _generate_foundation(self, model_name, user_prompt, temperature=0, max_tokens=2048, response_format=None):
        """
        Generates a completion using the Azure AI Foundation Models service.
        Handles text-only inputs and enforces JSON output when requested.
        """
        self._initialize_foundation_client()
        system_prompt_text = self.system_prompt
        if response_format and response_format.get("type") == "json_object":
            system_prompt_text += " IMPORTANT: The output must be exclusively in JSON format as specified in the user prompt!"

        messages = [SystemMessage(content=system_prompt_text), UserMessage(content=user_prompt)]

        # Build arguments for the API call
        kwargs = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature, "model": model_name}

        # Add response_format ONLY if the model supports it and it's requested to be JSON
        if response_format and response_format.get("type") == "json_object" and model_name in ["DeepSeek-V3-0324", "Llama-3.3-70B-Instruct", "Mistral-Large-2411"]:
             kwargs["response_format"] = {"type": response_format.get("type")}

        response = self.foundation_client.complete(**kwargs)
        return {
            "text": response.choices[0].message.content,
            "usage": response.usage
        }
