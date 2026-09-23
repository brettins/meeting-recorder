package com.github.meetingrecorder

import com.github.meetingrecorder.data.DispatchConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DispatchConfigTest {

    @Test
    fun `a trailing slash goes`() {
        assertEquals("https://dispatch:8000", DispatchConfig.normaliseUrl(" https://dispatch:8000/ "))
    }

    @Test
    fun `plain http to another machine is refused`() {
        assertEquals("", DispatchConfig.normaliseUrl("http://dispatch:8000"))
    }

    @Test
    fun `plain http to this device is allowed`() {
        assertEquals("http://127.0.0.1:8420", DispatchConfig.normaliseUrl("http://127.0.0.1:8420"))
        assertEquals("http://localhost:8420", DispatchConfig.normaliseUrl("http://localhost:8420"))
        assertEquals("http://[::1]:8420", DispatchConfig.normaliseUrl("http://[::1]:8420"))
    }

    @Test
    fun `a capitalised scheme still counts`() {
        assertEquals("HTTPS://dispatch.tail.ts.net", DispatchConfig.normaliseUrl("HTTPS://dispatch.tail.ts.net"))
    }

    @Test
    fun `a bare host is refused`() {
        assertEquals("", DispatchConfig.normaliseUrl("dispatch:8000"))
    }

    @Test
    fun `another scheme is refused`() {
        assertEquals("", DispatchConfig.normaliseUrl("ftp://dispatch"))
    }

    @Test
    fun `garbage is refused rather than thrown`() {
        assertEquals("", DispatchConfig.normaliseUrl("https://exa mple.com"))
    }

    @Test
    fun `empty is not an error`() {
        assertNull(DispatchConfig.urlError(""))
    }

    @Test
    fun `a bad url says what to type`() {
        assertTrue(DispatchConfig.urlError("dispatch")!!.contains("https://"))
    }

    @Test
    fun `plain http off the device says why`() {
        assertTrue(DispatchConfig.urlError("http://dispatch:8000")!!.contains("unencrypted"))
    }

    @Test
    fun `url and token are both needed`() {
        assertTrue(DispatchConfig.isConfigured("https://d:8000", hasToken = true))
        assertFalse(DispatchConfig.isConfigured("https://d:8000", hasToken = false))
        assertFalse(DispatchConfig.isConfigured("", hasToken = true))
    }

    @Test
    fun `a bad url is not configured`() {
        assertFalse(DispatchConfig.isConfigured("http://dispatch", hasToken = true))
    }

    @Test
    fun `a space inside the token is flagged`() {
        assertNotNull(DispatchConfig.tokenWarning("abc 123"))
        assertNull(DispatchConfig.tokenWarning("  abc123\n"))
    }
}
