package com.github.meetingrecorder.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.coroutineContext

/** A Maestro request failed in a way the user needs to hear about. */
class MaestroException(message: String, val statusCode: Int? = null) : Exception(message)

data class FiledTask(val key: String, val title: String, val created: Boolean)

data class FiledMeeting(
    val key: String,
    val url: String,
    val created: Boolean,
    val tasks: List<FiledTask> = emptyList(),
) {
    val newTasks: Int get() = tasks.count { it.created }
}

/** The body `POST /api/meetings` takes; field names are Maestro's. */
@Serializable
data class MaestroMeetingPayload(
    @SerialName("meeting_id") val meetingId: String,
    val title: String,
    val date: String,
    @SerialName("duration_seconds") val durationSeconds: Int,
    val notes: String,
    val transcript: String,
    val actions: List<String>,
    val company: String? = null,
)

/** Anything that files a meeting; the seam [MeetingFiling] is tested through. */
fun interface MeetingFiler {
    suspend fun fileMeeting(payload: MaestroMeetingPayload): FiledMeeting
}

/** Talks to Maestro over the tailnet; a token is sent only when one is given. */
class MaestroClient(
    baseUrl: String = DEFAULT_URL,
    private val token: String? = null,
    private val client: OkHttpClient = defaultClient,
    private val delayFn: suspend (Long) -> Unit = { delay(it) },
) : MeetingFiler {

    private val base = baseUrl.trim().trimEnd('/')

    /** Sends one meeting. Safe to repeat: Maestro updates on the same meeting_id. */
    override suspend fun fileMeeting(payload: MaestroMeetingPayload): FiledMeeting = withContext(Dispatchers.IO) {
        val body = lenientJson.encodeToString(MaestroMeetingPayload.serializer(), payload)
        val request = Request.Builder()
            .url("$base$MEETINGS_PATH")
            .header("Accept", "application/json")
            .apply { if (!token.isNullOrEmpty()) header("Authorization", "Bearer $token") }
            .post(body.toRequestBody(JSON))
            .build()
        val raw = withRetry { execute(request) }
        val parsed = try {
            lenientJson.parseToJsonElement(raw.ifBlank { "{}" }) as? JsonObject
        } catch (_: Exception) {
            null
        } ?: throw MaestroException("Maestro sent a reply this app could not read.")
        parseFiled(parsed, base)
    }

    private fun execute(request: Request): String {
        val response = try {
            client.newCall(request).execute()
        } catch (e: IOException) {
            throw Unreachable(e.message ?: e.javaClass.simpleName)
        }
        return response.use {
            val text = it.body?.string().orEmpty()
            if (!it.isSuccessful) throw MaestroException(errorMessage(it.code, text), it.code)
            text
        }
    }

    private class Unreachable(val reason: String) : Exception(reason)

    private suspend fun <T> withRetry(block: () -> T): T {
        var backoffMs = INITIAL_BACKOFF_MS
        repeat(MAX_RETRIES + 1) { attempt ->
            coroutineContext.ensureActive()
            try {
                return block()
            } catch (e: Exception) {
                val transient = e is Unreachable || (e is MaestroException && isTransientStatus(e.statusCode))
                if (!transient || attempt == MAX_RETRIES) {
                    throw if (e is Unreachable) MaestroException("Could not reach Maestro: ${e.reason}") else e
                }
                delayFn(backoffMs)
                backoffMs *= 2
            }
        }
        error("unreachable")
    }

    companion object {
        const val MEETINGS_PATH = "/api/meetings"
        const val DEFAULT_URL = "https://maestro.tailefa891.ts.net"
        private const val MAX_RETRIES = 2
        private const val INITIAL_BACKOFF_MS = 2_000L
        private val JSON = "application/json".toMediaType()

        private val defaultClient = OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .followSslRedirects(false)
            .build()

        private fun isTransientStatus(code: Int?): Boolean = code != null && (code >= 500 || code == 429)

        /** A Maestro error response as a sentence worth showing. */
        fun errorMessage(status: Int, body: String): String {
            val detail = try {
                ((lenientJson.parseToJsonElement(body) as? JsonObject)?.get("detail") as? JsonPrimitive)
                    ?.takeIf { it.isString }?.content
            } catch (_: Exception) {
                null
            }
            return when (status) {
                401 -> "Maestro refused this device. Check that it is on the tailnet."
                404 -> "Nothing at Maestro's address files meetings."
                else -> detail ?: "Maestro returned HTTP $status."
            }
        }

        /** Maestro's reply, read into what the filing notice reports. */
        fun parseFiled(body: JsonObject, baseUrl: String): FiledMeeting {
            val tasks = (body["tasks"] as? JsonArray).orEmpty()
                .mapNotNull { it as? JsonObject }
                .map { FiledTask(it.string("key"), it.string("title"), it.flag("created")) }
            val path = body.string("url")
            return FiledMeeting(
                key = body.string("key"),
                url = if (path.startsWith("/")) "$baseUrl$path" else path,
                created = body.flag("created"),
                tasks = tasks,
            )
        }

        private fun JsonObject.string(key: String): String = (this[key] as? JsonPrimitive)?.contentOrNull.orEmpty()

        private fun JsonObject.flag(key: String): Boolean = (this[key] as? JsonPrimitive)?.booleanOrNull ?: false
    }
}
