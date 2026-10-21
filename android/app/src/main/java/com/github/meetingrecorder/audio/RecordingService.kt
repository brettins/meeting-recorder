package com.github.meetingrecorder.audio

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.github.meetingrecorder.MainActivity
import com.github.meetingrecorder.MeetingRecorderApp
import com.github.meetingrecorder.R
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.File

/** Lifecycle phase of the recorder, surfaced from the service to the UI via [RecordingService.status]. */
enum class RecordingPhase { IDLE, RECORDING, STOPPED, FAILED }

data class RecorderStatus(
    val phase: RecordingPhase = RecordingPhase.IDLE,
    val outputPath: String? = null,
    // True if the OS silenced the mic at any point — the recording is likely empty of sound.
    val silenced: Boolean = false,
    // True once the input has been quiet for the silence window; clears when sound returns.
    val silentInput: Boolean = false,
)

/**
 * Foreground service (type `microphone`) that owns the [AudioRecorder]. Running recording inside a
 * foreground service is what keeps microphone capture alive when the app is backgrounded by a
 * notification, another app, or a call UI — without it, Android cuts the mic and the recording goes
 * silent. The result (and whether the mic was silenced) is published through [status].
 */
class RecordingService : Service() {

    private val audioRecorder by lazy { AudioRecorder(this) }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var levelJob: Job? = null

    // The interruption filter that was active before we engaged DND, so we can restore it on stop.
    private var savedInterruptionFilter: Int? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> handleStart(intent)
            ACTION_STOP -> handleStop()
            else -> stopSelf()
        }
        return START_NOT_STICKY
    }

    private fun handleStart(intent: Intent) {
        val dirPath = intent.getStringExtra(EXTRA_DIR)
        if (dirPath == null) {
            _status.value = RecorderStatus(RecordingPhase.FAILED)
            stopSelf()
            return
        }
        val bitrate = intent.getIntExtra(EXTRA_BITRATE, 64_000)

        createChannel()
        ServiceCompat.startForeground(
            this,
            NOTIF_ID,
            buildNotification(),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
        )

        try {
            val file = audioRecorder.start(File(dirPath), bitrate) {
                // OS silenced the mic mid-recording — reflect it immediately in the shared status.
                _status.update { it.copy(silenced = true) }
            }
            enableDndIfRequested()
            _status.update { it.copy(phase = RecordingPhase.RECORDING, outputPath = file.absolutePath) }
            startLevelMonitor()
        } catch (e: Exception) {
            _status.value = RecorderStatus(RecordingPhase.FAILED)
            ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
    }

    private fun handleStop() {
        stopLevelMonitor()
        audioRecorder.stop()
        restoreDnd()
        _status.update {
            it.copy(
                phase = RecordingPhase.STOPPED,
                silenced = it.silenced || audioRecorder.wasInterrupted,
                silentInput = false,
            )
        }
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        // Safety net for an unclean teardown (e.g. the OS killing the service under memory pressure
        // without an ACTION_STOP): finalize the recorder so the mic is released and the .m4a is
        // properly closed rather than left truncated, and never leave the user stuck in Do Not
        // Disturb. Both calls are idempotent, so a normal ACTION_STOP that already ran is a no-op.
        stopLevelMonitor()
        scope.cancel()
        audioRecorder.stop()
        restoreDnd()
        super.onDestroy()
    }

    // Suppresses notification/call sounds (not the recording itself) so they don't pull the user
    // away or bleed into the audio. Opt-in; no-ops without the one-time DND-access grant.
    private fun enableDndIfRequested() {
        val config = (application as MeetingRecorderApp).container.config
        if (!config.dndDuringRecordingEnabled) return
        val nm = getSystemService(NotificationManager::class.java)
        if (!nm.isNotificationPolicyAccessGranted) return
        // Capture the user's real filter only once — a re-delivered start intent must not overwrite
        // it with our own INTERRUPTION_FILTER_ALARMS, or restore would leave DND stuck on.
        if (savedInterruptionFilter == null) {
            savedInterruptionFilter = nm.currentInterruptionFilter
        }
        nm.setInterruptionFilter(NotificationManager.INTERRUPTION_FILTER_ALARMS)
    }

    private fun restoreDnd() {
        val previous = savedInterruptionFilter ?: return
        savedInterruptionFilter = null
        val nm = getSystemService(NotificationManager::class.java)
        if (nm.isNotificationPolicyAccessGranted) {
            nm.setInterruptionFilter(previous)
        }
    }

    private fun startLevelMonitor() {
        levelJob?.cancel()
        val detector = SilenceDetector(now = SystemClock::elapsedRealtime)
        levelJob = scope.launch {
            while (true) {
                delay(LEVEL_POLL_MS)
                val db = InputLevel.dbFromAmplitude(audioRecorder.maxAmplitude())
                _level.value = db
                val warning = detector.update(db, audioRecorder.clientSilenced)
                if (warning != _status.value.silentInput) {
                    _status.update { it.copy(silentInput = warning) }
                    getSystemService(NotificationManager::class.java)
                        .notify(NOTIF_ID, buildNotification(warning))
                }
            }
        }
    }

    private fun stopLevelMonitor() {
        levelJob?.cancel()
        levelJob = null
        _level.value = InputLevel.FLOOR_DB
    }

    private fun createChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.notif_channel_recording),
            NotificationManager.IMPORTANCE_LOW,
        )
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun buildNotification(silentInput: Boolean = false): Notification {
        val contentIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            },
            PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.notif_recording_title))
            .setContentText(
                if (silentInput) {
                    getString(R.string.notif_silent_input_text, SilenceDetector.DEFAULT_WINDOW_SECS)
                } else {
                    getString(R.string.notif_recording_text)
                },
            )
            .setSmallIcon(R.drawable.ic_stat_mic)
            .setOngoing(true)
            .setContentIntent(contentIntent)
            .build()
    }

    companion object {
        private const val CHANNEL_ID = "recording"
        private const val NOTIF_ID = 1001
        private const val ACTION_START = "com.github.meetingrecorder.action.START"
        private const val ACTION_STOP = "com.github.meetingrecorder.action.STOP"
        private const val EXTRA_DIR = "dir"
        private const val EXTRA_BITRATE = "bitrate"
        private const val LEVEL_POLL_MS = 200L

        private val _status = MutableStateFlow(RecorderStatus())
        val status: StateFlow<RecorderStatus> = _status.asStateFlow()

        private val _level = MutableStateFlow(InputLevel.FLOOR_DB)
        val level: StateFlow<Float> = _level.asStateFlow()

        fun start(context: Context, dir: File, bitrate: Int) {
            _status.value = RecorderStatus(RecordingPhase.IDLE)
            val intent = Intent(context, RecordingService::class.java).apply {
                action = ACTION_START
                putExtra(EXTRA_DIR, dir.absolutePath)
                putExtra(EXTRA_BITRATE, bitrate)
            }
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            val intent = Intent(context, RecordingService::class.java).apply {
                action = ACTION_STOP
            }
            context.startService(intent)
        }
    }
}
