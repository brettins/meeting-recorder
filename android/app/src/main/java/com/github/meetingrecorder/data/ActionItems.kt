package com.github.meetingrecorder.data

/** Action lines pulled out of a meeting's notes.md, to become draft tasks. */
object ActionItems {

    private val HEADINGS = setOf(
        "action items",
        "action item",
        "action points",
        "actions",
        "next steps",
        "follow-ups",
        "follow ups",
        "follow-up",
        "to-dos",
        "todos",
        "to do",
    )

    const val MAX_LENGTH = 200

    // A bold-only or colon line has no level of its own, so it ranks below every # heading.
    private const val PLAIN_HEADING_LEVEL = 7

    private val ATX = Regex("""^(#{1,6})\s+(.*?)\s*#*\s*$""")
    private val BOLD_HEADING = Regex("""^(?:\*\*|__)(.+?)(?:\*\*|__)\s*:?\s*$""")
    private val ITEM = Regex("""^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$""")
    private val CHECKBOX = Regex("""^\[([ xX])\]\s*(.*)$""")
    private val LINK = Regex("""\[([^\]]+)\]\([^)]*\)""")
    private val EMPHASIS = Regex("""(\*\*|__|\*|`)""")
    private val WHITESPACE = Regex("""\s+""")

    /** Markdown emphasis, code ticks and link targets stripped; spaces collapsed. */
    fun clean(text: String): String {
        val plain = EMPHASIS.replace(LINK.replace(text, "$1"), "")
        return plain.trim().split(WHITESPACE).filter { it.isNotEmpty() }.joinToString(" ")
    }

    /** Open action lines: items under an action heading, and unchecked boxes anywhere. */
    fun extract(notes: String): List<String> {
        val found = mutableListOf<String>()
        var sectionLevel: Int? = null
        var itemIndent: Int? = null

        for (line in notes.lines()) {
            val heading = heading(line)
            if (heading != null) {
                val (level, text) = heading
                if (isActionHeading(text)) {
                    sectionLevel = level
                    itemIndent = null
                } else if (sectionLevel != null && level <= sectionLevel) {
                    sectionLevel = null
                }
                continue
            }

            val item = ITEM.find(line) ?: continue
            val indent = indentWidth(item.groupValues[1])
            var body = item.groupValues[2].trim()
            val box = CHECKBOX.find(body)
            if (box != null) {
                if (box.groupValues[1] != " ") continue
                body = box.groupValues[2]
            } else if (sectionLevel == null) {
                continue
            }

            if (sectionLevel != null) {
                if (itemIndent == null) itemIndent = indent
                if (indent > itemIndent) continue
            } else if (indent > 0) {
                continue
            }

            val text = clean(body).take(MAX_LENGTH).trim()
            if (text.isNotEmpty() && found.none { it.equals(text, ignoreCase = true) }) {
                found.add(text)
            }
        }
        return found
    }

    private fun heading(line: String): Pair<Int, String>? {
        val stripped = line.trim()
        ATX.find(stripped)?.let { return it.groupValues[1].length to it.groupValues[2] }
        BOLD_HEADING.find(stripped)?.let { return PLAIN_HEADING_LEVEL to it.groupValues[1] }
        if (stripped.endsWith(":") && stripped.length <= 60 && ITEM.find(line) == null) {
            return PLAIN_HEADING_LEVEL to stripped
        }
        return null
    }

    private fun isActionHeading(text: String): Boolean =
        clean(text).lowercase().trimEnd(':').trim() in HEADINGS

    private fun indentWidth(indent: String): Int {
        var width = 0
        for (c in indent) width = if (c == '\t') (width / 4 + 1) * 4 else width + 1
        return width
    }
}
