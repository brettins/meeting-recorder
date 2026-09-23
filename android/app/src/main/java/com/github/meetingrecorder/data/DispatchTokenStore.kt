// security-crypto deprecated its whole API with no replacement inside the library.
@file:Suppress("DEPRECATION")

package com.github.meetingrecorder.data

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/** Where the Dispatch API token is kept, apart from the plain settings. */
interface DispatchTokenStore {
    fun get(): String
    fun set(token: String): Boolean
    fun clear()
}

/** The token in EncryptedSharedPreferences, keyed by the Android Keystore. */
class EncryptedDispatchTokenStore(private val context: Context) : DispatchTokenStore {

    private val prefs: SharedPreferences? by lazy { open() }

    override fun get(): String = prefs?.getString(KEY, "").orEmpty()

    override fun set(token: String): Boolean {
        val store = prefs ?: return false
        return store.edit().putString(KEY, token.trim()).commit()
    }

    override fun clear() {
        prefs?.edit()?.remove(KEY)?.commit()
    }

    private fun open(): SharedPreferences? = try {
        val key = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
        EncryptedSharedPreferences.create(
            context,
            FILE,
            key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    } catch (e: Exception) {
        Log.w(TAG, "Could not open the encrypted Dispatch token store: ${e.javaClass.simpleName}")
        null
    }

    private companion object {
        const val FILE = "dispatch_secrets"
        const val KEY = "dispatch_api_token"
        const val TAG = "DispatchTokenStore"
    }
}
