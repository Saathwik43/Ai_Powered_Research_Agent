import unittest

from ai.edit_target import (
    looks_overrun,
    mark_target,
    resolve_span,
    splice,
    trim_span,
    unwrap_echo,
)

SECTION = (
    "First paragraph about the method [1].\n\n"
    "```mermaid\nxychart-beta\n    bar [1, 2]\n```\n\n"
    "Third paragraph about the results [2]."
)


class TestResolveSpan(unittest.TestCase):
    def test_valid_offsets_are_used(self):
        target = "First paragraph"
        span = resolve_span(SECTION, target, 0, len(target))
        self.assertEqual(SECTION[span[0]:span[1]], target)

    def test_stale_offsets_fall_back_to_a_literal_search(self):
        # The section was hand-edited after the selection was captured, so the
        # offsets now point somewhere else entirely.
        span = resolve_span(SECTION, "Third paragraph", 0, 15)
        self.assertEqual(SECTION[span[0]:span[1]], "Third paragraph")

    def test_offsets_out_of_range_do_not_crash(self):
        span = resolve_span(SECTION, "Third paragraph", 9000, 9100)
        self.assertEqual(SECTION[span[0]:span[1]], "Third paragraph")

    def test_missing_text_resolves_to_nothing(self):
        self.assertIsNone(resolve_span(SECTION, "text that was deleted", None, None))

    def test_ambiguous_text_is_refused(self):
        content = "the same words here and the same words there"
        self.assertIsNone(resolve_span(content, "the same words", None, None))

    def test_ambiguous_text_is_accepted_when_offsets_disambiguate(self):
        content = "the same words here and the same words there"
        second = content.rindex("the same words")
        span = resolve_span(content, "the same words", second, second + 14)
        self.assertEqual(span, (second, second + 14))

    def test_empty_and_whitespace_targets_are_ignored(self):
        for target in (None, "", "   \n  "):
            with self.subTest(target=target):
                self.assertIsNone(resolve_span(SECTION, target, 0, 3))

    def test_span_is_trimmed_so_surrounding_blank_lines_survive(self):
        content = "A.\n\n  middle  \n\nB."
        start, end = resolve_span(content, "\n  middle  \n", None, None)
        self.assertEqual(content[start:end], "middle")

    def test_whitespace_only_span_resolves_to_nothing(self):
        self.assertIsNone(trim_span("a   b", 1, 4))

    def test_booleans_are_not_mistaken_for_offsets(self):
        # bool is a subclass of int; True/False must not be read as 1/0.
        span = resolve_span(SECTION, "Third paragraph", True, False)
        self.assertEqual(SECTION[span[0]:span[1]], "Third paragraph")


class TestUnwrapEcho(unittest.TestCase):
    def test_plain_reply_is_untouched(self):
        self.assertEqual(unwrap_echo("A revised sentence [1]."), "A revised sentence [1].")

    def test_echoed_target_tags_are_stripped(self):
        self.assertEqual(unwrap_echo("<target>Revised.</target>"), "Revised.")

    def test_diagram_reply_is_unfenced(self):
        reply = "```mermaid\nxychart-beta\n    bar [3, 4]\n```"
        self.assertEqual(unwrap_echo(reply, unwrap_fence=True), "xychart-beta\n    bar [3, 4]")

    def test_prose_reply_keeps_a_fence_it_was_asked_for(self):
        reply = "```mermaid\npie title X\n```"
        self.assertEqual(unwrap_echo(reply, unwrap_fence=False), reply)

    def test_only_a_whole_wrapping_fence_is_removed(self):
        reply = "Some prose.\n\n```mermaid\npie title X\n```\n\nMore prose."
        self.assertEqual(unwrap_echo(reply, unwrap_fence=True), reply)


class TestSpliceAndGuards(unittest.TestCase):
    def test_splice_preserves_everything_outside_the_span(self):
        start, end = resolve_span(SECTION, "xychart-beta\n    bar [1, 2]", None, None)
        out = splice(SECTION, start, end, "pie title Split\n    \"A\": 60\n    \"B\": 40")
        self.assertIn("First paragraph about the method [1].", out)
        self.assertIn("Third paragraph about the results [2].", out)
        self.assertIn("```mermaid", out)
        self.assertIn("pie title Split", out)
        self.assertNotIn("xychart-beta", out)

    def test_splice_can_delete_a_span(self):
        start, end = resolve_span(SECTION, "First paragraph", None, None)
        self.assertTrue(splice(SECTION, start, end, "").startswith(" about the method"))

    def test_a_proportionate_reply_is_not_an_overrun(self):
        self.assertFalse(looks_overrun("x" * 300, "y" * 500))

    def test_a_reply_that_echoes_the_section_is_an_overrun(self):
        self.assertTrue(looks_overrun("short target", "y" * 4000))

    def test_an_empty_reply_is_not_an_overrun(self):
        self.assertFalse(looks_overrun("short target", ""))

    def test_mark_target_brackets_only_the_span(self):
        marked = mark_target("abcdef", 2, 4)
        self.assertEqual(marked, "ab⟦EDIT⟧cd⟦/EDIT⟧ef")


if __name__ == "__main__":
    unittest.main()
