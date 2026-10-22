package com.github.meetingrecorder.data

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import java.io.File
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/** Filing a finished meeting into Maestro: the payload, the meeting id, and the sentence afterwards. */
object MaestroFiling {

    const val ID_KEY = "dispatch_meeting_id"

    private val FOLDER_STAMP = Regex("""^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})""")
    private val MINUTE = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm", Locale.US)
    private val ISO_WITH_OFFSET = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mmxxx", Locale.US)
    private val TITLE_TIME = DateTimeFormatter.ofPattern("MMM dd, yyyy HH:mm", Locale.US)

    /** The minute the recording started, which a rename leaves alone. */
    fun meetingIdFor(folderName: String, date: LocalDateTime): String {
        val stamp = FOLDER_STAMP.find(folderName) ?: return date.format(MINUTE)
        val (year, month, day, hour, minute) = stamp.destructured
        return "$year-$month-${day}T$hour:$minute"
    }

    /** When the folder name says the recording started, or null for a folder not named that way. */
    fun startedAt(folderName: String): LocalDateTime? {
        val stamp = FOLDER_STAMP.find(folderName) ?: return null
        val (year, month, day, hour, minute) = stamp.destructured
        return try {
            LocalDateTime.of(year.toInt(), month.toInt(), day.toInt(), hour.toInt(), minute.toInt())
        } catch (_: Exception) {
            null
        }
    }

    /** [base], or [base] with the first counter no other meeting holds. */
    fun unusedId(base: String, taken: Set<String>): String {
        var candidate = base
        var n = 2
        while (candidate in taken) {
            candidate = "$base~$n"
            n++
        }
        return candidate
    }

    /** The id this meeting was filed under before, or one no meeting beside it holds. */
    fun stableMeetingId(dir: File, date: LocalDateTime): String {
        heldId(dir)?.let { return it }
        val siblings = dir.parentFile?.listFiles()
            ?.filter { it.isDirectory && it != dir }
            ?.mapNotNull { heldId(it) }
            ?.toSet()
            .orEmpty()
        return unusedId(meetingIdFor(dir.name, date), siblings)
    }

    /** Keeps [id] in meeting.json so a rename or a sibling cannot move it; every other key survives. */
    fun rememberId(dir: File, id: String): Boolean = updateMeetingJson(dir, mapOf(ID_KEY to JsonPrimitive(id)))

    private fun heldId(dir: File): String? =
        (readMeetingJson(dir)[ID_KEY] as? JsonPrimitive)?.contentOrNull?.ifBlank { null }

    /** The meeting's title, or its start time when it was never named. */
    fun meetingTitle(title: String?, date: LocalDateTime): String =
        title?.trim()?.ifEmpty { null } ?: "Meeting ${date.format(TITLE_TIME)}"

    /** [date] as ISO 8601 to the minute, carrying the offset [zone] had then. */
    fun isoDate(date: LocalDateTime, zone: ZoneId): String = date.atZone(zone).format(ISO_WITH_OFFSET)

    fun buildPayload(
        meetingId: String,
        title: String?,
        date: LocalDateTime,
        durationSeconds: Int?,
        notes: String,
        transcript: String,
        actions: List<String>,
        company: String = "",
        zone: ZoneId = ZoneId.systemDefault(),
    ): MaestroMeetingPayload = MaestroMeetingPayload(
        meetingId = meetingId,
        title = meetingTitle(title, date),
        date = isoDate(date, zone),
        durationSeconds = durationSeconds ?: 0,
        notes = notes,
        transcript = transcript,
        actions = actions.toList(),
        company = company.trim().ifEmpty { null },
    )

    /** One sentence for the notice after filing. */
    fun filedSummary(filed: FiledMeeting): String {
        val name = filed.key.ifEmpty { "the meeting" }
        val new = filed.newTasks
        val tasks = when (new) {
            0 -> "no draft tasks"
            1 -> "1 draft task"
            else -> "$new draft tasks"
        }
        return when {
            filed.created -> "Filed into Maestro as $name, with $tasks in Sort."
            new > 0 -> "Updated $name in Maestro, and added $tasks to Sort."
            filed.tasks.isNotEmpty() -> "Updated $name in Maestro. Its tasks are already there."
            else -> "Updated $name in Maestro."
        }
    }
}

/** Reads a meeting folder, files it, and keeps the id it was filed under. */
class MeetingFiling(
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
    private val logWarn: (String) -> Unit = {},
) {

    /** The action lines a filing would turn into draft tasks. */
    fun proposedActions(notes: String): List<String> = ActionItems.extract(notes)

    /** Files the meeting in [dir]. [actions] replaces the parsed lines when given. */
    suspend fun file(
        filer: MeetingFiler,
        dir: File,
        title: String?,
        durationSeconds: Int?,
        actions: List<String>? = null,
        company: String = "",
    ): FiledMeeting {
        val (payload, meetingId) = withContext(ioDispatcher) {
            val notes = readText(File(dir, "notes.md"))
            val transcript = readText(File(dir, "transcript.md"))
            val date = MaestroFiling.startedAt(dir.name)
                ?: LocalDateTime.ofInstant(Instant.ofEpochMilli(dir.lastModified()), ZoneId.systemDefault())
            val meetingId = MaestroFiling.stableMeetingId(dir, date)
            MaestroFiling.buildPayload(
                meetingId = meetingId,
                title = title,
                date = date,
                durationSeconds = durationSeconds,
                notes = notes,
                transcript = transcript,
                actions = actions ?: proposedActions(notes),
                company = company,
            ) to meetingId
        }
        val filed = filer.fileMeeting(payload)
        withContext(ioDispatcher) {
            try {
                if (!MaestroFiling.rememberId(dir, meetingId)) {
                    logWarn("Filed, but meeting.json could not be read, so the meeting id was not kept")
                }
            } catch (e: Exception) {
                logWarn("Filed, but could not keep the meeting id in meeting.json: ${e.javaClass.simpleName}")
            }
        }
        return filed
    }

    private fun readText(file: File): String = if (file.exists()) file.readText() else ""
}
