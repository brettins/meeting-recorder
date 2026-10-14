package com.github.meetingrecorder

import com.github.meetingrecorder.data.FiledMeeting
import com.github.meetingrecorder.data.FiledTask
import com.github.meetingrecorder.data.MaestroFiling
import com.github.meetingrecorder.data.MaestroMeetingPayload
import com.github.meetingrecorder.data.MeetingFiler
import com.github.meetingrecorder.data.MeetingFiling
import com.github.meetingrecorder.data.MeetingRepository
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.time.LocalDateTime
import java.time.ZoneId

class DispatchFilingTest {

    @get:Rule
    val tempDir = TemporaryFolder()

    private val notes = "### Summary\nWe met.\n\n## Action Items\n- [ ] Ring the venue\n- [ ] Send the budget\n"
    private val at1430 = LocalDateTime.of(2026, 9, 23, 14, 30)

    private class FakeFiler(private val reply: FiledMeeting = FiledMeeting("DSP-9", "", created = true)) : MeetingFiler {
        val payloads = mutableListOf<MaestroMeetingPayload>()
        override suspend fun fileMeeting(payload: MaestroMeetingPayload): FiledMeeting {
            payloads += payload
            return reply
        }
    }

    private fun folder(name: String): File = File(tempDir.root, name).apply { mkdirs() }

    private fun meeting(name: String = "2026-09-23_14-30_Standup"): File = folder(name).apply {
        File(this, "notes.md").writeText(notes)
        File(this, "transcript.md").writeText("**Speaker 1:** hello\n")
    }

    // Meeting id

    @Test
    fun `the id is the minute from the folder`() {
        assertEquals("2026-09-23T14:30", MaestroFiling.meetingIdFor("2026-09-23_14-30_Standup", LocalDateTime.of(2000, 1, 1, 0, 0)))
    }

    @Test
    fun `a rename keeps the id`() {
        assertEquals(
            MaestroFiling.meetingIdFor("2026-09-23_14-30", at1430),
            MaestroFiling.meetingIdFor("2026-09-23_14-30_Budget_review", at1430),
        )
    }

    @Test
    fun `an odd folder falls back to the date`() {
        assertEquals("2026-09-23T09:05", MaestroFiling.meetingIdFor("imported", LocalDateTime.of(2026, 9, 23, 9, 5)))
    }

    @Test
    fun `the start time comes from the folder name`() {
        assertEquals(at1430, MaestroFiling.startedAt("2026-09-23_14-30_Standup"))
        assertNull(MaestroFiling.startedAt("2026-13-45_99-99"))
        assertNull(MaestroFiling.startedAt("imported"))
    }

    @Test
    fun `a taken id gets the first free counter`() {
        assertEquals("m", MaestroFiling.unusedId("m", emptySet()))
        assertEquals("m~3", MaestroFiling.unusedId("m", setOf("m", "m~2")))
    }

    @Test
    fun `the first filing takes the minute`() {
        assertEquals("2026-03-01T14:30", MaestroFiling.stableMeetingId(folder("2026-03-01_14-30"), at1430))
    }

    @Test
    fun `a second meeting in the same minute gets its own id`() {
        MaestroFiling.rememberId(folder("2026-03-01_14-30"), "2026-03-01T14:30")
        val second = folder("2026-03-01_14-30_2")
        assertEquals("2026-03-01T14:30~2", MaestroFiling.stableMeetingId(second, at1430))
    }

    @Test
    fun `an id once given survives a rename`() {
        val dir = folder("2026-03-01_14-30_Budget")
        MaestroFiling.rememberId(dir, "2026-03-01T14:30~2")
        assertEquals("2026-03-01T14:30~2", MaestroFiling.stableMeetingId(dir, at1430))
    }

    @Test
    fun `remembering the id keeps every other key`() {
        val dir = folder("2026-03-01_14-30")
        File(dir, "meeting.json").writeText("""{"title":"Budget","duration_seconds":60,"tags":["sing"]}""")
        MaestroFiling.rememberId(dir, "2026-03-01T14:30")
        val json = JSONObject(File(dir, "meeting.json").readText())
        assertEquals("Budget", json.getString("title"))
        assertEquals(60, json.getInt("duration_seconds"))
        assertEquals("sing", json.getJSONArray("tags").getString(0))
        assertEquals("2026-03-01T14:30", json.getString(MaestroFiling.ID_KEY))
    }

    @Test
    fun `renaming keeps the remembered id`() {
        val root = tempDir.newFolder("Meetings")
        val dir = File(root, "2026-03-01_14-30").apply { mkdirs() }
        MaestroFiling.rememberId(dir, "2026-03-01T14:30~2")
        val renamed = MeetingRepository(root).renameMeeting(dir, "Budget")
        assertEquals("2026-03-01T14:30~2", MaestroFiling.stableMeetingId(renamed, at1430))
    }

    // Payload

    @Test
    fun `the payload carries everything Maestro takes`() {
        val payload = MaestroFiling.buildPayload(
            meetingId = "2026-09-23T14:30",
            title = "Standup",
            date = at1430,
            durationSeconds = 900,
            notes = "n",
            transcript = "t",
            actions = listOf("Ring"),
            company = " Maestro ",
            zone = ZoneId.of("America/Toronto"),
        )
        assertEquals("2026-09-23T14:30", payload.meetingId)
        assertEquals("Standup", payload.title)
        assertEquals("2026-09-23T14:30-04:00", payload.date)
        assertEquals(900, payload.durationSeconds)
        assertEquals("n", payload.notes)
        assertEquals("t", payload.transcript)
        assertEquals(listOf("Ring"), payload.actions)
        assertEquals("Maestro", payload.company)
    }

    @Test
    fun `utc still writes an offset`() {
        assertEquals("2026-09-23T14:30+00:00", MaestroFiling.isoDate(at1430, ZoneId.of("UTC")))
    }

    @Test
    fun `no company is left out of the wire body`() {
        val payload = MaestroFiling.buildPayload("m", "t", at1430, 1, "", "", emptyList(), company = "  ")
        assertNull(payload.company)
        val wire = JSONObject(Json.encodeToString(MaestroMeetingPayload.serializer(), payload))
        assertFalse(wire.has("company"))
        assertEquals("m", wire.getString("meeting_id"))
        assertEquals(1, wire.getInt("duration_seconds"))
    }

    @Test
    fun `an unknown duration is zero`() {
        assertEquals(0, MaestroFiling.buildPayload("m", "t", at1430, null, "", "", emptyList()).durationSeconds)
    }

    @Test
    fun `an unnamed meeting is named by its time`() {
        assertEquals("Meeting Sep 23, 2026 14:30", MaestroFiling.meetingTitle(null, at1430))
        assertEquals("Meeting Sep 03, 2026 14:30", MaestroFiling.meetingTitle("  ", at1430.withDayOfMonth(3)))
    }

    // Filing

    @Test
    fun `the notes' action lines are sent`() = runTest {
        val filer = FakeFiler()
        testFiling().file(filer, meeting(), title = "Standup", durationSeconds = 900)
        assertEquals(listOf("Ring the venue", "Send the budget"), filer.payloads[0].actions)
        assertEquals("**Speaker 1:** hello\n", filer.payloads[0].transcript)
        assertEquals("2026-09-23T14:30", filer.payloads[0].meetingId)
    }

    @Test
    fun `chosen lines replace the parsed ones`() = runTest {
        val filer = FakeFiler()
        testFiling().file(filer, meeting(), title = null, durationSeconds = null, actions = listOf("Send the budget"))
        assertEquals(listOf("Send the budget"), filer.payloads[0].actions)
    }

    @Test
    fun `a meeting without notes still files`() = runTest {
        val dir = meeting().apply { File(this, "notes.md").delete() }
        val filer = FakeFiler()
        testFiling().file(filer, dir, title = null, durationSeconds = null)
        assertEquals("", filer.payloads[0].notes)
        assertEquals(emptyList<String>(), filer.payloads[0].actions)
    }

    @Test
    fun `filing writes the id down`() = runTest {
        val dir = meeting("2026-03-01_14-30")
        testFiling().file(FakeFiler(), dir, title = null, durationSeconds = 60)
        assertEquals("2026-03-01T14:30", JSONObject(File(dir, "meeting.json").readText()).getString(MaestroFiling.ID_KEY))
    }

    @Test
    fun `a refused filing writes nothing down`() = runTest {
        val dir = meeting("2026-03-01_14-30")
        val failing = MeetingFiler { throw IllegalStateException("refused") }
        runCatching { testFiling().file(failing, dir, title = null, durationSeconds = 60) }
        assertFalse(File(dir, "meeting.json").exists())
    }

    private fun TestScope.testFiling() =
        MeetingFiling(ioDispatcher = StandardTestDispatcher(testScheduler))

    // Summary

    private fun filed(created: Boolean, vararg flags: Boolean) = FiledMeeting(
        key = "DSP-9",
        url = "",
        created = created,
        tasks = flags.mapIndexed { n, flag -> FiledTask("D-$n", "t", flag) },
    )

    @Test
    fun `a first filing`() {
        assertEquals("Filed into Maestro as DSP-9, with 2 draft tasks in Sort.", MaestroFiling.filedSummary(filed(true, true, true)))
    }

    @Test
    fun `one task is singular`() {
        assertTrue(MaestroFiling.filedSummary(filed(true, true)).contains("1 draft task in"))
    }

    @Test
    fun `no actions`() {
        assertTrue(MaestroFiling.filedSummary(filed(true)).contains("no draft tasks"))
    }

    @Test
    fun `refiling with a new line`() {
        assertEquals("Updated DSP-9 in Maestro, and added 1 draft task to Sort.", MaestroFiling.filedSummary(filed(false, false, true)))
    }

    @Test
    fun `refiling with nothing new`() {
        assertTrue(MaestroFiling.filedSummary(filed(false, false)).contains("already there"))
    }

    @Test
    fun `refiling a meeting without tasks`() {
        assertEquals("Updated DSP-9 in Maestro.", MaestroFiling.filedSummary(filed(false)))
    }
}
