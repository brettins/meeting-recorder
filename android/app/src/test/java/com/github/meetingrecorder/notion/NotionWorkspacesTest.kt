package com.github.meetingrecorder.notion

import com.github.meetingrecorder.data.notion.NotionPage
import com.github.meetingrecorder.data.notion.NotionPicker
import com.github.meetingrecorder.data.notion.NotionWorkspace
import com.github.meetingrecorder.data.notion.NotionWorkspaces
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

class NotionWorkspacesTest {

    private val hex = "0123456789abcdef0123456789abcdef"
    private val uuid = "01234567-89ab-cdef-0123-456789abcdef"

    @Test
    fun `database id is extracted from share urls`() {
        assertEquals(hex, NotionWorkspaces.extractDatabaseId("https://www.notion.so/team/Meetings-$hex"))
        assertEquals(hex, NotionWorkspaces.extractDatabaseId("https://www.notion.so/$hex?v=fedcba9876543210fedcba9876543210"))
        assertEquals(hex, NotionWorkspaces.extractDatabaseId("https://www.notion.so/$uuid"))
        assertEquals(hex, NotionWorkspaces.extractDatabaseId(hex))
        assertEquals("not a link", NotionWorkspaces.extractDatabaseId(" not a link "))
        assertEquals("", NotionWorkspaces.extractDatabaseId("  "))
    }

    @Test
    fun `link validation`() {
        assertTrue(NotionWorkspaces.isDatabaseLink("https://notion.so/x-$hex"))
        assertFalse(NotionWorkspaces.isDatabaseLink("hello"))
        assertFalse(NotionWorkspaces.isUsable(" ", hex))
    }

    @Test
    fun `ids are slugged and deduplicated`() {
        assertEquals("my-team", NotionWorkspaces.idFor(" My Team! ", emptyList()))
        assertEquals("my-team-2", NotionWorkspaces.idFor("My Team", listOf("my-team")))
        assertEquals("my-team-3", NotionWorkspaces.idFor("My Team", listOf("my-team", "my-team-2")))
        assertEquals("workspace", NotionWorkspaces.idFor("***", emptyList()))
    }

    @Test
    fun `rename keeps the id and a blank link keeps the database`() {
        val added = NotionWorkspaces.add(emptyList(), "Work", "https://notion.so/$hex", " Recording ")
        val id = added.single().id
        assertEquals("Recording", added.single().filesProperty)
        val renamed = NotionWorkspaces.update(added, id, "Job", "", "Audio").single()
        assertEquals(id, renamed.id)
        assertEquals("Job", renamed.name)
        assertEquals(hex, renamed.databaseId)
        assertEquals("Audio", renamed.filesProperty)
    }

    @Test
    fun `remove drops only the named workspace`() {
        val two = NotionWorkspaces.add(NotionWorkspaces.add(emptyList(), "A", hex, ""), "B", hex, "")
        assertEquals(listOf("b"), NotionWorkspaces.remove(two, "a").map { it.id })
    }

    @Test
    fun `encode decode round trips and drops bad entries`() {
        val list = listOf(NotionWorkspace("a", "A", hex, "Rec"))
        assertEquals(list, NotionWorkspaces.decode(NotionWorkspaces.encode(list)))
        val messy = """[{"id":"a","name":"A","databaseId":"$hex"},{"id":"a","name":"Dup","databaseId":"$hex"},""" +
            """{"id":"b","name":"","databaseId":"$hex"}]"""
        assertEquals(listOf("a"), NotionWorkspaces.decode(messy).map { it.id })
        assertTrue(NotionWorkspaces.decode("garbage").isEmpty())
    }

    @Test
    fun `stored workspace json never contains a token field`() {
        assertFalse(NotionWorkspaces.encode(listOf(NotionWorkspace("a", "A", hex))).contains("token"))
    }

    private val pages = listOf(
        NotionPage("1", "Sing Marketing", "2026-10-20", ""),
        NotionPage("2", "Standup", "2026-10-21", ""),
        NotionPage("3", "Retro", null, ""),
    )

    @Test
    fun `filterPages requires every word`() {
        assertEquals(3, NotionPicker.filterPages(pages, "  ").size)
        assertEquals(listOf("1"), NotionPicker.filterPages(pages, "sing  MARKETING").map { it.id })
        assertEquals(listOf("2"), NotionPicker.filterPages(pages, "2026-10-21").map { it.id })
        assertTrue(NotionPicker.filterPages(pages, "sing standup").isEmpty())
    }

    @Test
    fun `suggestPage matches only the same day`() {
        assertEquals("2", NotionPicker.suggestPage(pages, LocalDateTime.of(2026, 10, 21, 9, 0))!!.id)
        assertNull(NotionPicker.suggestPage(pages, LocalDateTime.of(2026, 10, 22, 9, 0)))
    }

    @Test
    fun `default new title prefers the meeting title`() {
        val at = LocalDateTime.of(2026, 10, 21, 9, 5)
        assertEquals("Standup", NotionPicker.defaultNewTitle(" Standup ", at))
        assertEquals("Meeting 2026-10-21 09:05", NotionPicker.defaultNewTitle(null, at))
    }
}
