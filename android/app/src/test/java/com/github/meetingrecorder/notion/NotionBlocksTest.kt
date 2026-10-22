package com.github.meetingrecorder.notion

import com.github.meetingrecorder.data.notion.NotionBlocks
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class NotionBlocksTest {

    private fun JsonObject.type() = this["type"]!!.jsonPrimitive.content

    private fun JsonObject.richText(): JsonArray = this[type()]!!.jsonObject["rich_text"]!!.jsonArray

    private fun JsonObject.plain() = richText().joinToString("") {
        it.jsonObject["text"]!!.jsonObject["content"]!!.jsonPrimitive.content
    }

    @Test
    fun `headings cap at level three`() {
        val blocks = NotionBlocks.markdownToBlocks("# A\n## B\n##### C")
        assertEquals(listOf("heading_1", "heading_2", "heading_3"), blocks.map { it.type() })
    }

    @Test
    fun `bullets numbered quotes rules and checkboxes map to their block types`() {
        val blocks = NotionBlocks.markdownToBlocks("- a\n1. b\n> q\n---\n- [x] done\n- [ ] todo")
        assertEquals(
            listOf("bulleted_list_item", "numbered_list_item", "quote", "divider", "to_do", "to_do"),
            blocks.map { it.type() },
        )
        assertTrue(blocks[4]["to_do"]!!.jsonObject["checked"]!!.jsonPrimitive.boolean)
        assertEquals(false, blocks[5]["to_do"]!!.jsonObject["checked"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun `inline spans become annotations and markers are dropped`() {
        val rich = NotionBlocks.richText("a **bold** and *it* and `code` z")
        assertEquals("a bold and it and code z", rich.joinToString("") { it["text"]!!.jsonObject["content"]!!.jsonPrimitive.content })
        val bold = rich.first { it.containsKey("annotations") }
        assertTrue(bold["annotations"]!!.jsonObject["bold"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun `unmatched markers stay literal`() {
        assertEquals("2 * 3", NotionBlocks.markdownToBlocks("2 * 3").single().plain())
    }

    @Test
    fun `fenced code keeps body and maps unknown language to plain text`() {
        val blocks = NotionBlocks.markdownToBlocks("```kt\nval x = 1\n```\n```cobol\nx\n```")
        assertEquals("kotlin", blocks[0]["code"]!!.jsonObject["language"]!!.jsonPrimitive.content)
        assertEquals("val x = 1", blocks[0].plain())
        assertEquals("plain text", blocks[1]["code"]!!.jsonObject["language"]!!.jsonPrimitive.content)
    }

    @Test
    fun `text over 2000 characters is split into pieces under the ceiling`() {
        val pieces = NotionBlocks.splitText("word ".repeat(1000).trim())
        assertTrue(pieces.size > 1)
        assertTrue(pieces.all { it.length <= NotionBlocks.MAX_TEXT_LENGTH })
    }

    @Test
    fun `unbroken text is hard split at the ceiling`() {
        val pieces = NotionBlocks.splitText("x".repeat(4500))
        assertEquals(listOf(2000, 2000, 500), pieces.map { it.length })
    }

    @Test
    fun `chunkBlocks batches at 100`() {
        val blocks = NotionBlocks.markdownToBlocks((1..250).joinToString("\n") { "- item $it" })
        assertEquals(listOf(100, 100, 50), NotionBlocks.chunkBlocks(blocks).map { it.size })
    }

    @Test
    fun `transcript toggle packs short turns into few children`() {
        val transcript = (1..500).joinToString("\n\n") { "[00:00:$it] Speaker: hello there" }
        val toggle = NotionBlocks.transcriptToggle(transcript)
        val children = toggle["toggle"]!!.jsonObject["children"]!!.jsonArray
        assertEquals("toggle", toggle.type())
        assertTrue(children.size <= NotionBlocks.MAX_BLOCKS_PER_REQUEST)
        assertTrue(children.size < 500)
    }
}
