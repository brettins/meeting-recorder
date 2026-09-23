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

/** A Dispatch request failed in a way the user needs to hear about. */
class DispatchException(message: String, val statusCode: Int? = null) : Exception(message)

data class FiledTask(val key: String, val title: String, val created: Boolean)

data class FiledMeeting(
    val key: String,
    val url: String,
    val created: Boolean,
    val tasks: List<FiledTask> = emptyList(),
) {
    val newTasks: Int get() = tasks.count { it.created }
}

/** The body `POST /api/meetings` takes; field names are Dispatch's. */
@Serializable
data class DispatchMeetingPayload(
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
    suspend fun fileMeeting(payload: DispatchMeetingPayload): FiledMeeting
}

/** Talks to one Dispatch server with one API token. */
class DispatchClient(
    private val baseUrl: String,
    private val token: String,
    private val client: OkHttpClient = defaultClient,
    private val delayFn: suspend (Long) -> Unit = { delay(it) },
) : MeetingFiler {

    private val base = DispatchConfig.normaliseUrl(baseUrl)

    /** Sends one meeting. Safe to repeat: Dispatch updates on the same meeting_id. */
    override suspend fun fileMeeting(payload: DispatchMeetingPayload): FiledMeeting = withContext(Dispatchers.IO) {
        if (base.isEmpty()) {
            throw DispatchException(DispatchConfig.urlError(baseUrl) ?: "No Dispatch URL is set.")
        }
        val body = lenientJson.encodeToString(DispatchMeetingPayload.serializer(), payload)
        val request = Request.Builder()
            .url("$base$MEETINGS_PATH")
            .header("Authorization", "Bearer $token")
            .header("Accept", "application/json")
            .post(body.toRequestBody(JSON))
            .build()
        val raw = withRetry { execute(request) }
        val parsed = try {
            lenientJson.parseToJsonElement(raw.ifBlank { "{}" }) as? JsonObject
        } catch (_: Exception) {
            null
        } ?: throw DispatchException("Dispatch sent a reply this app could not read.")
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
            if (!it.isSuccessful) throw DispatchException(errorMessage(it.code, text), it.code)
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
                val transient = e is Unreachable || (e is DispatchException && isTransientStatus(e.statusCode))
                if (!transient || attempt == MAX_RETRIES) {
                    throw if (e is Unreachable) DispatchException("Could not reach Dispatch: ${e.reason}") else e
                }
                delayFn(backoffMs)
                backoffMs *= 2
            }
        }
        error("unreachable")
    }

    companion object {
        const val MEETINGS_PATH = "/api/meetings"
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

        /** A Dispatch error response as a sentence worth showing. */
        fun errorMessage(status: Int, body: String): String {
            val detail = try {
                ((lenientJson.parseToJsonElement(body) as? JsonObject)?.get("detail") as? JsonPrimitive)
                    ?.takeIf { it.isString }?.content
            } catch (_: Exception) {
                null
            }
            return when (status) {
                401 -> "Dispatch refused the API token. Check it in Settings → Dispatch."
                503 -> "The Dispatch server has no API token configured. Set DISPATCH_API_TOKEN on it."
                404 -> "Nothing at that address files meetings. Check the URL in Settings → Dispatch."
                else -> detail ?: "Dispatch returned HTTP $status."
            }
        }

        /** Dispatch's reply, read into what the filing notice reports. */
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
