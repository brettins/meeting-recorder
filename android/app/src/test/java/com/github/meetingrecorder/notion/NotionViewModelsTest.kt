package com.github.meetingrecorder.notion

import com.github.meetingrecorder.data.Config
import com.github.meetingrecorder.data.MeetingRepository
import com.github.meetingrecorder.data.notion.NotionError
import com.github.meetingrecorder.data.notion.NotionTokenStore
import com.github.meetingrecorder.data.notion.NotionWorkspace
import com.github.meetingrecorder.ui.detail.FilingState
import com.github.meetingrecorder.ui.detail.NotionFilingViewModel
import com.github.meetingrecorder.ui.detail.PagesState
import com.github.meetingrecorder.ui.settings.SettingsViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.mockito.kotlin.mock
import org.mockito.kotlin.whenever

@OptIn(ExperimentalCoroutinesApi::class)
class NotionViewModelsTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val hex = "0123456789abcdef0123456789abcdef"

    private class FakeTokens(var failing: Boolean = false) : NotionTokenStore {
        val map = mutableMapOf<String, String>()

        override fun get(workspaceId: String): String {
            if (failing) throw java.security.GeneralSecurityException("bad keyset")
            return map[workspaceId].orEmpty()
        }

        override fun set(workspaceId: String, token: String) {
            if (failing) throw java.security.GeneralSecurityException("bad keyset")
            map[workspaceId] = token
        }

        override fun remove(workspaceId: String) {
            if (failing) throw java.security.GeneralSecurityException("bad keyset")
            map.remove(workspaceId)
        }
    }

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
    }

    @After
    fun tearDown() {
        Thread.sleep(300)
        Dispatchers.resetMain()
    }

    private fun awaitTrue(condition: () -> Boolean) {
        val deadline = System.currentTimeMillis() + 5_000
        while (!condition() && System.currentTimeMillis() < deadline) Thread.sleep(10)
        assertTrue("condition not met in time", condition())
    }

    private fun config(stored: List<NotionWorkspace>): Config =
        mock<Config>().also { whenever(it.notionWorkspaces).thenReturn(stored) }

    @Test
    fun `settings keeps the workspace out of the list when the token cannot be stored`() {
        val tokens = FakeTokens(failing = true)
        val vm = SettingsViewModel(mock<Config>().also { whenever(it.notionWorkspaces).thenReturn(emptyList()) }, tokens)

        vm.addNotionWorkspace("Work", hex, "secret", "Recording")

        awaitTrue { vm.notionTokenError.value }
        assertTrue(vm.notionWorkspaces.value.isEmpty())
    }

    @Test
    fun `settings stores the token and the workspace together`() {
        val tokens = FakeTokens()
        val vm = SettingsViewModel(mock<Config>().also { whenever(it.notionWorkspaces).thenReturn(emptyList()) }, tokens)

        vm.addNotionWorkspace("Work", hex, "secret", "Recording")

        awaitTrue { vm.notionWorkspaces.value.isNotEmpty() && vm.notionTokenPresent.value.isNotEmpty() }
        assertFalse(vm.notionTokenError.value)
        assertEquals(listOf("work"), vm.notionWorkspaces.value.map { it.id })
        assertEquals("secret", tokens.map["work"])
        assertEquals(setOf("work"), vm.notionTokenPresent.value)
    }

    @Test
    fun `filing reports a token store failure distinctly`() {
        val ws = NotionWorkspace("work", "Work", hex, "Recording")
        val dir = tmp.newFolder("2026-10-21_09-00")
        java.io.File(dir, "notes.md").writeText("# n")
        val vm = NotionFilingViewModel(config(listOf(ws)), FakeTokens(failing = true), MeetingRepository(tmp.root))

        vm.open(dir.absolutePath)

        awaitTrue { vm.ui.value.pages is PagesState.Failed }
        val pages = vm.ui.value.pages as PagesState.Failed
        assertEquals(NotionError.Kind.TOKEN_STORE, pages.error.kind)
    }

    @Test
    fun `close resets state and reopening re-reads the workspaces`() {
        val cfgList = mutableListOf(NotionWorkspace("a", "A", hex))
        val cfg = mock<Config>().also { whenever(it.notionWorkspaces).thenAnswer { cfgList.toList() } }
        val dir = tmp.newFolder("2026-10-21_09-00")
        val vm = NotionFilingViewModel(cfg, FakeTokens(), MeetingRepository(tmp.root))

        vm.open(dir.absolutePath)
        awaitTrue { vm.ui.value.workspaces.isNotEmpty() }
        assertEquals(listOf("a"), vm.ui.value.workspaces.map { it.id })

        cfgList.add(NotionWorkspace("b", "B", hex))
        vm.open(dir.absolutePath)
        assertEquals(listOf("a"), vm.ui.value.workspaces.map { it.id })

        vm.close()
        assertTrue(vm.ui.value.workspaces.isEmpty())
        assertEquals(FilingState.Idle, vm.ui.value.filing)

        vm.open(dir.absolutePath)
        awaitTrue { vm.ui.value.workspaces.isNotEmpty() }
        assertEquals(listOf("a", "b"), vm.ui.value.workspaces.map { it.id })
    }
}
