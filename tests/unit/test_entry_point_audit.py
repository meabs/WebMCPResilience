from pathlib import Path


ADAPTER_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "webmcp_resilience"
    / "browser"
    / "adapter.js"
)


def test_primary_entry_point_does_not_reference_navigator_model_context() -> None:
    """Keep navigator.modelContext confined to the documented legacy fallback."""
    source = ADAPTER_PATH.read_text()
    primary_declaration = next(
        line for line in source.splitlines() if line.startswith("  const primaryContext =")
    )

    assert "document.modelContext" in primary_declaration
    assert "navigator.modelContext" not in primary_declaration
    assert "const legacyNavigatorContext" in source
    assert "Keep this isolated fallback for pages on older WebMCP surfaces" in source
