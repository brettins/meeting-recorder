package com.github.meetingrecorder.audio

/** Flags input that stays below [thresholdDb] for [windowMs] continuously; a muted mic never counts. */
class SilenceDetector(
    private val thresholdDb: Float = DEFAULT_THRESHOLD_DB,
    private val windowMs: Long = DEFAULT_WINDOW_MS,
    private val now: () -> Long,
) {
    private var quietSince: Long? = null

    var warning: Boolean = false
        private set

    fun update(levelDb: Float, muted: Boolean = false): Boolean {
        if (muted || levelDb >= thresholdDb) {
            reset()
            return false
        }
        val t = now()
        val start = quietSince ?: t.also { quietSince = it }
        if (t - start >= windowMs) warning = true
        return warning
    }

    fun reset() {
        quietSince = null
        warning = false
    }

    companion object {
        const val DEFAULT_THRESHOLD_DB = -45f
        const val DEFAULT_WINDOW_MS = 30_000L
        const val DEFAULT_WINDOW_SECS = (DEFAULT_WINDOW_MS / 1000).toInt()
    }
}
