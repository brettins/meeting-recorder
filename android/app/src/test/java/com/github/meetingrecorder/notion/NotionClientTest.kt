package com.github.meetingrecorder.notion

import com.github.meetingrecorder.data.notion.FilingWarning
import com.github.meetingrecorder.data.notion.NotionApi
import com.github.meetingrecorder.data.notion.NotionClient
import com.github.meetingrecorder.data.notion.NotionError
import com.github.meetingrecorder.data.notion.NotionFiling
import com.github.meetingrecorder.data.notion.NotionPage
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class NotionClientTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val server = MockWebServer()
    private val delays = mutableListOf<Long>()
    private lateinit var client: NotionClient

    @Before
    fun setUp() {
        server.start()
        client = NotionClient("secret-token", server.url("/v1").toString().trimEnd('/'), delayFn = { delays += it })
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun ok(body: String) = MockResponse().setResponseCode(200).setBody(body)

    private val page = NotionPage("p1", "Standup", "2026-10-21", "https://notion.so/p1")

    private suspend fun errorOf(block: suspend () -> Unit): NotionError {
        try {
            block()
        } catch (e: NotionError) {
            return e
        }
        fail("expected NotionError")
        throw IllegalStateException()
    }

    @Test
    fun `requests carry bearer token and the pinned Notion version`() = runTest {
        server.enqueue(ok("""{"data_sources":[{"id":"ds-1"}]}"""))
        assertEquals("ds-1", client.resolveDataSource("db-1"))
        val request = server.takeRequest()
        assertEquals("GET", request.method)
        assertEquals("/v1/databases/db-1", request.path)
        assertEquals("Bearer secret-token", request.getHeader("Authorization"))
        assertEquals("2025-09-03", request.getHeader("Notion-Version"))
    }

    @Test
    fun `resolveDataSource falls back to the database id on older workspaces`() = runTest {
        server.enqueue(ok("""{"id":"db-1"}"""))
        assertEquals("db-1", client.resolveDataSource("db-1"))
    }

    @Test
    fun `recentPages queries the data source and parses titles and dates`() = runTest {
        server.enqueue(
            ok(
                """{"results":[{"id":"a","url":"u","properties":{"Name":{"type":"title","title":[{"plain_text":"Hi "}]},""" +
                    """"When":{"type":"date","date":{"start":"2026-10-21T09:00:00Z"}}}},{"id":"b","properties":{}}]}""",
            ),
        )
        val pages = client.recentPages("ds-1")
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/v1/data_sources/ds-1/query", request.path)
        assertEquals(NotionPage("a", "Hi", "2026-10-21", "u"), pages[0])
        assertEquals("Untitled", pages[1].title)
        assertNull(pages[1].date)
    }

    @Test
    fun `createPage sets the title property under a data source parent`() = runTest {
        server.enqueue(ok("""{"id":"new","url":"u","properties":{}}"""))
        client.createPage("ds-1", "Weekly", "Meeting")
        val body = Json.parseToJsonElement(server.takeRequest().body.readUtf8()).jsonObject
        assertEquals("ds-1", body["parent"]!!.jsonObject["data_source_id"]!!.jsonPrimitive.content)
        val title = body["properties"]!!.jsonObject["Meeting"]!!.jsonObject["title"]!!.jsonArray[0]
        assertEquals("Weekly", title.jsonObject["text"]!!.jsonObject["content"]!!.jsonPrimitive.content)
    }

    @Test
    fun `appendBlocks sends one PATCH per batch and skips empty batches`() = runTest {
        server.enqueue(ok("{}"))
        server.enqueue(ok("{}"))
        val block = com.github.meetingrecorder.data.notion.NotionBlocks.paragraph("x")
        client.appendBlocks("p1", listOf(listOf(block), emptyList(), listOf(block)))
        assertEquals(2, server.requestCount)
        val request = server.takeRequest()
        assertEquals("PATCH", request.method)
        assertEquals("/v1/blocks/p1/children", request.path)
    }

    @Test
    fun `attachFile points the files property at the upload without a name`() = runTest {
        server.enqueue(ok("{}"))
        client.attachFile("p1", "Recording", "up-1")
        val request = server.takeRequest()
        assertEquals("/v1/pages/p1", request.path)
        val file = Json.parseToJsonElement(request.body.readUtf8()).jsonObject["properties"]!!
            .jsonObject["Recording"]!!.jsonObject["files"]!!.jsonArray[0].jsonObject
        assertEquals("file_upload", file["type"]!!.jsonPrimitive.content)
        assertEquals("up-1", file["file_upload"]!!.jsonObject["id"]!!.jsonPrimitive.content)
        assertFalse(file.containsKey("name"))
    }

    @Test
    fun `uploadFile creates the upload then sends multipart to the returned url`() = runTest {
        val audio = tmp.newFile("recording.m4a").apply { writeBytes(ByteArray(1024) { 1 }) }
        server.enqueue(ok("""{"id":"up-1","upload_url":"${server.url("/v1/file_uploads/up-1/send")}"}"""))
        server.enqueue(ok("{}"))

        assertEquals("up-1", client.uploadFile(audio))

        val create = server.takeRequest()
        assertEquals("/v1/file_uploads", create.path)
        val payload = Json.parseToJsonElement(create.body.readUtf8()).jsonObject
        assertEquals("single_part", payload["mode"]!!.jsonPrimitive.content)
        assertEquals("audio/mp4", payload["content_type"]!!.jsonPrimitive.content)
        val send = server.takeRequest()
        assertEquals("/v1/file_uploads/up-1/send", send.path)
        assertTrue(send.getHeader("Content-Type")!!.startsWith("multipart/form-data"))
        assertTrue(send.body.readUtf8().contains("name=\"file\"; filename=\"recording.m4a\""))
    }

    @Test
    fun `uploadFile derives the send url when the reply omits it`() = runTest {
        val audio = tmp.newFile("recording.m4a").apply { writeBytes(ByteArray(10)) }
        server.enqueue(ok("""{"id":"up-9"}"""))
        server.enqueue(ok("{}"))
        client.uploadFile(audio)
        server.takeRequest()
        assertEquals("/v1/file_uploads/up-9/send", server.takeRequest().path)
    }

    @Test
    fun `uploadFile over 20 MiB is refused before any request`() = runTest {
        val big = tmp.newFile("big.m4a")
        java.io.RandomAccessFile(big, "rw").use { it.setLength(21L * 1024 * 1024) }
        val error = errorOf { client.uploadFile(big) }
        assertEquals(NotionError.Kind.FILE_TOO_LARGE, error.kind)
        assertEquals("21.0", error.arg)
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `size cap boundaries`() {
        assertNull(NotionApi.uploadSizeError(20L * 1024 * 1024))
        assertEquals(NotionError.Kind.FILE_TOO_LARGE, NotionApi.uploadSizeError(20L * 1024 * 1024 + 1)!!.kind)
        assertFalse(NotionApi.exceedsFreePlan(5L * 1024 * 1024))
        assertTrue(NotionApi.exceedsFreePlan(5L * 1024 * 1024 + 1))
    }

    @Test
    fun `error mapping`() {
        assertEquals(NotionError.Kind.TOKEN_REJECTED, NotionApi.mapError(401, "{}").kind)
        assertEquals(NotionError.Kind.NOT_SHARED, NotionApi.mapError(404, "{}").kind)
        assertEquals(NotionError.Kind.FORBIDDEN, NotionApi.mapError(403, "{}").kind)
        val http = NotionApi.mapError(400, """{"message":"bad property"}""")
        assertEquals(NotionError.Kind.HTTP, http.kind)
        assertEquals("bad property", http.arg)
        assertEquals("Notion returned HTTP 400.", NotionApi.mapError(400, "not json").message)
    }

    @Test
    fun `a 401 response surfaces as TOKEN_REJECTED and is not retried`() = runTest {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"message":"x"}"""))
        val error = errorOf { client.resolveDataSource("db") }
        assertEquals(NotionError.Kind.TOKEN_REJECTED, error.kind)
        assertEquals(401, error.statusCode)
        assertEquals(1, server.requestCount)
        assertTrue(delays.isEmpty())
    }

    @Test
    fun `5xx and 429 are retried with doubling backoff then succeed`() = runTest {
        server.enqueue(MockResponse().setResponseCode(503))
        server.enqueue(MockResponse().setResponseCode(429))
        server.enqueue(ok("""{"id":"db"}"""))
        assertEquals("db", client.resolveDataSource("db"))
        assertEquals(listOf(2000L, 4000L), delays)
    }

    @Test
    fun `persistent 500 gives up after two retries`() = runTest {
        repeat(3) { server.enqueue(MockResponse().setResponseCode(500).setBody("""{"message":"boom"}""")) }
        val error = errorOf { client.resolveDataSource("db") }
        assertEquals(500, error.statusCode)
        assertEquals(3, server.requestCount)
    }

    @Test
    fun `unreadable reply is mapped`() = runTest {
        server.enqueue(ok("<html>"))
        assertEquals(NotionError.Kind.UNREADABLE_REPLY, errorOf { client.resolveDataSource("db") }.kind)
    }

    @Test
    fun `files and title property discovery`() {
        val schema = Json.parseToJsonElement(
            """{"properties":{"Name":{"type":"title"},"Recording":{"type":"files"},"A":{"type":"files"},"X":{"type":"date"}}}""",
        ).jsonObject
        assertEquals(listOf("A", "Recording"), NotionApi.filesPropertyNames(schema))
        assertEquals("Name", NotionApi.titlePropertyName(schema))
    }

    // ----- filing workflow -----

    private fun meetingFiles(notes: String? = "# Notes\n- a", transcript: String? = "[00:00:01] A: hi", audioBytes: Int? = 100) =
        Triple(
            notes?.let { tmp.newFile("notes.md").apply { writeText(it) } },
            transcript?.let { tmp.newFile("transcript.md").apply { writeText(it) } },
            audioBytes?.let { tmp.newFile("recording.m4a").apply { writeBytes(ByteArray(it)) } },
        )

    @Test
    fun `filing writes notes then transcript then attaches audio`() = runTest {
        val (notes, transcript, audio) = meetingFiles()
        server.enqueue(ok("{}"))
        server.enqueue(ok("""{"id":"up-1"}"""))
        server.enqueue(ok("{}"))
        server.enqueue(ok("{}"))
        val progress = mutableListOf<NotionFiling.Progress>()

        val result = NotionFiling.fileMeeting(client, page, notes, transcript, audio, "Recording") { progress += it }

        assertTrue(result.notesWritten && result.transcriptWritten && result.audioAttached)
        assertTrue(result.warnings.isEmpty())
        assertEquals(listOf(NotionFiling.Progress.WRITING_NOTES, NotionFiling.Progress.UPLOADING_AUDIO), progress)
        assertEquals(
            listOf("/v1/blocks/p1/children", "/v1/file_uploads", "/v1/file_uploads/up-1/send", "/v1/pages/p1"),
            List(4) { server.takeRequest().path },
        )
    }

    @Test
    fun `a refused upload keeps the notes and warns`() = runTest {
        val (notes, transcript, audio) = meetingFiles()
        server.enqueue(ok("{}"))
        server.enqueue(MockResponse().setResponseCode(400).setBody("""{"message":"file too big for plan"}"""))

        val result = NotionFiling.fileMeeting(client, page, notes, transcript, audio, "Recording")

        assertTrue(result.notesWritten)
        assertFalse(result.audioAttached)
        val warning = result.warnings.single() as FilingWarning.AudioFailed
        assertEquals("file too big for plan", warning.error.arg)
    }

    @Test
    fun `audio over 20 MiB warns without any upload request`() = runTest {
        val (notes, _, _) = meetingFiles(transcript = null, audioBytes = null)
        val big = tmp.newFile("recording.m4a")
        java.io.RandomAccessFile(big, "rw").use { it.setLength(25L * 1024 * 1024) }
        server.enqueue(ok("{}"))

        val result = NotionFiling.fileMeeting(client, page, notes, null, big, "Recording")

        assertEquals(1, server.requestCount)
        assertEquals(NotionError.Kind.FILE_TOO_LARGE, (result.warnings.single() as FilingWarning.AudioFailed).error.kind)
    }

    @Test
    fun `missing files property skips audio with a warning`() = runTest {
        val (notes, transcript, audio) = meetingFiles()
        server.enqueue(ok("{}"))
        val result = NotionFiling.fileMeeting(client, page, notes, transcript, audio, "")
        assertEquals(listOf<FilingWarning>(FilingWarning.NoFilesProperty), result.warnings)
        assertFalse(result.audioAttached)
        assertEquals(1, server.requestCount)
    }

    @Test
    fun `a meeting with neither notes nor transcript warns and sends nothing`() = runTest {
        val result = NotionFiling.fileMeeting(client, page, File(tmp.root, "none.md"), null, null, "Recording")
        assertEquals(listOf<FilingWarning>(FilingWarning.NothingToFile), result.warnings)
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `a notes failure propagates`() = runTest {
        val (notes, transcript, audio) = meetingFiles()
        server.enqueue(MockResponse().setResponseCode(404).setBody("{}"))
        val error = errorOf { NotionFiling.fileMeeting(client, page, notes, transcript, audio, "Recording") }
        assertEquals(NotionError.Kind.NOT_SHARED, error.kind)
    }
}
