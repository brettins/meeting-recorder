package com.github.meetingrecorder.ui.detail

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.github.meetingrecorder.R
import com.github.meetingrecorder.appViewModelFactory
import com.github.meetingrecorder.data.notion.FilingResult
import com.github.meetingrecorder.data.notion.FilingWarning
import com.github.meetingrecorder.data.notion.NotionApi
import com.github.meetingrecorder.data.notion.NotionError
import com.github.meetingrecorder.data.notion.NotionFiling
import com.github.meetingrecorder.data.notion.NotionPicker

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NotionFilingDialog(
    meetingPath: String,
    onDismiss: () -> Unit,
    viewModel: NotionFilingViewModel = viewModel(factory = appViewModelFactory),
) {
    LaunchedEffect(meetingPath) { viewModel.open(meetingPath) }
    val ui by viewModel.ui.collectAsState()
    var query by rememberSaveable { mutableStateOf("") }
    var selectedPageId by rememberSaveable { mutableStateOf<String?>(null) }
    var creating by rememberSaveable { mutableStateOf(false) }
    var newTitle by rememberSaveable(ui.suggestedTitle) { mutableStateOf(ui.suggestedTitle) }
    var workspaceMenu by remember { mutableStateOf(false) }

    val loaded = ui.pages as? PagesState.Loaded
    val visiblePages = loaded?.let { NotionPicker.filterPages(it.pages, query) }.orEmpty()
    val suggestion = ui.recorded?.let { NotionPicker.suggestPage(visiblePages, it) }
    LaunchedEffect(suggestion?.id, loaded?.dataSourceId) {
        if (selectedPageId == null) selectedPageId = suggestion?.id
    }

    val working = ui.filing is FilingState.Working
    val done = ui.filing is FilingState.Done
    val canFile = loaded != null && !working && !done &&
        (if (creating) newTitle.isNotBlank() else selectedPageId != null)

    AlertDialog(
        onDismissRequest = { if (!working) onDismiss() },
        title = { Text(stringResource(R.string.action_file_into_notion)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (ui.workspaces.isEmpty()) {
                    Text(stringResource(R.string.notion_no_workspace))
                    return@Column
                }
                if (ui.workspaces.size > 1) {
                    ExposedDropdownMenuBox(expanded = workspaceMenu, onExpandedChange = { workspaceMenu = it }) {
                        OutlinedTextField(
                            value = ui.selected?.name.orEmpty(),
                            onValueChange = {},
                            readOnly = true,
                            label = { Text(stringResource(R.string.notion_workspace)) },
                            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(workspaceMenu) },
                            modifier = Modifier.fillMaxWidth().menuAnchor(MenuAnchorType.PrimaryNotEditable),
                            enabled = !working,
                        )
                        ExposedDropdownMenu(expanded = workspaceMenu, onDismissRequest = { workspaceMenu = false }) {
                            ui.workspaces.forEach { workspace ->
                                DropdownMenuItem(
                                    text = { Text(workspace.name) },
                                    onClick = {
                                        workspaceMenu = false
                                        selectedPageId = null
                                        viewModel.selectWorkspace(workspace)
                                    },
                                )
                            }
                        }
                    }
                }
                when (val pages = ui.pages) {
                    PagesState.Idle, PagesState.Loading -> Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(Modifier.padding(end = 12.dp))
                        Text(stringResource(R.string.notion_loading_pages, ui.selected?.name.orEmpty()))
                    }
                    is PagesState.Failed -> {
                        ErrorLine(notionErrorText(pages.error))
                        TextButton(onClick = viewModel::loadPages) { Text(stringResource(R.string.action_retry)) }
                    }
                    is PagesState.Loaded -> {
                        if (NotionApi.exceedsFreePlan(ui.audioBytes)) {
                            Text(
                                stringResource(R.string.notion_free_plan_hint, "%.1f".format(ui.audioBytes / (1024.0 * 1024.0))),
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                        if (creating) {
                            OutlinedTextField(
                                value = newTitle,
                                onValueChange = { newTitle = it },
                                label = { Text(stringResource(R.string.notion_new_page_title)) },
                                singleLine = true,
                                enabled = !working && !done,
                                modifier = Modifier.fillMaxWidth(),
                            )
                        } else {
                            OutlinedTextField(
                                value = query,
                                onValueChange = { query = it },
                                label = { Text(stringResource(R.string.notion_search_pages)) },
                                singleLine = true,
                                enabled = !working && !done,
                                modifier = Modifier.fillMaxWidth(),
                            )
                            if (visiblePages.isEmpty()) {
                                Text(stringResource(R.string.notion_no_pages), style = MaterialTheme.typography.bodySmall)
                            }
                            LazyColumn(Modifier.heightIn(max = 240.dp)) {
                                items(visiblePages, key = { it.id }) { page ->
                                    Row(
                                        Modifier
                                            .fillMaxWidth()
                                            .clickable(enabled = !working && !done) { selectedPageId = page.id },
                                        verticalAlignment = Alignment.CenterVertically,
                                    ) {
                                        RadioButton(selected = selectedPageId == page.id, onClick = null, modifier = Modifier.padding(8.dp))
                                        Column(Modifier.weight(1f)) {
                                            Text(page.title, style = MaterialTheme.typography.bodyMedium)
                                            val subtitle = page.date ?: stringResource(R.string.notion_no_date)
                                            val label = if (page.id == suggestion?.id) {
                                                "$subtitle · ${stringResource(R.string.notion_same_day)}"
                                            } else {
                                                subtitle
                                            }
                                            Text(label, style = MaterialTheme.typography.bodySmall)
                                        }
                                    }
                                }
                            }
                        }
                        if (!working && !done) {
                            TextButton(onClick = { creating = !creating }) {
                                Text(
                                    stringResource(
                                        if (creating) R.string.notion_choose_instead else R.string.notion_create_instead,
                                    ),
                                )
                            }
                        }
                    }
                }
                when (val filing = ui.filing) {
                    FilingState.Idle -> {}
                    is FilingState.Working -> Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(Modifier.padding(end = 12.dp))
                        Text(stringResource(progressText(filing.progress)))
                    }
                    is FilingState.Failed -> ErrorLine(notionErrorText(filing.error))
                    is FilingState.Done -> Text(filedSummary(filing.result))
                }
            }
        },
        confirmButton = {
            if (done) {
                TextButton(onClick = onDismiss) { Text(stringResource(R.string.action_close)) }
            } else {
                TextButton(
                    enabled = canFile,
                    onClick = {
                        val page = if (creating) null else loaded?.pages?.firstOrNull { it.id == selectedPageId }
                        viewModel.file(page, newTitle)
                    },
                ) {
                    Text(stringResource(if (creating) R.string.notion_create_and_file else R.string.notion_file_here))
                }
            }
        },
        dismissButton = {
            if (!done) {
                TextButton(enabled = !working, onClick = onDismiss) { Text(stringResource(R.string.action_cancel)) }
            }
        },
    )
}

@Composable
private fun ErrorLine(text: String) {
    Text(text, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
}

private fun progressText(progress: NotionFiling.Progress): Int = when (progress) {
    NotionFiling.Progress.LOADING_PAGES -> R.string.notion_progress_filing
    NotionFiling.Progress.WRITING_NOTES -> R.string.notion_progress_writing
    NotionFiling.Progress.UPLOADING_AUDIO -> R.string.notion_progress_uploading
}

@Composable
private fun filedSummary(result: FilingResult): String {
    val parts = buildList {
        if (result.notesWritten) add(stringResource(R.string.notion_part_notes))
        if (result.transcriptWritten) add(stringResource(R.string.notion_part_transcript))
        if (result.audioAttached) add(stringResource(R.string.notion_part_recording))
    }
    val summary = when {
        parts.isEmpty() -> stringResource(R.string.notion_filed_nothing, result.page.title)
        else -> {
            val listed = if (parts.size == 1) {
                parts.single()
            } else {
                stringResource(R.string.notion_list_and, parts.dropLast(1).joinToString(", "), parts.last())
            }
            stringResource(R.string.notion_filed_summary, listed, result.page.title)
        }
    }
    val warnings = result.warnings.map {
        when (it) {
            FilingWarning.NothingToFile -> stringResource(R.string.notion_warn_nothing_to_file)
            FilingWarning.NoFilesProperty -> stringResource(R.string.notion_warn_no_files_property)
            is FilingWarning.AudioFailed -> stringResource(R.string.notion_warn_audio_failed, notionErrorText(it.error))
        }
    }
    return (listOf(summary) + warnings).joinToString(" ")
}

@Composable
fun notionErrorText(error: NotionError): String = when (error.kind) {
    NotionError.Kind.TOKEN_REJECTED -> stringResource(R.string.notion_err_token_rejected)
    NotionError.Kind.NOT_SHARED -> stringResource(R.string.notion_err_not_shared)
    NotionError.Kind.FORBIDDEN -> stringResource(R.string.notion_err_forbidden)
    NotionError.Kind.UNREADABLE_REPLY -> stringResource(R.string.notion_err_unreadable)
    NotionError.Kind.NETWORK -> stringResource(R.string.notion_err_network, error.arg)
    NotionError.Kind.FILE_TOO_LARGE -> stringResource(R.string.notion_err_file_too_large, error.arg)
    NotionError.Kind.NO_UPLOAD_TARGET -> stringResource(R.string.notion_err_no_upload_target)
    NotionError.Kind.NO_TOKEN -> stringResource(R.string.notion_err_no_token, error.arg)
    NotionError.Kind.HTTP -> error.arg.ifEmpty { stringResource(R.string.notion_err_http, error.statusCode ?: 0) }
}
