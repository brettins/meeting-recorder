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
internal fun readMeetingJson(dir: File): JsonObject {
    val file = File(dir, "meeting.json")
    if (!file.exists()) return JsonObject(emptyMap())
    return try {
        lenientJson.parseToJsonElement(file.readText()).jsonObject
    } catch (_: Exception) {
        JsonObject(emptyMap())
    }
}

/** Writes [changes] into meeting.json in [dir]; a null value removes its key, and every other key is kept. */
internal fun updateMeetingJson(dir: File, changes: Map<String, JsonElement?>) {
    val merged = readMeetingJson(dir).toMutableMap()
    for ((key, value) in changes) {
        if (value == null) merged.remove(key) else merged[key] = value
    }
    File(dir, "meeting.json").writeText(JsonObject(merged).toString())
}
