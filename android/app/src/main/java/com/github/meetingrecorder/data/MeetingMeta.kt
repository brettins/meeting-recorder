package com.github.meetingrecorder.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import java.io.File

/**
 * Shared lenient JSON codec for all meeting.json and Gemini wire handling. Unknown keys are
 * ignored and primitives are coerced where possible, matching the tolerance of the org.json
 * parser this codebase used before migrating to kotlinx.serialization. Defaults are not encoded
 * (Json's default), so null/absent fields are omitted from output — same as org.json's
 * put-only-what-you-have behavior.
 */
internal val lenientJson = Json {
    ignoreUnknownKeys = true
    isLenient = true
}

/**
 * The meeting.json metadata schema. Field names are the exact on-disk format shared with the
 * Linux app (snake_case `duration_seconds`) — do not rename them.
 */
@Serializable
data class MeetingMeta(
    val title: String? = null,
    @SerialName("duration_seconds") val durationSeconds: Int? = null,
) {
    companion object {
        /**
         * Parses meeting.json text. Blank titles normalize to null (matching the historical
         * `optString("title").ifBlank { null }` behavior). Throws on malformed JSON — callers
         * keep their own try/catch so broken metadata is skipped, never fatal.
         */
        fun parse(text: String): MeetingMeta {
            val meta = lenientJson.decodeFromString(serializer(), text)
            return meta.copy(title = meta.title?.ifBlank { null })
        }
    }
}

/** meeting.json in [dir] as a raw object; missing or malformed reads as empty. */
internal fun readMeetingJson(dir: File): JsonObject = readMeetingJsonChecked(dir) ?: JsonObject(emptyMap())

/** meeting.json in [dir]: empty when there is none, null when one exists and cannot be read. */
internal fun readMeetingJsonChecked(dir: File): JsonObject? {
    val file = File(dir, "meeting.json")
    if (!file.exists()) return JsonObject(emptyMap())
    return try {
        lenientJson.parseToJsonElement(file.readText()).jsonObject
    } catch (_: Exception) {
        null
    }
}

/**
 * Writes [changes] into meeting.json in [dir]; a null value removes its key, and every other key is kept.
 * Returns false, writing nothing, when an existing meeting.json cannot be read.
 */
internal fun updateMeetingJson(dir: File, changes: Map<String, JsonElement?>): Boolean {
    // Merging onto a failed read would replace the tags, title and filing id
    // with only the keys being written. The Linux app refuses for the same reason.
    val merged = readMeetingJsonChecked(dir)?.toMutableMap() ?: return false
    for ((key, value) in changes) {
        if (value == null) merged.remove(key) else merged[key] = value
    }
    // Written beside and renamed over, so a process killed mid-write leaves the old file whole.
    val tmp = File(dir, "meeting.json.tmp")
    tmp.writeText(JsonObject(merged).toString())
    if (!tmp.renameTo(File(dir, "meeting.json"))) {
        tmp.delete()
        return false
    }
    return true
}
