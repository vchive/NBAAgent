"""Compatibility facade for the chat state machine."""

from .chat_use_case import ChatUseCase

Orchestrator = ChatUseCase

__all__ = ["ChatUseCase", "Orchestrator"]
