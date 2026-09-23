package com.github.meetingrecorder.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.github.meetingrecorder.data.AudioQuality
import com.github.meetingrecorder.data.Config
import com.github.meetingrecorder.data.DispatchConfig
import com.github.meetingrecorder.data.DispatchTokenStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** What the Dispatch tab says after Save. */
sealed interface DispatchNotice {
    data object Saved : DispatchNotice
    data object TokenNotStored : DispatchNotice
    data class Problem(val text: String) : DispatchNotice
}

// Plain ViewModel — no Context needed; Config is constructor-injected via appViewModelFactory.
class SettingsViewModel(
    private val config: Config,
    private val dispatchTokens: DispatchTokenStore,
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

    private val _dispatchUrl = MutableStateFlow(config.dispatchUrl)
    val dispatchUrl: StateFlow<String> = _dispatchUrl.asStateFlow()

    private val _dispatchCompany = MutableStateFlow(config.dispatchCompany)
    val dispatchCompany: StateFlow<String> = _dispatchCompany.asStateFlow()

    private val _hasDispatchToken = MutableStateFlow(false)
    val hasDispatchToken: StateFlow<Boolean> = _hasDispatchToken.asStateFlow()

    private val _dispatchNotice = MutableStateFlow<DispatchNotice?>(null)
    val dispatchNotice: StateFlow<DispatchNotice?> = _dispatchNotice.asStateFlow()

    init {
        viewModelScope.launch {
            _hasDispatchToken.value = withContext(Dispatchers.IO) { dispatchTokens.get().isNotEmpty() }
        }
    }

    /** Saves the Dispatch settings; a blank [typedToken] keeps the stored one unless [forget]. */
    fun saveDispatch(url: String, company: String, typedToken: String, forget: Boolean) {
        val storedUrl = DispatchConfig.normaliseUrl(url).ifEmpty { url.trim() }
        _dispatchUrl.value = storedUrl
        config.dispatchUrl = storedUrl
        _dispatchCompany.value = company.trim()
        config.dispatchCompany = company.trim()
        val token = typedToken.trim()
        val problem = DispatchConfig.urlError(url) ?: DispatchConfig.tokenWarning(token)
        viewModelScope.launch {
            val stored = withContext(Dispatchers.IO) {
                when {
                    token.isNotEmpty() -> dispatchTokens.set(token)
                    forget -> {
                        dispatchTokens.clear()
                        true
                    }
                    else -> true
                }
            }
            _hasDispatchToken.value = withContext(Dispatchers.IO) { dispatchTokens.get().isNotEmpty() }
            _dispatchNotice.value = when {
                !stored -> DispatchNotice.TokenNotStored
                problem != null -> DispatchNotice.Problem(problem)
                else -> DispatchNotice.Saved
            }
        }
    }

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
}
