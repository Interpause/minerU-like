"""JSON shapes from the bundled OvisOCR3 task prompts."""

from .contracts import Payload

_CATEGORIES = "abstract algorithm aside_text chart content display_formula doc_title figure_title footer footer_image footnote formula_number header header_image image inline_formula number paragraph_title reference_content seal table text vertical_text vision_footnote".split()


def _records(content: bool) -> Payload:
    properties: Payload = {
        "bbox": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0, "maximum": 999},
            "minItems": 4,
            "maxItems": 4,
        },
        "category": {"type": "string", "enum": _CATEGORIES.copy()},
    }
    if content:
        properties["content"] = {"type": "string"}
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


def structured_schema() -> Payload:
    return _records(True)


def layout_schema() -> Payload:
    return _records(False)


def caption_schema() -> Payload:
    return _records(True)


def cross_page_schema() -> Payload:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "source_pages": {"type": "array", "items": {"type": "integer", "minimum": 1}},
                "html": {"type": "string"},
            },
            "required": ["source_pages", "html"],
            "additionalProperties": False,
        },
    }
