package com.github.meetingrecorder.audio

import kotlin.math.log10

/** Converts MediaRecorder peak amplitude into decibels and a 0..1 meter fraction. */
object InputLevel {
    const val FLOOR_DB = -90f
    const val METER_MIN_DB = -60f
    private const val MAX_AMPLITUDE = 32_767f

    fun dbFromAmplitude(amplitude: Int): Float {
        if (amplitude <= 0) return FLOOR_DB
        val db = 20f * log10(amplitude.coerceAtMost(MAX_AMPLITUDE.toInt()) / MAX_AMPLITUDE)
        return db.coerceAtLeast(FLOOR_DB)
    }

    fun meterFraction(db: Float): Float = ((db - METER_MIN_DB) / -METER_MIN_DB).coerceIn(0f, 1f)
}
