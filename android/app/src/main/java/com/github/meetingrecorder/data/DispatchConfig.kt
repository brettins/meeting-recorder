package com.github.meetingrecorder.data

import java.net.URI

/** Dispatch settings rules: which URLs a token may be sent to, and what counts as configured. */
object DispatchConfig {

    /** A Dispatch base URL without a trailing slash, or empty when it is not one. */
    fun normaliseUrl(text: String): String {
        val candidate = text.trim().trimEnd('/')
        if (candidate.isEmpty()) return ""
        val uri = try {
            URI(candidate)
        } catch (_: Exception) {
            return ""
        }
        val scheme = uri.scheme?.lowercase() ?: return ""
        if (scheme != "http" && scheme != "https") return ""
        val host = uri.host ?: return ""
        if (scheme == "http" && !isLocal(host)) return ""
        return candidate
    }

    /** Whether a host never leaves this device, so a token may cross it unencrypted. */
    fun isLocal(host: String): Boolean =
        host.lowercase().removePrefix("[").removeSuffix("]") in setOf("localhost", "127.0.0.1", "::1")

    /** Why [text] cannot be used as the Dispatch URL, or null. */
    fun urlError(text: String): String? {
        if (text.isBlank() || normaliseUrl(text).isNotEmpty()) return null
        return "The Dispatch URL needs https://, like https://zed.tailefa891.ts.net. " +
            "Plain http:// only works for this device, because the token would travel unencrypted."
    }

    /** A format problem with a pasted token, or null. */
    fun tokenWarning(token: String): String? =
        if (token.trim().any { it.isWhitespace() }) {
            "The Dispatch token contains a space or line break; the paste picked up extra text."
        } else {
            null
        }

    /** Whether filing can be offered: a usable URL and a stored token. */
    fun isConfigured(url: String, hasToken: Boolean): Boolean = normaliseUrl(url).isNotEmpty() && hasToken
}
