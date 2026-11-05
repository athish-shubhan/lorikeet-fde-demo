from .base import Final, Model, ToolCall
from .scripted import ScriptedModel


def get_model(name: str = "scripted"):
    if name == "scripted":
        return ScriptedModel()
    if name.startswith("claude"):
        from .claude import ClaudeModel

        _, _, model_id = name.partition(":")
        return ClaudeModel(model_id) if model_id else ClaudeModel()
    raise ValueError(f"unknown model {name!r}; use 'scripted' or 'claude[:model-id]'")


__all__ = ["Final", "Model", "ToolCall", "ScriptedModel", "get_model"]
