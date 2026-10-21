package com.github.meetingrecorder.ui.settings

import androidx.lifecycle.ViewModel
import com.github.meetingrecorder.data.AudioQuality
import com.github.meetingrecorder.data.Config
import com.github.meetingrecorder.data.notion.NotionTokenStore
import com.github.meetingrecorder.data.notion.NotionWorkspace
import com.github.meetingrecorder.data.notion.NotionWorkspaces
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

// Plain ViewModel — no Context needed; Config and the token store are constructor-injected via appViewModelFactory.
class SettingsViewModel(
    private val config: Config,
    private val tokens: NotionTokenStore,
) : ViewModel() {

    private val _apiKey = MutableStateFlow(config.apiKey)
    val apiKey: StateFlow<String> = _apiKey.asStateFlow()

    private val _model = MutableStateFlow(config.model)
    val model: StateFlow<String> = _model.asStateFlow()

    val availableModels: List<String> = Config.AVAILABLE_MODELS

    private val _audioQuality = MutableStateFlow(config.audioQuality)
    val audioQuality: StateFlow<AudioQuality> = _audioQuality.asStateFlow()

    val availableQualities: List<AudioQuality> = AudioQuality.entries

    private val _transcriptionPrompt = MutableStateFlow(config.transcriptionPrompt)
    val transcriptionPrompt: StateFlow<String> = _transcriptionPrompt.asStateFlow()

    private val _summarizationPrompt = MutableStateFlow(config.summarizationPrompt)
    val summarizationPrompt: StateFlow<String> = _summarizationPrompt.asStateFlow()

    private val _titlePrompt = MutableStateFlow(config.titlePrompt)
    val titlePrompt: StateFlow<String> = _titlePrompt.asStateFlow()

    private val _processingCountdownEnabled = MutableStateFlow(config.processingCountdownEnabled)
    val processingCountdownEnabled: StateFlow<Boolean> = _processingCountdownEnabled.asStateFlow()

    private val _dndDuringRecordingEnabled = MutableStateFlow(config.dndDuringRecordingEnabled)
    val dndDuringRecordingEnabled: StateFlow<Boolean> = _dndDuringRecordingEnabled.asStateFlow()

    fun setApiKey(key: String) {
        _apiKey.value = key
        config.apiKey = key
    }

    fun setModel(model: String) {
        _model.value = model
        config.model = model
    }

    fun setAudioQuality(quality: AudioQuality) {
        _audioQuality.value = quality
        config.audioQuality = quality
    }

    fun setTranscriptionPrompt(value: String) {
        _transcriptionPrompt.value = value
        config.transcriptionPrompt = value
    }

    fun setSummarizationPrompt(value: String) {
        _summarizationPrompt.value = value
        config.summarizationPrompt = value
    }

    fun setTitlePrompt(value: String) {
        _titlePrompt.value = value
        config.titlePrompt = value
    }

    fun setProcessingCountdownEnabled(enabled: Boolean) {
        _processingCountdownEnabled.value = enabled
        config.processingCountdownEnabled = enabled
    }

    fun setDndDuringRecordingEnabled(enabled: Boolean) {
        _dndDuringRecordingEnabled.value = enabled
        config.dndDuringRecordingEnabled = enabled
    }

    private val _notionWorkspaces = MutableStateFlow(config.notionWorkspaces)
    val notionWorkspaces: StateFlow<List<NotionWorkspace>> = _notionWorkspaces.asStateFlow()

    fun hasNotionToken(workspaceId: String): Boolean = tokens.get(workspaceId).isNotBlank()

    fun addNotionWorkspace(name: String, databaseLink: String, token: String, filesProperty: String) {
        val updated = NotionWorkspaces.add(_notionWorkspaces.value, name, databaseLink, filesProperty)
        tokens.set(updated.last().id, token)
        saveNotionWorkspaces(updated)
    }

    /** Edits a workspace; a blank [token] keeps the stored one. */
    fun updateNotionWorkspace(id: String, name: String, databaseLink: String, token: String, filesProperty: String) {
        if (token.isNotBlank()) tokens.set(id, token)
        saveNotionWorkspaces(NotionWorkspaces.update(_notionWorkspaces.value, id, name, databaseLink, filesProperty))
    }

    fun removeNotionWorkspace(id: String) {
        tokens.remove(id)
        saveNotionWorkspaces(NotionWorkspaces.remove(_notionWorkspaces.value, id))
    }

    private fun saveNotionWorkspaces(workspaces: List<NotionWorkspace>) {
        _notionWorkspaces.value = workspaces
        config.notionWorkspaces = workspaces
    }
}
