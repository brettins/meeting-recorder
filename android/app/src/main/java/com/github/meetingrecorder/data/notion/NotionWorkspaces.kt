package com.github.meetingrecorder.data.notion

import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.jsonArray
import java.time.LocalDate
import java.time.LocalDateTime

/** One Notion workspace a meeting can be filed into; the token lives in a [NotionTokenStore]. */
@Serializable
data class NotionWorkspace(
    val id: String,
    val name: String,
    val databaseId: String,
    val filesProperty: String = "",
)

object NotionWorkspaces {
    private val HEX32 = Regex("[0-9a-fA-F]{32}")
    private val UUID = Regex("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
    private val ID_UNSAFE = Regex("[^a-z0-9]+")

    /** Pulls a database id out of a pasted Notion URL, or returns the text unchanged. */
    fun extractDatabaseId(text: String): String {
        val candidate = text.trim()
        if (candidate.isEmpty()) return ""
        val segment = candidate.substringBefore("?").trimEnd('/').substringAfterLast("/")
        UUID.find(segment)?.let { return it.value.replace("-", "") }
        return HEX32.findAll(segment).lastOrNull()?.value ?: candidate
    }

    /** Derives a stable id from [name], suffixed when it collides with [taken]. */
    fun idFor(name: String, taken: Collection<String>): String {
        val base = ID_UNSAFE.replace(name.trim().lowercase(), "-").trim('-').ifEmpty { "workspace" }
        if (base !in taken) return base
        var index = 2
        while ("$base-$index" in taken) index++
        return "$base-$index"
    }

    fun add(
        workspaces: List<NotionWorkspace>,
        name: String,
        databaseLink: String,
        filesProperty: String,
    ): List<NotionWorkspace> = workspaces + NotionWorkspace(
        id = idFor(name, workspaces.map { it.id }),
        name = name.trim(),
        databaseId = extractDatabaseId(databaseLink),
        filesProperty = filesProperty.trim(),
    )

    fun update(
        workspaces: List<NotionWorkspace>,
        id: String,
        name: String,
        databaseLink: String,
        filesProperty: String,
    ): List<NotionWorkspace> = workspaces.map {
        if (it.id != id) {
            it
        } else {
            it.copy(
                name = name.trim().ifEmpty { it.name },
                databaseId = databaseLink.takeIf { link -> link.isNotBlank() }?.let(::extractDatabaseId) ?: it.databaseId,
                filesProperty = filesProperty.trim(),
            )
        }
    }

    fun remove(workspaces: List<NotionWorkspace>, id: String): List<NotionWorkspace> = workspaces.filter { it.id != id }

    fun isDatabaseLink(link: String): Boolean = HEX32.matches(extractDatabaseId(link))

    fun isUsable(name: String, databaseLink: String): Boolean = name.isNotBlank() && isDatabaseLink(databaseLink)

    fun encode(workspaces: List<NotionWorkspace>): String =
        lenientNotionJson.encodeToString(ListSerializer(NotionWorkspace.serializer()), workspaces)

    /** Decodes stored workspaces, dropping unusable entries and duplicate ids; malformed text yields none. */
    fun decode(text: String): List<NotionWorkspace> {
        val parsed = try {
            lenientNotionJson.parseToJsonElement(text).jsonArray.mapNotNull { entry ->
                try {
                    lenientNotionJson.decodeFromJsonElement(NotionWorkspace.serializer(), entry)
                } catch (_: Exception) {
                    null
                }
            }
        } catch (_: Exception) {
            return emptyList()
        }
        val seen = mutableSetOf<String>()
        return parsed.filter { it.name.isNotBlank() && it.databaseId.isNotBlank() && seen.add(it.id) }
    }
}

/** Page-picker decisions for the filing dialog. */
object NotionPicker {
    private fun normalise(text: String) = text.lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")

    /** Pages whose title or date contains every word of [query]. */
    fun filterPages(pages: List<NotionPage>, query: String): List<NotionPage> {
        val words = normalise(query).split(" ").filter { it.isNotEmpty() }
        if (words.isEmpty()) return pages
        return pages.filter { page ->
            val haystack = normalise("${page.title} ${page.date.orEmpty()}")
            words.all { it in haystack }
        }
    }

    /** The page dated the same day as [recorded], or null. */
    fun suggestPage(pages: List<NotionPage>, recorded: LocalDateTime): NotionPage? {
        val day = recorded.toLocalDate().toString()
        return pages.firstOrNull { it.date == day }
    }

    fun defaultNewTitle(meetingTitle: String?, recorded: LocalDateTime): String {
        val stem = meetingTitle?.trim().orEmpty()
        if (stem.isNotEmpty()) return stem
        val day: LocalDate = recorded.toLocalDate()
        return "Meeting $day %02d:%02d".format(recorded.hour, recorded.minute)
    }
}
