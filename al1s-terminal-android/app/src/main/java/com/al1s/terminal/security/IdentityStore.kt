package com.al1s.terminal.security

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class TerminalIdentity(
    val installationId: String,
    val terminalId: String,
    val targetDeviceId: String,
    val credential: String,
    val terminalRowVersion: Int,
    val capabilityRevision: Int,
)

class IdentityStore(context: Context) : RegistrationIdentityStore {
    private val preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
    fun needsOwnedState():Boolean = preferences.getString("owned-state-terminal",null) != load()?.terminalId
    fun acceptanceStatus():String = preferences.getString("owned-acceptance", "accepting").orEmpty()
    @Synchronized fun updateOwnedState(value:org.json.JSONObject) {
        val current=load() ?: return
        require(value.getString("terminal_id")==current.terminalId)
        val version=value.getInt("row_version")
        if(version<current.terminalRowVersion)return
        check(preferences.edit().putInt(KEY_ROW_VERSION,version)
            .putString("owned-state-terminal",current.terminalId)
            .putString("owned-acceptance",value.getString("acceptance_status")).commit())
    }

    override fun installationId(): String =
        preferences.getString(KEY_INSTALLATION_ID, null)
            ?: UUID.randomUUID().toString().also {
                preferences.edit().putString(KEY_INSTALLATION_ID, it).apply()
            }

    fun configuration(): Pair<String, String>? {
        val baseUrl = preferences.getString(KEY_BASE_URL, null) ?: return null
        val displayName = preferences.getString(KEY_DISPLAY_NAME, null) ?: return null
        return baseUrl to displayName
    }

    fun load(): TerminalIdentity? {
        val terminalId = preferences.getString(KEY_TERMINAL_ID, null) ?: return null
        val targetDeviceId = preferences.getString(KEY_TARGET_DEVICE_ID, null) ?: return null
        val encrypted = preferences.getString(KEY_CREDENTIAL, null) ?: return null
        return TerminalIdentity(
            installationId = installationId(),
            terminalId = terminalId,
            targetDeviceId = targetDeviceId,
            credential = decrypt(encrypted),
            terminalRowVersion = preferences.getInt(KEY_ROW_VERSION, 1),
            capabilityRevision = preferences.getInt(KEY_CAPABILITY_REVISION, 0),
        )
    }

    override fun saveRegistration(
        baseUrl: String,
        displayName: String,
        terminalId: String,
        targetDeviceId: String,
        credential: String,
        terminalRowVersion: Int,
    ) = TerminalConnectionGate.commitConnection {
        val previous = preferences.all
        val committed = preferences.edit()
            .putString(KEY_BASE_URL, baseUrl.trim().trimEnd('/'))
            .putString(KEY_DISPLAY_NAME, displayName.trim())
            .putString(KEY_TERMINAL_ID, terminalId)
            .putString(KEY_TARGET_DEVICE_ID, targetDeviceId)
            .putString(KEY_CREDENTIAL, encrypt(credential))
            .putInt(KEY_ROW_VERSION, terminalRowVersion)
            .putInt(KEY_CAPABILITY_REVISION, 0)
            .remove("capability-fingerprint")
            .remove("owned-state-terminal")
            .commit()
        if (!committed) {
            // SharedPreferences updates memory before its disk commit finishes.
            // Restore the old complete bundle on failure, never a mixed origin/identity.
            val restore = preferences.edit().clear()
            for ((key, value) in previous) {
                when (value) {
                    is String -> restore.putString(key, value)
                    is Int -> restore.putInt(key, value)
                    is Long -> restore.putLong(key, value)
                    is Boolean -> restore.putBoolean(key, value)
                    is Float -> restore.putFloat(key, value)
                    is Set<*> -> restore.putStringSet(key, value.filterIsInstance<String>().toSet())
                }
            }
            restore.commit()
            error("注册连接保存失败，原连接已保留")
        }
    }

    @Synchronized fun updateTerminalRowVersion(rowVersion: Int) {
        if(rowVersion<preferences.getInt(KEY_ROW_VERSION,1))return
        preferences.edit().putInt(KEY_ROW_VERSION, rowVersion).apply()
    }

    fun updateCapabilityRevision(revision: Int) {
        preferences.edit().putInt(KEY_CAPABILITY_REVISION, revision).apply()
    }
    fun capabilityFingerprint():String?=preferences.getString("capability-fingerprint",null)
    fun confirmCapability(revision:Int,fingerprint:String) {
        check(preferences.edit().putInt(KEY_CAPABILITY_REVISION,revision)
            .putString("capability-fingerprint",fingerprint).commit())
    }

    fun clearCredential() {
        preferences.edit()
            .remove(KEY_TERMINAL_ID)
            .remove(KEY_TARGET_DEVICE_ID)
            .remove(KEY_CREDENTIAL)
            .remove(KEY_ROW_VERSION)
            .remove(KEY_CAPABILITY_REVISION)
            .remove("capability-fingerprint")
            .apply()
    }

    private fun encrypt(value: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val encoded = cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(encoded, Base64.NO_WRAP)
    }

    private fun decrypt(encoded: String): String {
        val bytes = Base64.decode(encoded, Base64.NO_WRAP)
        require(bytes.size > IV_LENGTH) { "Stored terminal credential is invalid" }
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(
            Cipher.DECRYPT_MODE,
            secretKey(),
            GCMParameterSpec(128, bytes.copyOfRange(0, IV_LENGTH)),
        )
        return cipher.doFinal(bytes.copyOfRange(IV_LENGTH, bytes.size)).toString(Charsets.UTF_8)
    }

    private fun secretKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEY_STORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEY_STORE)
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build(),
        )
        return generator.generateKey()
    }

    companion object {
        private const val PREFERENCES = "terminal-identity"
        private const val KEY_INSTALLATION_ID = "installation-id"
        private const val KEY_BASE_URL = "base-url"
        private const val KEY_DISPLAY_NAME = "display-name"
        private const val KEY_TERMINAL_ID = "terminal-id"
        private const val KEY_TARGET_DEVICE_ID = "target-device-id"
        private const val KEY_CREDENTIAL = "credential"
        private const val KEY_ROW_VERSION = "terminal-row-version"
        private const val KEY_CAPABILITY_REVISION = "capability-revision"
        private const val ANDROID_KEY_STORE = "AndroidKeyStore"
        private const val KEY_ALIAS = "al1s-terminal-credential-v1"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val IV_LENGTH = 12
    }
}
