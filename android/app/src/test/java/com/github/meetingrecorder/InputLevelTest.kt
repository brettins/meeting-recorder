package com.github.meetingrecorder

import com.github.meetingrecorder.audio.InputLevel
import org.junit.Assert.assertEquals
import org.junit.Test

class InputLevelTest {
    @Test
    fun fullScaleIsZeroDb() = assertEquals(0f, InputLevel.dbFromAmplitude(32_767), 0.001f)

    @Test
    fun halfScaleIsAboutMinusSix() = assertEquals(-6.02f, InputLevel.dbFromAmplitude(16_384), 0.05f)

    @Test
    fun zeroAndNegativeHitTheFloor() {
        assertEquals(InputLevel.FLOOR_DB, InputLevel.dbFromAmplitude(0), 0f)
        assertEquals(InputLevel.FLOOR_DB, InputLevel.dbFromAmplitude(-5), 0f)
    }

    @Test
    fun overRangeClampsToZeroDb() = assertEquals(0f, InputLevel.dbFromAmplitude(70_000), 0.001f)

    @Test
    fun amplitudeOneNeverGoesBelowFloor() {
        assertEquals(InputLevel.FLOOR_DB, InputLevel.dbFromAmplitude(1), 0f)
    }

    @Test
    fun meterFractionMapsRangeAndClamps() {
        assertEquals(0f, InputLevel.meterFraction(-90f), 0f)
        assertEquals(0f, InputLevel.meterFraction(-60f), 0f)
        assertEquals(0.5f, InputLevel.meterFraction(-30f), 0.001f)
        assertEquals(1f, InputLevel.meterFraction(0f), 0f)
        assertEquals(1f, InputLevel.meterFraction(6f), 0f)
    }
}
