package com.github.meetingrecorder

import com.github.meetingrecorder.data.MaestroClient
import com.github.meetingrecorder.data.MaestroException
import com.github.meetingrecorder.data.MaestroMeetingPayload
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

class MaestroClientTest {

    private val server = MockWebServer()
    private lateinit var base: String
    private val delays = mutableListOf<Long>()

    private val reply = """
        {"id": 650, "key": "SNG-1", "url": "/items/650", "created": true,
         "tasks": [
           {"id": 651, "key": "SNG-2", "title": "Ring the venue", "created": true},
           {"id": 652, "key": "SNG-3", "title": "Send the budget", "created": false}
         ]}
    """.trimIndent()

    private val payload = MaestroMeetingPayload(
        meetingId = "m",
        title = "Standup",
        date = "2026-09-23T14:30-04:00",
        durationSeconds = 900,
        notes = "n",
        transcript = "t",
        actions = listOf("Ring the venue"),
    )

    @Before
    fun setUp() {
        server.start()
        base = "http://127.0.0.1:${server.port}/"
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun client(token: String? = null) = MaestroClient(base, token, delayFn = { delays += it })

    private fun json(body: String, code: Int = 200) =
        MockResponse().setResponseCode(code).setHeader("Content-Type", "application/json").setBody(body)

    private suspend fun failure(block: suspend () -> Unit): MaestroException {
        try {
            block()
        } catch (e: MaestroException) {
            return e
        }
        fail("expected a MaestroException")
        throw AssertionError()
    }

    @Test
    fun `it posts json to the meetings endpoint`() = runTest {
        server.enqueue(json(reply))
        client().fileMeeting(payload)
        val request = server.takeRequest()
        assertEquals("/api/meetings", request.path)
        assertEquals("POST", request.method)
        val body = JSONObject(request.body.readUtf8())
        assertEquals("m", body.getString("meeting_id"))
        assertEquals(900, body.getInt("duration_seconds"))
        assertEquals("Ring the venue", body.getJSONArray("actions").getString(0))
        assertFalse(body.has("company"))
        assertTrue(request.getHeader("Content-Type")!!.startsWith("application/json"))
    }

    @Test
    fun `a company is sent when there is one`() = runTest {
        server.enqueue(json(reply))
        client().fileMeeting(payload.copy(company = "Maestro"))
        assertEquals("Maestro", JSONObject(server.takeRequest().body.readUtf8()).getString("company"))
    }

    @Test
    fun `no token means no authorization header`() = runTest {
        server.enqueue(json(reply))
        client().fileMeeting(payload)
        assertNull(server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun `a given token is a bearer header`() = runTest {
        server.enqueue(json(reply))
        client("tok-123").fileMeeting(payload)
        assertEquals("Bearer tok-123", server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun `the reply is read`() = runTest {
        server.enqueue(json(reply))
        val filed = client().fileMeeting(payload)
        assertEquals("SNG-1", filed.key)
        assertEquals("http://127.0.0.1:${server.port}/items/650", filed.url)
        assertEquals(listOf("SNG-2", "SNG-3"), filed.tasks.map { it.key })
        assertEquals(1, filed.newTasks)
        assertTrue(filed.created)
    }

    @Test
    fun `a server error is retried`() = runTest {
        server.enqueue(json("{}", 502))
        server.enqueue(json(reply))
        assertEquals("SNG-1", client().fileMeeting(payload).key)
        assertEquals(2, server.requestCount)
        assertEquals(listOf(2_000L), delays)
    }

    @Test
    fun `a refusal is not retried`() = runTest {
        server.enqueue(json("""{"detail": "That token is wrong."}""", 401))
        server.enqueue(json(reply))
        val e = failure { client().fileMeeting(payload) }
        assertTrue(e.message!!.contains("tailnet"))
        assertEquals(401, e.statusCode)
        assertEquals(1, server.requestCount)
    }

    @Test
    fun `a bad request carries Maestro's reason`() = runTest {
        val detail = "No company called Nobody. One of: Maestro, Personal."
        server.enqueue(json("""{"detail": "$detail"}""", 400))
        assertEquals(detail, failure { client().fileMeeting(payload) }.message)
    }

    @Test
    fun `an unreachable server says so after three tries`() = runTest {
        repeat(3) { server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START)) }
        val e = failure { client().fileMeeting(payload) }
        assertTrue(e.message!!.startsWith("Could not reach Maestro"))
        assertEquals(listOf(2_000L, 4_000L), delays)
    }

    @Test
    fun `a reply that is not json is an error`() = runTest {
        server.enqueue(MockResponse().setBody("<html>proxy</html>"))
        assertTrue(failure { client().fileMeeting(payload) }.message!!.contains("could not read"))
    }

    @Test
    fun `the token never appears in an error`() = runTest {
        server.enqueue(json("{}", 401))
        assertFalse(failure { client("tok-123").fileMeeting(payload) }.message!!.contains("tok-123"))
    }

    @Test
    fun `401 points at the tailnet`() {
        assertTrue(MaestroClient.errorMessage(401, "{}").contains("tailnet"))
    }

    @Test
    fun `404 says nothing files meetings there`() {
        assertTrue(MaestroClient.errorMessage(404, "").contains("files meetings"))
    }

    @Test
    fun `the default address is the tailnet one`() {
        assertEquals("https://maestro.tailefa891.ts.net", MaestroClient.DEFAULT_URL)
    }

    @Test
    fun `an unreadable body falls back to the status`() {
        assertEquals("Maestro returned HTTP 418.", MaestroClient.errorMessage(418, "teapot"))
    }

    @Test
    fun `an absolute url is kept`() {
        val body = Json.parseToJsonElement("""{"url": "https://x/items/1"}""").jsonObject
        assertEquals("https://x/items/1", MaestroClient.parseFiled(body, "http://d").url)
    }

    @Test
    fun `malformed tasks are skipped`() {
        val body = Json.parseToJsonElement("""{"tasks": ["junk", {"key": "A-1"}]}""").jsonObject
        assertEquals(listOf("A-1"), MaestroClient.parseFiled(body, "http://d").tasks.map { it.key })
    }
}
