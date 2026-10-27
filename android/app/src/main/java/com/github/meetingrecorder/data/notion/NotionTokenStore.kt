package com.github.meetingrecorder.data.notion

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.security.KeyStore

/** Holds Notion integration tokens, keyed by workspace id. */
interface NotionTokenStore {
    fun get(workspaceId: String): String

    fun set(workspaceId: String, token: String)

    fun remove(workspaceId: String)
}

/** Keystore-backed [NotionTokenStore] using EncryptedSharedPreferences; an unreadable store is wiped and recreated. */
class EncryptedNotionTokenStore(private val context: Context) : NotionTokenStore {
    private val prefs: SharedPreferences by lazy {
        try {
            create()
        } catch (_: Exception) {
            reset()
            create()
        }
    }

    private fun create(): SharedPreferences {
        val key = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
        return EncryptedSharedPreferences.create(
            context,
            PREFS_NAME,
            key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    private fun reset() {
        context.deleteSharedPreferences(PREFS_NAME)
        runCatching {
            KeyStore.getInstance("AndroidKeyStore").apply {
                load(null)
                deleteEntry(MasterKey.DEFAULT_MASTER_KEY_ALIAS)
            }
        }
    }

    override fun get(workspaceId: String): String = prefs.getString(workspaceId, "").orEmpty()

    override fun set(workspaceId: String, token: String) {
        prefs.edit().putString(workspaceId, token.trim()).apply()
    }

    override fun remove(workspaceId: String) {
        prefs.edit().remove(workspaceId).apply()
    }

    private companion object {
        const val PREFS_NAME = "meeting_recorder_notion_tokens"
    }
}
