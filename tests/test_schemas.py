from mineru_like.schemas import caption_schema, cross_page_schema, layout_schema, structured_schema


def test_task_specific_shapes_and_fresh_values() -> None:
    layout = layout_schema()["items"]
    structured = structured_schema()["items"]
    assert layout["required"] == ["bbox", "category"]
    assert structured["required"] == ["bbox", "category", "content"]
    assert caption_schema() == structured_schema()
    assert cross_page_schema()["items"]["required"] == ["source_pages", "html"]
    structured["properties"]["category"]["enum"].clear()
    assert structured_schema()["items"]["properties"]["category"]["enum"]
