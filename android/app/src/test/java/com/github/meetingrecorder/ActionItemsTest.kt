package com.github.meetingrecorder

import com.github.meetingrecorder.data.ActionItems
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class ActionItemsTest {

    private val defaultPromptNotes = """
        ### Summary
        We planned the October show.

        ### Key points
        - **Brett** wants the venue booked.
        - Budget is tight.

        ## Action Items
        - [ ] Brett to ring the venue about the deposit
        - [ ] **Emily** to send the budget by Friday
    """.trimIndent()

    @Test
    fun `the default prompt's shape`() {
        assertEquals(
            listOf("Brett to ring the venue about the deposit", "Emily to send the budget by Friday"),
            ActionItems.extract(defaultPromptNotes),
        )
    }

    @Test
    fun `bullets before the heading are not actions`() {
        assertFalse("Budget is tight." in ActionItems.extract(defaultPromptNotes))
    }

    @Test
    fun `next steps counts`() {
        assertEquals(listOf("Book the hall", "Tell the choir"), ActionItems.extract("## Next steps\n- Book the hall\n- Tell the choir\n"))
    }

    @Test
    fun `the heading is case blind`() {
        assertEquals(listOf("Book the hall"), ActionItems.extract("# ACTION ITEMS\n- Book the hall\n"))
    }

    @Test
    fun `a bold line is a heading`() {
        assertEquals(listOf("Book the hall"), ActionItems.extract("**Action items:**\n- Book the hall\n"))
    }

    @Test
    fun `a plain line with a colon is a heading`() {
        assertEquals(listOf("Book the hall", "Pay"), ActionItems.extract("Next steps:\n1. Book the hall\n2) Pay\n"))
    }

    @Test
    fun `the next heading ends the section`() {
        val notes = "## Action items\n- Book the hall\n## Decisions\n- Keep the date\n"
        assertEquals(listOf("Book the hall"), ActionItems.extract(notes))
    }

    @Test
    fun `a subheading stays inside the section`() {
        val notes = "## Action items\n### Brett\n- Book the hall\n### Emily\n- Pay\n## Other\n- x\n"
        assertEquals(listOf("Book the hall", "Pay"), ActionItems.extract(notes))
    }

    @Test
    fun `a plain colon line ends a bold section`() {
        val notes = "**Next steps**\n- Book the hall\nDecisions:\n- Keep the date\n"
        assertEquals(listOf("Book the hall"), ActionItems.extract(notes))
    }

    @Test
    fun `nested detail is left out`() {
        val notes = "## Action items\n- Book the hall\n  - the big one\n- Pay\n"
        assertEquals(listOf("Book the hall", "Pay"), ActionItems.extract(notes))
    }

    @Test
    fun `a tab-indented item is nested too`() {
        val notes = "## Action items\n- Book the hall\n\t- the big one\n"
        assertEquals(listOf("Book the hall"), ActionItems.extract(notes))
    }

    @Test
    fun `a ticked box is already done`() {
        assertEquals(listOf("Pay"), ActionItems.extract("## Action items\n- [x] Book the hall\n- [ ] Pay\n"))
    }

    @Test
    fun `prose under the heading is not an action`() {
        assertEquals(emptyList<String>(), ActionItems.extract("## Action items\nNothing firm was agreed.\n"))
    }

    @Test
    fun `an open box outside a section counts`() {
        assertEquals(listOf("Ring Sam"), ActionItems.extract("## Summary\n- [ ] Ring Sam\n- plain\n"))
    }

    @Test
    fun `a ticked box outside a section does not`() {
        assertEquals(emptyList<String>(), ActionItems.extract("- [X] Ring Sam\n"))
    }

    @Test
    fun `emphasis and code go`() {
        assertEquals("Brett to fix deploy.sh", ActionItems.clean("**Brett** to fix `deploy.sh`"))
    }

    @Test
    fun `a link keeps its text`() {
        assertEquals("Read the brief", ActionItems.clean("Read [the brief](https://example.com/x)"))
    }

    @Test
    fun `underscores in words survive`() {
        assertEquals("Rename snake_case_thing", ActionItems.clean("Rename snake_case_thing"))
    }

    @Test
    fun `repeats are dropped`() {
        assertEquals(listOf("Book the hall"), ActionItems.extract("## Action items\n- Book the hall\n- book the  hall\n"))
    }

    @Test
    fun `a long line is cut`() {
        assertEquals(200, ActionItems.extract("## Action items\n- " + "x".repeat(400))[0].length)
    }

    @Test
    fun `windows line endings still parse`() {
        assertEquals(listOf("Book the hall"), ActionItems.extract("## Action items\r\n- Book the hall\r\n"))
    }

    @Test
    fun `no notes no actions`() {
        assertEquals(emptyList<String>(), ActionItems.extract(""))
    }
}
