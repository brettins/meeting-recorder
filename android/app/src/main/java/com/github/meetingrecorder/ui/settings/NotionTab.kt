package com.github.meetingrecorder.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.github.meetingrecorder.R
import com.github.meetingrecorder.data.notion.NotionWorkspace
import com.github.meetingrecorder.data.notion.NotionWorkspaces

private const val DEFAULT_FILES_PROPERTY = "Recording"

@Composable
internal fun NotionTab(viewModel: SettingsViewModel) {
    val workspaces by viewModel.notionWorkspaces.collectAsState()
    val tokenPresent by viewModel.notionTokenPresent.collectAsState()
    var editing by rememberSaveable { mutableStateOf<String?>(null) }
    var adding by rememberSaveable { mutableStateOf(false) }
    var deleting by rememberSaveable { mutableStateOf<String?>(null) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(stringResource(R.string.notion_settings_intro), style = MaterialTheme.typography.bodyMedium)
        if (workspaces.isEmpty()) {
            Text(stringResource(R.string.notion_no_workspaces), style = MaterialTheme.typography.bodySmall)
        }
        workspaces.forEach { workspace ->
            HorizontalDivider()
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(workspace.name, style = MaterialTheme.typography.titleMedium)
                    val tokenLabel = if (workspace.id in tokenPresent) {
                        R.string.notion_token_stored
                    } else {
                        R.string.notion_token_missing
                    }
                    Text(stringResource(tokenLabel), style = MaterialTheme.typography.bodySmall)
                }
                TextButton(onClick = { editing = workspace.id }) { Text(stringResource(R.string.action_edit)) }
                TextButton(onClick = { deleting = workspace.id }) { Text(stringResource(R.string.action_delete)) }
            }
        }
        HorizontalDivider()
        Button(onClick = { adding = true }, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.notion_add_workspace))
        }
    }

    if (adding) {
        WorkspaceDialog(
            existing = null,
            onDismiss = { adding = false },
            onSave = { name, link, token, files ->
                viewModel.addNotionWorkspace(name, link, token, files)
                adding = false
            },
        )
    }
    workspaces.firstOrNull { it.id == editing }?.let { workspace ->
        WorkspaceDialog(
            existing = workspace,
            onDismiss = { editing = null },
            onSave = { name, link, token, files ->
                viewModel.updateNotionWorkspace(workspace.id, name, link, token, files)
                editing = null
            },
        )
    }
    workspaces.firstOrNull { it.id == deleting }?.let { workspace ->
        AlertDialog(
            onDismissRequest = { deleting = null },
            text = { Text(stringResource(R.string.notion_confirm_delete, workspace.name)) },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.removeNotionWorkspace(workspace.id)
                    deleting = null
                }) { Text(stringResource(R.string.action_delete)) }
            },
            dismissButton = { TextButton(onClick = { deleting = null }) { Text(stringResource(R.string.action_cancel)) } },
        )
    }
}

@Composable
private fun WorkspaceDialog(
    existing: NotionWorkspace?,
    onDismiss: () -> Unit,
    onSave: (name: String, link: String, token: String, filesProperty: String) -> Unit,
) {
    var name by rememberSaveable { mutableStateOf(existing?.name.orEmpty()) }
    var link by rememberSaveable { mutableStateOf(existing?.databaseId.orEmpty()) }
    var token by rememberSaveable { mutableStateOf("") }
    var files by rememberSaveable { mutableStateOf(existing?.filesProperty ?: DEFAULT_FILES_PROPERTY) }
    val linkValid = NotionWorkspaces.isUsable(name, link)

    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(stringResource(if (existing == null) R.string.notion_add_workspace else R.string.notion_edit_workspace))
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text(stringResource(R.string.notion_label_name)) },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = link,
                    onValueChange = { link = it },
                    label = { Text(stringResource(R.string.notion_label_database_link)) },
                    isError = link.isNotBlank() && !NotionWorkspaces.isDatabaseLink(link),
                    supportingText = {
                        if (link.isNotBlank() && !NotionWorkspaces.isDatabaseLink(link)) {
                            Text(stringResource(R.string.notion_database_link_invalid))
                        }
                    },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = token,
                    onValueChange = { token = it },
                    label = { Text(stringResource(R.string.notion_label_token)) },
                    supportingText = { if (existing != null) Text(stringResource(R.string.notion_hint_token_keep)) },
                    visualTransformation = PasswordVisualTransformation(),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = files,
                    onValueChange = { files = it },
                    label = { Text(stringResource(R.string.notion_label_files_property)) },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(
                enabled = linkValid && (existing != null || token.isNotBlank()),
                onClick = { onSave(name, link, token, files) },
            ) { Text(stringResource(R.string.action_save)) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.action_cancel)) } },
    )
}
