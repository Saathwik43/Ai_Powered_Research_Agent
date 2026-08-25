import unittest

from ai.mermaid_check import (
    diagram_flags,
    extract_mermaid_blocks,
    has_unclosed_fence,
    validate_mermaid,
)

GOOD_XYCHART = """xychart-beta
    title "Model Performance Comparison"
    x-axis ["Baseline", "ResNet-50", "Transformer", "Proposed"]
    y-axis "Accuracy (%)" 0 --> 100
    bar [65.4, 78.2, 86.5, 92.8]
    line [65.4, 78.2, 86.5, 92.8]"""


def fenced(body, lang="mermaid"):
    return f"Some prose.\n\n```{lang}\n{body}\n```\n\nMore prose."


class TestExtraction(unittest.TestCase):
    def test_extracts_a_mermaid_fence(self):
        self.assertEqual(extract_mermaid_blocks(fenced(GOOD_XYCHART)), [GOOD_XYCHART])

    def test_extracts_an_unlabelled_fence_that_starts_with_a_diagram_type(self):
        self.assertEqual(len(extract_mermaid_blocks(fenced("flowchart TD\n A --> B", lang=""))), 1)

    def test_ignores_ordinary_code_fences(self):
        self.assertEqual(extract_mermaid_blocks(fenced("print('hello')", lang="python")), [])

    def test_no_fences_at_all(self):
        self.assertEqual(extract_mermaid_blocks("Just a paragraph of prose."), [])

    def test_unclosed_fence_is_detected(self):
        self.assertTrue(has_unclosed_fence("prose\n```mermaid\npie title X\n"))
        self.assertFalse(has_unclosed_fence(fenced(GOOD_XYCHART)))


class TestValidation(unittest.TestCase):
    def test_a_valid_xychart_passes(self):
        self.assertEqual(validate_mermaid(GOOD_XYCHART), "")

    def test_valid_shapes_pass(self):
        for chart in (
            'pie title Split\n    "Train": 70\n    "Test": 30',
            "flowchart TD\n    A[Start] --> B{Quality OK?}\n    B --> C(End)",
            "sequenceDiagram\n    participant A\n    A->>B: msg",
        ):
            with self.subTest(chart=chart.splitlines()[0]):
                self.assertEqual(validate_mermaid(chart), "")

    def test_empty_block(self):
        self.assertIn("empty", validate_mermaid("   "))

    def test_missing_diagram_type(self):
        reason = validate_mermaid('title "Results"\n    bar [1, 2, 3]')
        self.assertIn("does not start with a diagram type", reason)

    def test_truncated_block_has_unbalanced_brackets(self):
        reason = validate_mermaid('xychart-beta\n    x-axis ["A", "B"\n    bar [1, 2]')
        self.assertIn("unbalanced", reason)

    def test_unclosed_quote(self):
        reason = validate_mermaid('pie title Split\n    "Train: 70')
        self.assertIn("unclosed double quote", reason)

    def test_xychart_without_a_series(self):
        reason = validate_mermaid('xychart-beta\n    title "T"\n    x-axis ["A", "B"]')
        self.assertIn("no `bar [...]` or `line [...]`", reason)

    def test_xychart_with_a_non_numeric_value(self):
        reason = validate_mermaid('xychart-beta\n    x-axis ["A", "B"]\n    bar [1, 92.8%]')
        self.assertIn("non-numeric", reason)

    def test_xychart_label_and_value_counts_must_match(self):
        reason = validate_mermaid('xychart-beta\n    x-axis ["A", "B", "C"]\n    bar [1, 2]')
        self.assertIn("3 labels", reason)
        self.assertIn("2 values", reason)

    def test_pie_without_slices(self):
        self.assertIn("no `\"Label\": value` slices", validate_mermaid("pie title Split"))

    def test_flowchart_is_not_held_to_xychart_rules(self):
        self.assertEqual(validate_mermaid("mindmap\n  root((idea))\n    branch"), "")


class TestDiagramFlags(unittest.TestCase):
    """
    The revise path re-emits the whole section, so a ```mermaid block can come
    back renamed, truncated or deleted with the prose looking perfectly fine.
    """

    def test_clean_revision_raises_nothing(self):
        self.assertEqual(diagram_flags(fenced(GOOD_XYCHART), fenced(GOOD_XYCHART)), {})

    def test_broken_diagram_is_reported_with_its_position(self):
        broken = fenced('xychart-beta\n    x-axis ["A", "B"]\n    bar [1, "two"]')
        flags = diagram_flags(fenced(GOOD_XYCHART), broken)
        self.assertEqual(len(flags["diagram_errors"]), 1)
        self.assertEqual(flags["diagram_errors"][0]["index"], 1)
        self.assertIn("non-numeric", flags["diagram_errors"][0]["error"])

    def test_unclosed_fence_is_reported_without_a_position(self):
        flags = diagram_flags(fenced(GOOD_XYCHART), "prose\n```mermaid\npie title X\n")
        self.assertIn({"index": None, "error": "a fenced code block was never closed"},
                      flags["diagram_errors"])

    def test_dropped_diagram_is_counted(self):
        flags = diagram_flags(fenced(GOOD_XYCHART), "Just the prose now.")
        self.assertEqual(flags["diagrams_dropped"], 1)
        self.assertNotIn("diagram_errors", flags)

    def test_an_added_diagram_is_not_reported_as_dropped(self):
        flags = diagram_flags("Just prose.", fenced(GOOD_XYCHART))
        self.assertEqual(flags, {})

    def test_prose_only_sections_are_unaffected(self):
        self.assertEqual(diagram_flags("Old prose.", "New prose."), {})

    def test_second_diagram_is_positioned_correctly(self):
        two = fenced(GOOD_XYCHART) + "\n" + fenced("pie title Split")
        flags = diagram_flags(two, two)
        self.assertEqual([e["index"] for e in flags["diagram_errors"]], [2])


if __name__ == "__main__":
    unittest.main()
