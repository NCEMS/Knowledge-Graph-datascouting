"""
LLM Client for querying OpenAI-compatible chat completion APIs (OpenRouter or OpenAI)
"""

import os
import logging
from typing import Dict, List, Optional, Any
from openai import OpenAI
from datetime import datetime

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LLMClient:
    """Client for LLM interactions with caching support"""
    
    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini-2024-07-18", 
                 temperature: float = 0.3, timeout: int = 300, base_url: Optional[str] = None):
        """
        Initialize LLM client

        Routes through OpenRouter (https://openrouter.ai) by default when
        OPENROUTER_API_KEY is set, since it is a single OpenAI-compatible gateway
        to many model providers. Falls back to calling OpenAI directly using
        OPENAI_API_KEY for backward compatibility when OpenRouter isn't configured.

        Args:
            api_key: Explicit API key; overrides environment auto-detection below.
            model: Model to use. When routed through OpenRouter this must be an
                   OpenRouter model slug (e.g. "openai/gpt-4o-mini"), not a bare
                   OpenAI model name.
            temperature: Temperature for model (default: 0.3, lower = more deterministic)
            timeout: Request timeout in seconds
            base_url: Explicit API base URL; overrides environment auto-detection below.

        Resolution order (when api_key/base_url are not explicitly passed):
            1. OPENROUTER_API_KEY set -> base_url=OpenRouter, api_key=OPENROUTER_API_KEY
            2. OPENAI_API_KEY set     -> base_url=OpenAI's own default, api_key=OPENAI_API_KEY
            3. neither set            -> raises ValueError
        """
        if api_key is not None:
            self.api_key = api_key
            self.base_url = base_url
        elif os.environ.get("OPENROUTER_API_KEY"):
            self.api_key = os.environ["OPENROUTER_API_KEY"]
            self.base_url = base_url if base_url is not None else OPENROUTER_BASE_URL
        elif os.environ.get("OPENAI_API_KEY"):
            self.api_key = os.environ["OPENAI_API_KEY"]
            self.base_url = base_url
        else:
            raise ValueError(
                "API key not found. Set OPENROUTER_API_KEY (preferred) or OPENAI_API_KEY."
            )

        client_kwargs: Dict[str, Any] = {"api_key": self.api_key, "timeout": timeout}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
            # Optional per OpenRouter's docs (https://openrouter.ai/docs) - used only
            # for their request attribution/leaderboards, not required for auth.
            client_kwargs["default_headers"] = {
                "HTTP-Referer": "https://github.com/NCEMS/XLMS-PPI-working-group",
                "X-Title": "xHAMLET",
            }
        self.client = OpenAI(**client_kwargs)
        self.model = model
        self.temperature = temperature
        provider = "OpenRouter" if self.base_url else "OpenAI (default endpoint)"
        logger.info(f"LLMClient initialized with model={model} via {provider}")
        logger.info(f"LLMClient initialized with model: {model}")
    
    def query(self, prompt: str, text: str, system_role: str = "assistant") -> Optional[str]:
        """
        Query LLM with a single prompt and text
        
        Args:
            prompt: System prompt/instruction
            text: User text to analyze
            system_role: System role (default: "assistant")
            
        Returns:
            LLM response or None if failed
        """
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text}
        ]
        
        try:
            logger.info(f"Querying LLM with model {self.model}")
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                store=True,
                timeout=300
            )
            
            response_text = completion.choices[0].message.content
            tokens_used = completion.usage.total_tokens
            logger.info(f"LLM query successful. Tokens used: {tokens_used}")
            
            return response_text
            
        except Exception as e:
            logger.error(f"LLM query failed: {e}")
            return None
    
    def batch_query(self, prompts: Dict[str, str], text: str) -> Dict[str, Optional[str]]:
        """
        Query LLM with multiple prompts for the same text
        
        Args:
            prompts: Dict of {prompt_key: prompt_text}
            text: User text to analyze
            
        Returns:
            Dict of {prompt_key: response}
        """
        responses = {}
        
        for prompt_key, prompt_text in prompts.items():
            logger.info(f"Querying with prompt: {prompt_key}")
            response = self.query(prompt_text, text)
            responses[prompt_key] = response
        
        return responses
    
    def parse_json_response(self, response: str) -> Optional[Dict[str, Any]]:
        """
        Attempt to parse JSON from LLM response
        
        Args:
            response: LLM response text
            
        Returns:
            Parsed JSON dict or None if parsing failed
        """
        import json
        
        if not response:
            return None
        
        try:
            # Try direct JSON parsing
            return json.loads(response)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            if "```json" in response:
                try:
                    json_str = response.split("```json")[1].split("```")[0].strip()
                    return json.loads(json_str)
                except (IndexError, json.JSONDecodeError):
                    pass
            
            # Try other code block formats
            if "```" in response:
                try:
                    json_str = response.split("```")[1].split("```")[0].strip()
                    # Remove language specifier if present
                    if json_str.startswith("json"):
                        json_str = json_str[4:].strip()
                    return json.loads(json_str)
                except (IndexError, json.JSONDecodeError):
                    pass
            
            logger.warning("Could not parse JSON from LLM response")
            return None
    
    def cache_response(self, responses_dict: Dict[str, Any], prompt_key: str, 
                      response: str, metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Format response for caching
        
        Args:
            responses_dict: Existing responses dict to update
            prompt_key: Key identifying the prompt
            response: LLM response text
            metadata: Optional metadata to store with response
            
        Returns:
            Updated responses dict
        """
        entry = {
            "response": response,
            "queried_at": datetime.now().isoformat(),
            "model": self.model
        }
        
        if metadata:
            entry["metadata"] = metadata
        
        # Try to parse as JSON
        parsed = self.parse_json_response(response)
        if parsed:
            entry["parsed"] = parsed
        
        responses_dict[prompt_key] = entry
        return responses_dict
