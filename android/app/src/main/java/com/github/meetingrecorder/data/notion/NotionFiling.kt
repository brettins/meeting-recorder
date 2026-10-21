package com.github.meetingrecorder.data.notion

import java.io.File

/** Why part of a meeting did not reach Notion. */
sealed interface FilingWarning {
    data object NothingToFile : FilingWarning
    data object NoFilesProperty : FilingWarning
    data class AudioFailed(val error: NotionError) : FilingWarning
}

/** What reached Notion, and what did not. */
data class FilingResult(
    val page: NotionPage,
    val notesWritten: Boolean = false,
    val transcriptWritten: Boolean = false,
    val audioAttached: Boolean = false,
    val warnings: List<FilingWarning> = emptyList(),
)

object NotionFiling {
    private fun readText(file: File?): String {
        if (file == null || !file.exists()) return ""
        return try {
            file.readText()
        } catch (_: java.io.IOException) {
            ""
        }
    }

    /** Notes first, transcript folded into a toggle. */
    fun buildBody(notes: String, transcript: String, transcriptLabel: String = "Transcript") =
        buildList {
            if (notes.isNotBlank()) addAll(NotionBlocks.markdownToBlocks(notes))
            if (transcript.isNotBlank()) add(NotionBlocks.transcriptToggle(transcript, transcriptLabel))
        }

    /** Writes notes and transcript onto [page], then attaches the audio; an audio failure keeps the notes. */
    suspend fun fileMeeting(
        client: NotionClient,
        page: NotionPage,
        notesFile: File?,
        transcriptFile: File?,
        audioFile: File?,
        filesProperty: String,
        onProgress: (Progress) -> Unit = {},
    ): FilingResult {
        val notes = readText(notesFile)
        val transcript = readText(transcriptFile)
        val body = buildBody(notes, transcript)
        val warnings = mutableListOf<FilingWarning>()
        var notesWritten = false
        var transcriptWritten = false
        if (body.isNotEmpty()) {
            onProgress(Progress.WRITING_NOTES)
            client.appendBlocks(page.id, NotionBlocks.chunkBlocks(body))
            notesWritten = notes.isNotBlank()
            transcriptWritten = transcript.isNotBlank()
        } else {
            warnings.add(FilingWarning.NothingToFile)
        }

        var audioAttached = false
        if (audioFile != null && audioFile.exists()) {
            if (filesProperty.isBlank()) {
                warnings.add(FilingWarning.NoFilesProperty)
            } else {
                onProgress(Progress.UPLOADING_AUDIO)
                try {
                    val uploadId = client.uploadFile(audioFile)
                    client.attachFile(page.id, filesProperty, uploadId)
                    audioAttached = true
                } catch (e: NotionError) {
                    warnings.add(FilingWarning.AudioFailed(e))
                }
            }
        }
        return FilingResult(page, notesWritten, transcriptWritten, audioAttached, warnings)
    }

    enum class Progress { LOADING_PAGES, WRITING_NOTES, UPLOADING_AUDIO }
}
