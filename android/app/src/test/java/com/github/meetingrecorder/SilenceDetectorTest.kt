package com.github.meetingrecorder

import com.github.meetingrecorder.audio.SilenceDetector
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SilenceDetectorTest {
    private var clock = 0L
    private val detector = SilenceDetector(now = { clock })
    private val quiet = -60f
    private val loud = -20f

    @Test
    fun justUnderWindowDoesNotWarn() {
        detector.update(quiet)
        clock = 29_999
        assertFalse(detector.update(quiet))
    }

    @Test
    fun exactlyWindowWarns() {
        detector.update(quiet)
        clock = 30_000
        assertTrue(detector.update(quiet))
    }

    @Test
    fun levelAtThresholdCountsAsSound() {
        detector.update(quiet)
        clock = 40_000
        assertFalse(detector.update(SilenceDetector.DEFAULT_THRESHOLD_DB))
    }

    @Test
    fun warningStaysUntilSoundReturns() {
        detector.update(quiet)
        clock = 45_000
        assertTrue(detector.update(quiet))
        clock = 60_000
        assertTrue(detector.update(quiet))
        assertFalse(detector.update(loud))
        assertFalse(detector.warning)
    }

    @Test
    fun soundResetsTheWindow() {
        detector.update(quiet)
        clock = 25_000
        detector.update(loud)
        clock = 50_000
        assertFalse(detector.update(quiet))
        clock = 79_999
        assertFalse(detector.update(quiet))
        clock = 80_000
        assertTrue(detector.update(quiet))
    }

    @Test
    fun secondStretchWarnsAgainAfterSound() {
        detector.update(quiet)
        clock = 30_000
        assertTrue(detector.update(quiet))
        clock = 31_000
        assertFalse(detector.update(loud))
        assertFalse(detector.update(quiet))
        clock = 60_999
        assertFalse(detector.update(quiet))
        clock = 61_000
        assertTrue(detector.update(quiet))
    }

    @Test
    fun mutedMicNeverWarnsAndRestartsTheWindow() {
        detector.update(quiet)
        clock = 50_000
        assertFalse(detector.update(quiet, muted = true))
        assertFalse(detector.warning)
        assertFalse(detector.update(quiet))
        clock = 79_999
        assertFalse(detector.update(quiet))
        clock = 80_000
        assertTrue(detector.update(quiet))
    }

    @Test
    fun mutingClearsAnActiveWarning() {
        detector.update(quiet)
        clock = 30_000
        assertTrue(detector.update(quiet))
        assertFalse(detector.update(quiet, muted = true))
    }

    @Test
    fun resetClearsState() {
        detector.update(quiet)
        clock = 30_000
        detector.update(quiet)
        detector.reset()
        assertFalse(detector.warning)
        assertFalse(detector.update(quiet))
    }
}
