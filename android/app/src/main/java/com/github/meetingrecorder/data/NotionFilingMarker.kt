package com.github.meetingrecorder.data

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive

/** One entry of the `notion_filings` list in meeting.json. */
data class NotionFilingMarker(
    val workspace: String,
    val workspaceId: String,
    val pageId: String,
    val pageUrl: String,
    val filedAt: String,
    val notes: Boolean,
    val transcript: Boolean,
    val audio: Boolean,
) {
    fun toJson(): JsonObject = JsonObject(
        mapOf(
            "workspace" to JsonPrimitive(workspace),
            "workspace_id" to JsonPrimitive(workspaceId),
            "page_id" to JsonPrimitive(pageId),
            "page_url" to JsonPrimitive(pageUrl),
            "filed_at" to JsonPrimitive(filedAt),
            "notes" to JsonPrimitive(notes),
            "transcript" to JsonPrimitive(transcript),
            "audio" to JsonPrimitive(audio),
        ),
    )

    companion object {
        const val KEY = "notion_filings"

        /** Returns [meta] with [filing] added to the list, replacing an earlier entry for the same page. */
        fun apply(meta: Map<String, JsonElement>, filing: NotionFilingMarker): JsonObject {
            val existing = (meta[KEY] as? JsonArray).orEmpty().filter { entry ->
                (entry as? JsonObject)?.get("page_id")?.jsonPrimitive?.contentOrNull != filing.pageId
            }
            return JsonObject(meta + (KEY to JsonArray(existing + filing.toJson())))
        }
    }
}
