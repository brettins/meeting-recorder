package com.github.meetingrecorder.data

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.time.LocalDateTime
import java.util.concurrent.ConcurrentHashMap
import java.util.logging.Logger

class MeetingRepository(
    private val rootDir: File,
    private val logWarn: (String) -> Unit = {},
) : MeetingStore {

    private val logger = Logger.getLogger("MeetingRepository")

    // Captured when the repository (a per-process singleton) is constructed — effectively app launch.
    // recoverOrphanedRecordings() only acts on directories locked *before* this moment, so a recording
    // the user starts during this session (its `.recording` lock is newer) is never mistaken for an
    // orphan and deleted out from under the active recorder.
    private val sessionStartTime = System.currentTimeMillis()

    fun listMeetings(): List<Meeting> {
        val meetings = mutableListOf<Meeting>()
        if (!rootDir.exists()) return meetings

        // Flat structure: BASE_DIR/YYYY-MM-DD_HH-MM[_title]/
        val pattern = Regex("""^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})(?:_.*)?$""")
        rootDir.listFiles()?.forEach { meetingDir ->
            if (!meetingDir.isDirectory) return@forEach
            if (File(meetingDir, ".recording").exists()) return@forEach

            val match = pattern.matchEntire(meetingDir.name) ?: return@forEach
            val date = try {
                LocalDateTime.of(
                    match.groupValues[1].toInt(),
                    match.groupValues[2].toInt(),
                    match.groupValues[3].toInt(),
                    match.groupValues[4].toInt(),
                    match.groupValues[5].toInt(),
                )
            } catch (_: Exception) {
                return@forEach
            }

            var title: String? = null
            var durationSeconds: Int? = null
            val metaFile = File(meetingDir, "meeting.json")
            if (metaFile.exists()) {
                try {
                    val meta = MeetingMeta.parse(metaFile.readText())
                    title = meta.title
                    durationSeconds = meta.durationSeconds
                } catch (_: Exception) {
                    // Malformed meeting.json: keep the meeting visible, just without metadata.
                }
            }

            meetings.add(
                Meeting(
                    path = meetingDir,
                    timeLabel = meetingDir.name,
                    date = date,
                    title = title,
                    hasNotes = File(meetingDir, "notes.md").exists(),
                    hasTranscript = File(meetingDir, "transcript.md").exists(),
                    hasAudio = File(meetingDir, "recording.m4a").exists(),
                    durationSeconds = durationSeconds,
                ),
            )
        }

        return meetings.sortedByDescending { it.date }
    }

    /**
     * Recovers meeting directories left in a "recording in progress" state — i.e. still holding a
     * `.recording` lock file — by a previous process that died before it could finish: the app was
     * killed mid-recording, or processing failed and the directory was never de-orphaned. Such
     * directories are hidden from [listMeetings] even though their audio is on disk.
     *
     * Only directories locked before [sessionStartTime] are considered, so an in-progress recording
     * from the current session is never touched. For each such locked directory whose name matches
     * the meeting pattern:
     *  - if it holds a non-empty `recording.m4a`, the lock is removed so the audio becomes visible
     *    in the library (the user can re-transcribe later via "Use Existing Recording");
     *  - otherwise (no usable audio) the lock is still removed if the directory holds other real
     *    content (e.g. notes/transcript written before a crash), so that content isn't hidden
     *    forever; if there is nothing worth keeping, the dead stub is deleted.
     *
     * Safe to call when storage isn't readable yet — [listFiles] returns null and this no-ops.
     */
    fun recoverOrphanedRecordings() {
        if (!rootDir.exists()) return
        val pattern = Regex("""^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})(?:_.*)?$""")
        rootDir.listFiles()?.forEach { dir ->
            if (!dir.isDirectory) return@forEach
            val lock = File(dir, ".recording")
            // Skip dirs with no lock, and any locked during this session — that lock belongs to a
            // recording the user just started, whose audio may not be on disk yet.
            if (!lock.exists() || lock.lastModified() >= sessionStartTime) return@forEach
            if (!pattern.matches(dir.name)) return@forEach

            val audio = File(dir, "recording.m4a")
            if (audio.exists() && audio.length() > 0L) {
                lock.delete()
            } else {
                // No usable audio (missing or 0 bytes). Unlock if the dir still holds real user
                // content so it isn't hidden forever; otherwise it's a dead stub — delete it. A
                // 0-byte recording.m4a and the meeting.json metadata stub don't count as content.
                val hasValuableContent = dir.listFiles()?.any {
                    it.name != ".recording" && it.name != "recording.m4a" &&
                        it.name != "meeting.json" && it.length() > 0L
                } ?: false
                if (hasValuableContent) lock.delete() else dir.deleteRecursively()
            }
        }
    }

    fun createMeetingDir(title: String?): File {
        val now = LocalDateTime.now()
        val datePart = "${now.year.toString().padStart(4, '0')}-${now.monthValue.toString().padStart(2, '0')}-${now.dayOfMonth.toString().padStart(2, '0')}"
        val timePart = "${now.hour.toString().padStart(2, '0')}-${now.minute.toString().padStart(2, '0')}"

        val folderName = if (title != null) {
            val sanitized = title.replace(Regex("[^a-zA-Z0-9_\\-]"), "_").take(30)
            "${datePart}_${timePart}_$sanitized"
        } else {
            "${datePart}_$timePart"
        }

        // Two recordings started in the same minute with the same title must not
        // share a folder: the second would write over the first one's audio.
        var dir = File(rootDir, folderName)
        var n = 2
        while (dir.exists() && !dir.listFiles().isNullOrEmpty()) {
            dir = File(rootDir, "${folderName}_$n")
            n++
        }
        dir.mkdirs()
        return dir
    }

    override fun saveMeetingMeta(dir: File, title: String?, durationSeconds: Int?) {
        updateMeta(dir, throwIfUnreadable = false) { meta ->
            val merged = meta.toMutableMap()
            if (title != null) merged["title"] = JsonPrimitive(title) else merged.remove("title")
            if (durationSeconds != null) merged["duration_seconds"] = JsonPrimitive(durationSeconds) else merged.remove("duration_seconds")
            JsonObject(merged)
        }
    }

    /** Records that a meeting was filed to a Notion page in meeting.json; throws when the file exists but cannot be read. */
    fun recordNotionFiling(dir: File, filing: NotionFilingMarker) {
        updateMeta(dir, throwIfUnreadable = true) { NotionFilingMarker.apply(it, filing) }
    }

    private val metaLocks = ConcurrentHashMap<String, Any>()

    private fun updateMeta(dir: File, throwIfUnreadable: Boolean, transform: (Map<String, JsonElement>) -> JsonObject) {
        val metaFile = File(dir, "meeting.json")
        synchronized(metaLocks.getOrPut(dir.absolutePath) { Any() }) {
            val current = readMetaTree(metaFile)
            if (current == null) {
                logger.warning("meeting.json in ${dir.name} is unreadable; leaving it untouched")
                if (throwIfUnreadable) throw IOException("meeting.json in ${dir.name} is unreadable")
                return
            }
            writeMetaAtomically(metaFile, transform(current).toString())
        }
    }

    private fun writeMetaAtomically(metaFile: File, text: String) {
        val temp = File.createTempFile("meeting", ".tmp", metaFile.parentFile)
        try {
            FileOutputStream(temp).use {
                it.write(text.toByteArray())
                it.fd.sync()
            }
            Files.move(temp.toPath(), metaFile.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        } finally {
            temp.delete()
        }
    }

    /** Parsed meeting.json (empty when absent), or null when it exists but is not a JSON object. */
    private fun readMetaTree(metaFile: File): Map<String, JsonElement>? {
        if (!metaFile.exists()) return emptyMap()
        return try {
            lenientJson.parseToJsonElement(metaFile.readText()) as? JsonObject
        } catch (_: Exception) {
            null
        }
    }

    /**
     * Renames a meeting: updates the title in meeting.json and renames the folder
     * to reflect the new title suffix. Returns the (possibly new) directory.
     */
    fun renameMeeting(dir: File, newTitle: String?): File {
        val cleanTitle = newTitle?.trim()?.ifBlank { null }
        // Folder name format: "YYYY-MM-DD_HH-MM" (first 16 chars) + optional "_title"
        val timePart = dir.name.take(16)
        val sanitized = cleanTitle?.replace(Regex("[^a-zA-Z0-9_\\-]"), "_")?.take(30)
        val newName = if (sanitized == null) timePart else "${timePart}_$sanitized"

        val newDir = File(rootDir, newName)
        if (newDir.absolutePath != dir.absolutePath) {
            dir.renameTo(newDir)
        }

        // Edits the raw JSON tree, not MeetingMeta, so duration_seconds and Linux-written keys survive.
        keptOrSaid(newDir, updateMeetingJson(newDir, mapOf("title" to cleanTitle?.let { JsonPrimitive(it) })))

        return newDir
    }

    fun deleteMeeting(dir: File) {
        if (dir.canonicalPath.startsWith(rootDir.canonicalPath + File.separator)) {
            dir.deleteRecursively()
        }
    }

    /**
     * Returns the meeting directory that directly contains [file], or null if the file
     * is not inside a recognised meeting directory under [rootDir].
     */
    fun meetingDirContaining(file: File): File? {
        val parent = file.parentFile ?: return null
        val pattern = Regex("""^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})(?:_.*)?$""")
        if (!pattern.matches(parent.name)) return null
        return try {
            if (parent.parentFile?.canonicalPath == rootDir.canonicalPath) parent else null
        } catch (_: Exception) {
            null
        }
    }

    private fun keptOrSaid(dir: File, written: Boolean) {
        if (!written) logWarn("meeting.json in ${dir.name} could not be read, so it was left as it was")
    }
}
