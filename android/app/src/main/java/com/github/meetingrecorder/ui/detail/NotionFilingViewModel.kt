package com.github.meetingrecorder.ui.detail

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.github.meetingrecorder.data.Config
import com.github.meetingrecorder.data.MeetingMeta
import com.github.meetingrecorder.data.MeetingRepository
import com.github.meetingrecorder.data.NotionFilingMarker
import com.github.meetingrecorder.data.notion.FilingResult
import com.github.meetingrecorder.data.notion.NotionApi
import com.github.meetingrecorder.data.notion.NotionClient
import com.github.meetingrecorder.data.notion.NotionError
import com.github.meetingrecorder.data.notion.NotionFiling
import com.github.meetingrecorder.data.notion.NotionPage
import com.github.meetingrecorder.data.notion.NotionPicker
import com.github.meetingrecorder.data.notion.NotionTokenStore
import com.github.meetingrecorder.data.notion.NotionWorkspace
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.time.Instant
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

sealed interface PagesState {
    data object Idle : PagesState
    data object Loading : PagesState
    data class Loaded(val pages: List<NotionPage>, val dataSourceId: String, val titleProperty: String) : PagesState
    data class Failed(val error: NotionError) : PagesState
}

sealed interface FilingState {
    data object Idle : FilingState
    data class Working(val progress: NotionFiling.Progress) : FilingState
    data class Done(val result: FilingResult) : FilingState
    data class Failed(val error: NotionError) : FilingState
}

data class NotionFilingUi(
    val workspaces: List<NotionWorkspace> = emptyList(),
    val selected: NotionWorkspace? = null,
    val pages: PagesState = PagesState.Idle,
    val filing: FilingState = FilingState.Idle,
    val suggestedTitle: String = "",
    val recorded: LocalDateTime? = null,
    val audioBytes: Long = 0,
)

/** Drives the "File into Notion" dialog: loads pages, files a meeting, records the marker. */
class NotionFilingViewModel(
    private val config: Config,
    private val tokens: NotionTokenStore,
    private val repository: MeetingRepository,
    private val clientFactory: (String) -> NotionClient = { NotionClient(it) },
) : ViewModel() {

    private val _ui = MutableStateFlow(NotionFilingUi())
    val ui: StateFlow<NotionFilingUi> = _ui.asStateFlow()

    private var meetingDir: File? = null
    private var loadJob: Job? = null

    fun open(meetingPath: String) {
        val dir = File(meetingPath)
        meetingDir = dir
        val workspaces = config.notionWorkspaces
        viewModelScope.launch {
            val (title, audioBytes) = withContext(Dispatchers.IO) {
                val meta = File(dir, "meeting.json").takeIf { it.exists() }?.let {
                    try {
                        MeetingMeta.parse(it.readText())
                    } catch (_: Exception) {
                        null
                    }
                }
                meta?.title to (audioFile(dir)?.length() ?: 0L)
            }
            val recorded = parseRecorded(dir.name)
            _ui.value = NotionFilingUi(
                workspaces = workspaces,
                selected = workspaces.firstOrNull(),
                suggestedTitle = recorded?.let { NotionPicker.defaultNewTitle(title, it) } ?: title.orEmpty(),
                recorded = recorded,
                audioBytes = audioBytes,
            )
            loadPages()
        }
    }

    fun selectWorkspace(workspace: NotionWorkspace) {
        _ui.update { it.copy(selected = workspace, pages = PagesState.Idle, filing = FilingState.Idle) }
        loadPages()
    }

    fun loadPages() {
        val workspace = _ui.value.selected ?: return
        loadJob?.cancel()
        _ui.update { it.copy(pages = PagesState.Loading) }
        loadJob = viewModelScope.launch {
            try {
                val client = clientFor(workspace)
                val dataSourceId = client.resolveDataSource(workspace.databaseId)
                val schema = client.dataSourceSchema(dataSourceId)
                val pages = client.recentPages(dataSourceId)
                _ui.update { it.copy(pages = PagesState.Loaded(pages, dataSourceId, NotionApi.titlePropertyName(schema))) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: NotionError) {
                _ui.update { it.copy(pages = PagesState.Failed(e)) }
            }
        }
    }

    /** Files into [page], or creates a page called [newTitle] when [page] is null. */
    fun file(page: NotionPage?, newTitle: String) {
        val state = _ui.value
        val workspace = state.selected ?: return
        val loaded = state.pages as? PagesState.Loaded ?: return
        val dir = meetingDir ?: return
        if (state.filing is FilingState.Working) return
        if (page == null && newTitle.isBlank()) return
        _ui.update { it.copy(filing = FilingState.Working(NotionFiling.Progress.LOADING_PAGES)) }
        viewModelScope.launch {
            try {
                val client = clientFor(workspace)
                val target = page ?: client.createPage(loaded.dataSourceId, newTitle.trim(), loaded.titleProperty)
                val result = NotionFiling.fileMeeting(
                    client = client,
                    page = target,
                    notesFile = File(dir, "notes.md"),
                    transcriptFile = File(dir, "transcript.md"),
                    audioFile = audioFile(dir),
                    filesProperty = workspace.filesProperty,
                ) { progress -> _ui.update { it.copy(filing = FilingState.Working(progress)) } }
                if (result.notesWritten || result.transcriptWritten || result.audioAttached) {
                    withContext(Dispatchers.IO) {
                        repository.recordNotionFiling(
                            dir,
                            NotionFilingMarker(
                                workspace = workspace.name,
                                pageId = result.page.id,
                                pageUrl = result.page.url,
                                filedAt = DateTimeFormatter.ISO_INSTANT.format(Instant.now()),
                                notes = result.notesWritten,
                                transcript = result.transcriptWritten,
                                audio = result.audioAttached,
                            ),
                        )
                    }
                }
                _ui.update { it.copy(filing = FilingState.Done(result)) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: NotionError) {
                _ui.update { it.copy(filing = FilingState.Failed(e)) }
            }
        }
    }

    fun resetFiling() {
        _ui.update { it.copy(filing = FilingState.Idle) }
    }

    private fun clientFor(workspace: NotionWorkspace): NotionClient {
        val token = tokens.get(workspace.id)
        if (token.isBlank()) {
            throw NotionError(
                NotionError.Kind.NO_TOKEN,
                "No integration token is stored for ${workspace.name}. Add it in Settings.",
                arg = workspace.name,
            )
        }
        return clientFactory(token)
    }

    private fun audioFile(dir: File): File? =
        sequenceOf("recording.m4a", "recording.mp3").map { File(dir, it) }.firstOrNull { it.exists() }

    private fun parseRecorded(name: String): LocalDateTime? = try {
        LocalDateTime.parse(name.take(16), DateTimeFormatter.ofPattern("yyyy-MM-dd_HH-mm"))
    } catch (_: Exception) {
        null
    }
}
