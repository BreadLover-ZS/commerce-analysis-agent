"""Static safe constraint hints; never echo rejected values or error messages."""

SERIES_CONSTRAINT = {
    "code": "exactly_one_entity",
    "fields": ["filters.skus", "filters.categories"],
    "combined_items": 1,
    "instruction": "每次仅指定一个已授权SKU或一个品类；多个对象须分别提议，不自动分拆。",
}
