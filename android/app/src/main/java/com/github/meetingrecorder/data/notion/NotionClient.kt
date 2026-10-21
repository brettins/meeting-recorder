package com.github.meetingrecorder.data.notion

import kotlinx.coroutines.delay
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

const val NOTION_API_ROOT = "https://api.notion.com/v1"
const val NOTION_VERSION = "2025-09-03"
const val SINGLE_PART_LIMIT: Long = 20L * 1024 * 1024
const val FREE_PLAN_FILE_LIMIT: Long = 5L * 1024 * 1024

/** A failed Notion call; [message] is the English fallback. */
class NotionError(
    val kind: Kind,
    message: String,
    val statusCode: Int? = null,
    val arg: String = "",
) : Exception(message) {
    enum class Kind {
        TOKEN_REJECTED,
        NOT_SHARED,
        FORBIDDEN,
        HTTP,
        UNREADABLE_REPLY,
        NETWORK,
        FILE_TOO_LARGE,
        NO_UPLOAD_TARGET,
        NO_TOKEN,
        TOKEN_STORE,
        UNEXPECTED,
    }
}

data class NotionPage(val id: String, val title: String, val date: String?, val url: String)

object NotionApi {
    fun pageTitle(page: JsonObject): String {
        val properties = page["properties"] as? JsonObject ?: return ""
        for (value in properties.values) {
            val obj = value as? JsonObject ?: continue
            if (obj["type"]?.jsonPrimitive?.contentOrNull == "title") {
                val parts = obj["title"] as? JsonArray ?: return ""
                return parts.joinToString("") { (it as? JsonObject)?.get("plain_text")?.jsonPrimitive?.contentOrNull ?: "" }
                    .trim()
            }
        }
        return ""
    }

    fun pageDate(page: JsonObject): String? {
        val properties = page["properties"] as? JsonObject ?: return null
        for (value in properties.values) {
            val obj = value as? JsonObject ?: continue
            if (obj["type"]?.jsonPrimitive?.contentOrNull == "date") {
                val start = (obj["date"] as? JsonObject)?.get("start")?.jsonPrimitive?.contentOrNull
                if (!start.isNullOrEmpty()) return start.take(10)
            }
        }
        return null
    }

    fun parsePageSummary(page: JsonObject): NotionPage = NotionPage(
        id = page["id"]?.jsonPrimitive?.contentOrNull ?: "",
        title = pageTitle(page),
        date = pageDate(page),
        url = page["url"]?.jsonPrimitive?.contentOrNull ?: "",
    )

    fun filesPropertyNames(schema: JsonObject): List<String> =
        (schema["properties"] as? JsonObject).orEmpty()
            .filter { (_, v) -> (v as? JsonObject)?.get("type")?.jsonPrimitive?.contentOrNull == "files" }
            .keys.sorted()

    fun titlePropertyName(schema: JsonObject): String =
        (schema["properties"] as? JsonObject).orEmpty()
            .entries.firstOrNull { (_, v) -> (v as? JsonObject)?.get("type")?.jsonPrimitive?.contentOrNull == "title" }
            ?.key ?: "Name"

    /** Returns the error for [size] when it cannot be uploaded in one piece, or null. */
    fun uploadSizeError(size: Long): NotionError? {
        if (size <= SINGLE_PART_LIMIT) return null
        val mib = "%.1f".format(size / (1024.0 * 1024.0))
        return NotionError(
            NotionError.Kind.FILE_TOO_LARGE,
            "The recording is $mib MiB. Notion accepts at most 20 MiB in one piece, " +
                "and this app does not split larger uploads. File the notes without the audio, " +
                "or record at a lower quality.",
            arg = mib,
        )
    }

    /** True when [size] would only fit on a paid workspace. */
    fun exceedsFreePlan(size: Long): Boolean = size > FREE_PLAN_FILE_LIMIT

    fun mapError(status: Int, body: String): NotionError {
        val detail = try {
            lenientNotionJson.parseToJsonElement(body).jsonObject["message"]?.jsonPrimitive?.contentOrNull ?: ""
        } catch (_: Exception) {
            ""
        }
        return when (status) {
            401 -> NotionError(
                NotionError.Kind.TOKEN_REJECTED,
                "Notion rejected the integration token. Check it in Settings.",
                status,
            )
            404 -> NotionError(
                NotionError.Kind.NOT_SHARED,
                "Notion cannot see that database. Share it with the integration from the " +
                    "database's menu under Connections.",
                status,
            )
            403 -> NotionError(
                NotionError.Kind.FORBIDDEN,
                "The integration does not have permission for that page or database.",
                status,
            )
            else -> NotionError(
                NotionError.Kind.HTTP,
                detail.ifEmpty { "Notion returned HTTP $status." },
                status,
                arg = detail,
            )
        }
    }

    fun mimeFor(file: File): String = when (file.extension.lowercase()) {
        "m4a" -> "audio/mp4"
        "mp3" -> "audio/mpeg"
        "wav" -> "audio/wav"
        "ogg" -> "audio/ogg"
        else -> "application/octet-stream"
    }
}

internal val lenientNotionJson = Json {
    ignoreUnknownKeys = true
    isLenient = true
}

/** Talks to one Notion workspace using one integration token. */
class NotionClient(
    private val token: String,
    private val baseUrl: String = NOTION_API_ROOT,
    private val http: OkHttpClient = defaultHttp,
    private val delayFn: suspend (Long) -> Unit = { delay(it) },
) {
    companion object {
        private const val MAX_RETRIES = 2
        private const val INITIAL_BACKOFF_MS = 2_000L

        private val defaultHttp = OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(120, TimeUnit.SECONDS)
            .build()
    }

    suspend fun resolveDataSource(databaseId: String): String {
        val body = request("GET", "/databases/$databaseId")
        val first = (body["data_sources"] as? JsonArray)?.firstOrNull() as? JsonObject
        val id = first?.get("id")?.jsonPrimitive?.contentOrNull
        return if (!id.isNullOrEmpty()) id else databaseId
    }

    suspend fun dataSourceSchema(dataSourceId: String): JsonObject = request("GET", "/data_sources/$dataSourceId")

    suspend fun recentPages(dataSourceId: String, limit: Int = 50): List<NotionPage> {
        val payload = buildJsonObject { put("page_size", minOf(limit, 100)) }
        val body = request("POST", "/data_sources/$dataSourceId/query", payload)
        return (body["results"] as? JsonArray).orEmpty()
            .mapNotNull { it as? JsonObject }
            .map(NotionApi::parsePageSummary)
    }

    suspend fun createPage(dataSourceId: String, title: String, titleProperty: String = "Name"): NotionPage {
        val payload = buildJsonObject {
            put(
                "parent",
                buildJsonObject {
                    put("type", "data_source_id")
                    put("data_source_id", dataSourceId)
                },
            )
            put(
                "properties",
                buildJsonObject {
                    put(
                        titleProperty,
                        buildJsonObject {
                            put(
                                "title",
                                JsonArray(
                                    listOf(
                                        buildJsonObject {
                                            put("type", "text")
                                            put("text", buildJsonObject { put("content", title) })
                                        },
                                    ),
                                ),
                            )
                        },
                    )
                },
            )
        }
        return NotionApi.parsePageSummary(request("POST", "/pages", payload))
    }

    suspend fun appendBlocks(pageId: String, batches: List<List<JsonObject>>) {
        for (batch in batches) {
            if (batch.isEmpty()) continue
            request(
                "PATCH",
                "/blocks/$pageId/children",
                buildJsonObject { put("children", JsonArray(batch)) },
            )
        }
    }

    suspend fun uploadFile(file: File): String {
        NotionApi.uploadSizeError(file.length())?.let { throw it }
        val contentType = NotionApi.mimeFor(file)
        val created = request(
            "POST",
            "/file_uploads",
            buildJsonObject {
                put("mode", "single_part")
                put("filename", file.name)
                put("content_type", contentType)
            },
        )
        val uploadId = created["id"]?.jsonPrimitive?.contentOrNull.orEmpty()
        if (uploadId.isEmpty()) {
            throw NotionError(NotionError.Kind.NO_UPLOAD_TARGET, "Notion did not return an upload target for the recording.")
        }
        val derivedUrl = "$baseUrl/file_uploads/$uploadId/send"
        val uploadUrl = created["upload_url"]?.jsonPrimitive?.contentOrNull.orEmpty()
            .takeIf { trustedUploadUrl(it) } ?: derivedUrl
        val body = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("file", file.name.replace("\"", ""), file.asRequestBody(contentType.toMediaType()))
            .build()
        send(uploadUrl, "POST", body)
        return uploadId
    }

    suspend fun attachFile(pageId: String, propertyName: String, uploadId: String) {
        val payload = buildJsonObject {
            put(
                "properties",
                buildJsonObject {
                    put(
                        propertyName,
                        buildJsonObject {
                            put("type", "files")
                            put(
                                "files",
                                JsonArray(
                                    listOf(
                                        buildJsonObject {
                                            put("type", "file_upload")
                                            put("file_upload", buildJsonObject { put("id", uploadId) })
                                        },
                                    ),
                                ),
                            )
                        },
                    )
                },
            )
        }
        request("PATCH", "/pages/$pageId", payload)
    }

    private fun trustedUploadUrl(candidate: String): Boolean {
        val url = candidate.toHttpUrlOrNull() ?: return false
        val base = baseUrl.toHttpUrlOrNull() ?: return false
        return url.host == base.host && (url.scheme == "https" || url.scheme == base.scheme)
    }

    private suspend fun request(method: String, path: String, payload: JsonElement? = null): JsonObject {
        val body = payload?.let { it.toString().toRequestBody("application/json".toMediaType()) }
        val raw = send("$baseUrl$path", method, body)
        if (raw.isEmpty()) return JsonObject(emptyMap())
        return try {
            require(raw.trimStart().firstOrNull() in listOf('{', '[')) { "not a JSON document" }
            Json.parseToJsonElement(raw) as? JsonObject ?: JsonObject(emptyMap())
        } catch (e: Exception) {
            throw NotionError(NotionError.Kind.UNREADABLE_REPLY, "Notion sent a reply this app could not read: ${e.message}")
        }
    }

    private suspend fun send(url: String, method: String, body: RequestBody?): String {
        var attempt = 0
        while (true) {
            try {
                return execute(url, method, body)
            } catch (e: NotionError) {
                val status = e.statusCode
                val transient = status != null && (status == 429 || status in 500..599)
                if (!transient || attempt >= MAX_RETRIES) throw e
                delayFn(INITIAL_BACKOFF_MS shl attempt)
                attempt++
            }
        }
    }

    private suspend fun execute(url: String, method: String, body: RequestBody?): String =
        suspendCancellableCoroutine { continuation ->
            val request = Request.Builder()
                .url(url)
                .header("Authorization", "Bearer $token")
                .header("Notion-Version", NOTION_VERSION)
                .method(method, body ?: if (method == "GET") null else ByteArray(0).toRequestBody(null))
                .build()
            val call = http.newCall(request)
            continuation.invokeOnCancellation { call.cancel() }
            call.enqueue(
                object : Callback {
                    override fun onFailure(call: Call, e: IOException) {
                        continuation.resumeWithException(
                            NotionError(NotionError.Kind.NETWORK, "Could not reach Notion: ${e.message}", arg = e.message.orEmpty()),
                        )
                    }

                    override fun onResponse(call: Call, response: Response) {
                        response.use {
                            try {
                                val text = it.body?.string().orEmpty()
                                if (it.isSuccessful) {
                                    continuation.resume(text)
                                } else {
                                    continuation.resumeWithException(NotionApi.mapError(it.code, text))
                                }
                            } catch (e: IOException) {
                                continuation.resumeWithException(
                                    NotionError(NotionError.Kind.NETWORK, "Could not reach Notion: ${e.message}", arg = e.message.orEmpty()),
                                )
                            }
                        }
                    }
                },
            )
        }
}
