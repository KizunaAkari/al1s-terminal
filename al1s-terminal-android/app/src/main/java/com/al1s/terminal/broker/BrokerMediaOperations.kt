package com.al1s.terminal.broker

import android.content.Context
import android.os.Bundle
import com.al1s.terminal.media.ScrcpySession
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class BrokerMediaOperations(private val context: Context) : AutoCloseable {
    private var media: ScrcpySession? = null
    private var inputRevoked = false
    private val cleanup = Executors.newSingleThreadScheduledExecutor().apply {
        scheduleWithFixedDelay({ synchronized(this@BrokerMediaOperations) {
            if (media?.alive() == false) { media?.close(); media = null }
        } }, 1, 1, TimeUnit.SECONDS)
    }
    @Synchronized fun canReplace() = media?.alive() != true
    @Synchronized fun revokeInput() { inputRevoked = true }
    @Synchronized fun closeSession() {inputRevoked=true;media?.close();media=null}
    @Synchronized fun invoke(operation: String, payload: Bundle): Bundle = when (operation) {
        "media_open" -> {
            check(media == null) { "media_session_already_active" }
            val session = ScrcpySession(context.packageManager.getApplicationInfo("com.al1s.terminal", 0).sourceDir)
            val video = session.start()
            media = session
            inputRevoked = false
            Bundle().apply { putString("session_id", session.id); putParcelable("video", video) }
        }
        "media_renew" -> { checkNotNull(media).renew(payload.getString("session_id").orEmpty()); Bundle() }
        "media_control" -> {
            check(!inputRevoked) { "editor_input_revoked_for_automation" }
            checkNotNull(media).input(payload.getString("session_id").orEmpty(), requireNotNull(payload.getByteArray("packet")))
            Bundle()
        }
        "media_close" -> {
            check(media?.id == payload.getString("session_id")) { "media_session_mismatch" }
            media?.close(); media = null
            Bundle()
        }
        else -> error("unsupported_media_operation")
    }
    @Synchronized override fun close() { cleanup.shutdownNow(); media?.close(); media = null }
}
