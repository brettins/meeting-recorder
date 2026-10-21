package com.github.meetingrecorder.data.notion

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/** Holds Notion integration tokens, keyed by workspace id. */
interface NotionTokenStore {
    fun get(workspaceId: String): String

    fun set(workspaceId: String, token: String)

    fun remove(workspaceId: String)
}

/** Keystore-backed [NotionTokenStore] using EncryptedSharedPreferences. */
class EncryptedNotionTokenStore(context: Context) : NotionTokenStore {
    private val prefs: SharedPreferences by lazy {
        val key = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
        EncryptedSharedPreferences.create(
            context,
            "meeting_recorder_notion_tokens",
            key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    override fun get(workspaceId: String): String = prefs.getString(workspaceId, "").orEmpty()

    override fun set(workspaceId: String, token: String) {
        prefs.edit().putString(workspaceId, token.trim()).apply()
    }

    override fun remove(workspaceId: String) {
        prefs.edit().remove(workspaceId).apply()
    }
}
