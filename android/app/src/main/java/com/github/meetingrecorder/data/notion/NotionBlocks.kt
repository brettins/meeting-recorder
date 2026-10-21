package com.github.meetingrecorder.data.notion

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** Markdown to Notion block conversion, mirroring the desktop's notion_blocks.py. */
object NotionBlocks {
    const val MAX_TEXT_LENGTH = 2000
    const val MAX_RICH_TEXT_ITEMS = 100
    const val MAX_BLOCKS_PER_REQUEST = 100

    private val HEADING = Regex("""^(#{1,6})\s+(.*)$""")
    private val BULLET = Regex("""^\s*[-*+]\s+(.*)$""")
    private val NUMBERED = Regex("""^\s*\d+[.)]\s+(.*)$""")
    private val CHECKBOX = Regex("""^\s*[-*+]\s+\[([ xX])\]\s*(.*)$""")
    private val QUOTE = Regex("""^>\s?(.*)$""")
    private val RULE = Regex("""^\s*([-*_])(?:\s*\1){2,}\s*$""")
    private val FENCE = Regex("""^\s*```(\w*)\s*$""")
    private val SPAN = Regex(
        """(\*\*\*.+?\*\*\*|\*\*.+?\*\*|__.+?__|(?<!\*)\*(?!\*).+?(?<!\*)\*(?!\*)|_.+?_|`.+?`)""",
        RegexOption.DOT_MATCHES_ALL,
    )

    private val CODE_LANGUAGES = setOf(
        "bash", "c", "c++", "css", "diff", "docker", "go", "html", "java", "javascript", "json",
        "kotlin", "markdown", "python", "ruby", "rust", "shell", "sql", "swift", "typescript",
        "xml", "yaml",
    )

    private val LANGUAGE_ALIASES = mapOf(
        "js" to "javascript",
        "ts" to "typescript",
        "py" to "python",
        "sh" to "shell",
        "rb" to "ruby",
        "kt" to "kotlin",
        "yml" to "yaml",
        "md" to "markdown",
        "dockerfile" to "docker",
        "cpp" to "c++",
    )

    internal fun splitText(text: String): List<String> {
        if (text.length <= MAX_TEXT_LENGTH) return listOf(text)
        val pieces = mutableListOf<String>()
        var rest = text
        while (rest.length > MAX_TEXT_LENGTH) {
            val window = rest.substring(0, MAX_TEXT_LENGTH)
            var cut = window.lastIndexOf(' ')
            if (cut < MAX_TEXT_LENGTH / 2) cut = MAX_TEXT_LENGTH
            pieces.add(rest.substring(0, cut))
            rest = rest.substring(cut).trimStart()
        }
        if (rest.isNotEmpty()) pieces.add(rest)
        return pieces
    }

    private fun textObject(content: String, annotations: List<String> = emptyList()): JsonObject =
        buildJsonObject {
            put("type", "text")
            put("text", buildJsonObject { put("content", content) })
            if (annotations.isNotEmpty()) {
                put("annotations", buildJsonObject { annotations.forEach { put(it, true) } })
            }
        }

    /** Converts one line of inline Markdown into Notion rich-text objects. */
    fun richText(markdown: String): List<JsonObject> {
        if (markdown.isEmpty()) return emptyList()
        val items = mutableListOf<JsonObject>()

        fun emit(content: String, annotations: List<String> = emptyList()) {
            splitText(content).forEach { if (it.isNotEmpty()) items.add(textObject(it, annotations)) }
        }

        fun emitPart(part: String) {
            when {
                part.startsWith("***") && part.endsWith("***") && part.length > 6 ->
                    emit(part.substring(3, part.length - 3), listOf("bold", "italic"))
                part.startsWith("**") && part.endsWith("**") && part.length > 4 ->
                    emit(part.substring(2, part.length - 2), listOf("bold"))
                part.startsWith("__") && part.endsWith("__") && part.length > 4 ->
                    emit(part.substring(2, part.length - 2), listOf("bold"))
                part.startsWith("`") && part.endsWith("`") && part.length > 2 ->
                    emit(part.substring(1, part.length - 1), listOf("code"))
                part.startsWith("*") && part.endsWith("*") && part.length > 2 ->
                    emit(part.substring(1, part.length - 1), listOf("italic"))
                part.startsWith("_") && part.endsWith("_") && part.length > 2 ->
                    emit(part.substring(1, part.length - 1), listOf("italic"))
                else -> emit(part)
            }
        }

        var last = 0
        for (match in SPAN.findAll(markdown)) {
            if (match.range.first > last) emit(markdown.substring(last, match.range.first))
            emitPart(match.value)
            last = match.range.last + 1
        }
        if (last < markdown.length) emit(markdown.substring(last))
        return items.take(MAX_RICH_TEXT_ITEMS)
    }

    private fun block(kind: String, markdown: String, extra: Map<String, JsonElement> = emptyMap()): JsonObject =
        buildJsonObject {
            put("object", "block")
            put("type", kind)
            put(
                kind,
                buildJsonObject {
                    put("rich_text", JsonArray(richText(markdown)))
                    extra.forEach { (k, v) -> put(k, v) }
                },
            )
        }

    fun paragraph(text: String): JsonObject = buildJsonObject {
        put("object", "block")
        put("type", "paragraph")
        put(
            "paragraph",
            buildJsonObject {
                put("rich_text", JsonArray(splitText(text).map { textObject(it) }))
            },
        )
    }

    private fun codeLanguage(fence: String): String {
        val lowered = fence.trim().lowercase()
        val name = LANGUAGE_ALIASES[lowered] ?: lowered
        return if (name in CODE_LANGUAGES) name else "plain text"
    }

    /** Converts a Markdown document into a flat list of Notion blocks. */
    fun markdownToBlocks(markdown: String): List<JsonObject> {
        val blocks = mutableListOf<JsonObject>()
        val lines = markdown.replace("\r\n", "\n").split("\n")
        var index = 0
        while (index < lines.size) {
            val line = lines[index]
            val stripped = line.trim()

            val fence = FENCE.matchEntire(line)
            if (fence != null) {
                val language = codeLanguage(fence.groupValues[1])
                index++
                val body = mutableListOf<String>()
                while (index < lines.size && !FENCE.matches(lines[index])) {
                    body.add(lines[index])
                    index++
                }
                index++
                val code = body.joinToString("\n")
                blocks.add(
                    buildJsonObject {
                        put("object", "block")
                        put("type", "code")
                        put(
                            "code",
                            buildJsonObject {
                                put("rich_text", JsonArray(splitText(code).map { textObject(it) }))
                                put("language", language)
                            },
                        )
                    },
                )
                continue
            }

            index++
            if (stripped.isEmpty()) continue

            if (RULE.matches(line)) {
                blocks.add(
                    buildJsonObject {
                        put("object", "block")
                        put("type", "divider")
                        put("divider", JsonObject(emptyMap()))
                    },
                )
                continue
            }

            val checkbox = CHECKBOX.matchEntire(line)
            if (checkbox != null) {
                val done = checkbox.groupValues[1].lowercase() == "x"
                blocks.add(block("to_do", checkbox.groupValues[2], mapOf("checked" to JsonPrimitive(done))))
                continue
            }

            val heading = HEADING.matchEntire(stripped)
            if (heading != null) {
                val level = minOf(heading.groupValues[1].length, 3)
                blocks.add(block("heading_$level", heading.groupValues[2]))
                continue
            }

            val quote = QUOTE.matchEntire(stripped)
            if (quote != null) {
                blocks.add(block("quote", quote.groupValues[1]))
                continue
            }

            val bullet = BULLET.matchEntire(line)
            if (bullet != null) {
                blocks.add(block("bulleted_list_item", bullet.groupValues[1]))
                continue
            }

            val numbered = NUMBERED.matchEntire(line)
            if (numbered != null) {
                blocks.add(block("numbered_list_item", numbered.groupValues[1]))
                continue
            }

            blocks.add(block("paragraph", stripped))
        }
        return blocks
    }

    /** Wraps a transcript in a collapsed toggle. */
    fun transcriptToggle(transcript: String, label: String = "Transcript"): JsonObject {
        val paragraphs = transcript.replace("\r\n", "\n").split("\n\n").map { it.trim() }.filter { it.isNotEmpty() }
        val packed = packParagraphs(paragraphs)
        return buildJsonObject {
            put("object", "block")
            put("type", "toggle")
            put(
                "toggle",
                buildJsonObject {
                    put("rich_text", JsonArray(richText(label)))
                    put(
                        "children",
                        buildJsonArray { packed.take(MAX_BLOCKS_PER_REQUEST).forEach { add(paragraph(it)) } },
                    )
                },
            )
        }
    }

    private fun packParagraphs(paragraphs: List<String>): List<String> {
        val packed = mutableListOf<String>()
        var current = ""
        for (para in paragraphs) {
            val candidate = if (current.isNotEmpty()) "$current\n\n$para" else para
            if (candidate.length > MAX_TEXT_LENGTH && current.isNotEmpty()) {
                packed.add(current)
                current = para
            } else {
                current = candidate
            }
        }
        if (current.isNotEmpty()) packed.add(current)
        return packed
    }

    /** Splits blocks into batches of 100, the most Notion accepts per append. */
    fun chunkBlocks(blocks: List<JsonObject>): List<List<JsonObject>> =
        blocks.chunked(MAX_BLOCKS_PER_REQUEST)
}
