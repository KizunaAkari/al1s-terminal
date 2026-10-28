package com.al1s.terminal.editor

import android.os.Bundle
import android.os.ParcelFileDescriptor
import android.os.SharedMemory
import com.al1s.terminal.broker.BrokerClient
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.io.DataInputStream
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** Each browser attachment starts a fresh stream with its codec header. */
@androidx.annotation.RequiresApi(27)
class AndroidEditorSession(
    private val origin: String, private val credential: String, val id: String,
    val instance: String, private val client: OkHttpClient,
    private val prepareFrame:()->Bundle={BrokerClient.invoke("editor_prepare")},
) : AutoCloseable {
    private val worker = Executors.newFixedThreadPool(3)
    private val sockets = mutableMapOf<String, WebSocket>()
    private val active = AtomicBoolean(true)
    @Volatile private var video: ParcelFileDescriptor? = null
    @Volatile private var mediaId: String? = null
    private var mediaSocket:WebSocket?=null

    @Synchronized fun connectMissing() {
        if (!active.get()) return
        for (channel in EditorEndpoint.channels) if (channel !in sockets) {
            val request = Request.Builder().url(EditorEndpoint.uplink(origin, id, instance, channel))
                .header("Authorization", "Bearer $credential").build()
            sockets[channel] = client.newWebSocket(request, listener(channel))
        }
        mediaId?.let { BrokerClient.invoke("media_renew", Bundle().apply { putString("session_id", it) }) }
    }

    private fun listener(channel: String) = object : WebSocketListener() {
        override fun onMessage(socket: WebSocket, bytes: ByteString) {
            if (!active.get()) return
            val data = bytes.toByteArray()
            if(channel=="ocr") {
                worker.execute {runCatching {ocr(data,socket)}.onFailure {socket.cancel()}}
                return
            }
            if(channel=="app_icon") {
                worker.execute {runCatching {icon(data,socket)}.onFailure {socket.cancel()}}
                return
            }
            if (data.contentEquals("open".toByteArray())) worker.execute {
                runCatching { openChannel(channel, socket) }.onFailure { socket.cancel() }
            } else if (channel == "control") worker.execute {
                runCatching {synchronized(this@AndroidEditorSession) {
                    check(active.get() && sockets[channel]===socket) {"editor_attachment_changed"}
                    BrokerClient.invoke("media_control", Bundle().apply {
                        putString("session_id", checkNotNull(mediaId)); putByteArray("packet", data)
                    })
                } }.onFailure { socket.cancel() }
            } else socket.cancel()
        }
        override fun onFailure(socket: WebSocket, error: Throwable, response: Response?) = lost(channel, socket)
        override fun onClosed(socket: WebSocket, code: Int, reason: String) = lost(channel, socket)
        override fun onClosing(socket: WebSocket, code: Int, reason: String) { socket.close(code, reason) }
    }

    @Synchronized private fun lost(channel: String, socket: WebSocket) {
        if (sockets[channel] === socket) {
            sockets.remove(channel)
            if (channel == "video") closeMedia(socket)
        }
    }

    private fun openChannel(channel: String, socket: WebSocket) {
        if(channel in setOf("video","screenshot","foreground")) {
            val preparation=prepareFrame()
            EditorPreparation.requireFrameReady(
                preparation.getBoolean("prepared"),preparation.getBoolean("automation"))
        }
        when (channel) {
            "video" -> stream(socket)
            "screenshot" -> send(socket, original())
            "foreground" -> {
                val result = BrokerClient.invoke("foreground")
                send(socket, JSONObject().put("package_name", result.getString("package_name"))
                    .put("label", result.getString("label")).toString().toByteArray())
            }
        }
    }
    private fun ocr(data:ByteArray,socket:WebSocket) {
        require(data.size in 33..(4*1024*1024))
        val memory=SharedMemory.create("al1s-ocr-crop",data.size)
        try {
            val buffer=memory.mapReadWrite()
            try {buffer.put(data)} finally {SharedMemory.unmap(buffer)}
            memory.setProtect(android.system.OsConstants.PROT_READ)
            val result=BrokerClient.invoke("editor_ocr",Bundle().apply {putParcelable("image",memory)})
            send(socket,checkNotNull(result.getString("result")).toByteArray())
        } finally {memory.close()}
    }
    @Suppress("DEPRECATION") private fun icon(data:ByteArray,socket:WebSocket) {
        require(data.size in 3..255)
        val packageName=data.toString(Charsets.US_ASCII)
        val result=BrokerClient.invoke("application_icon",Bundle().apply {putString("package_name",packageName)})
        val memory=checkNotNull(result.getParcelable<SharedMemory>("image"))
        try {
            val buffer=memory.mapReadOnly()
            try {require(buffer.remaining() in 24..262144);send(socket,ByteArray(buffer.remaining()).also {buffer.get(it)})}
            finally {SharedMemory.unmap(buffer)}
        } finally {memory.close()}
    }

    @Suppress("DEPRECATION") private fun original(): ByteArray {
        val result = BrokerClient.invoke("editor_screenshot")
        val memory = checkNotNull(result.getParcelable<SharedMemory>("image"))
        return try {
            val buffer = memory.mapReadOnly()
            try {
                require(buffer.remaining() in 24..(16 * 1024 * 1024))
                require(buffer.remaining() == result.getInt("length"))
                ByteArray(buffer.remaining()).also { buffer.get(it) }
            } finally { SharedMemory.unmap(buffer) }
        } finally { memory.close() }
    }

    @Suppress("DEPRECATION") private fun stream(socket: WebSocket) {
        val descriptor=synchronized(this) {
            check(active.get() && sockets["video"]===socket && mediaId==null) {"video_attachment_changed"}
            val session = BrokerClient.invoke("media_open")
            mediaId = checkNotNull(session.getString("session_id"))
            mediaSocket=socket
            checkNotNull(session.getParcelable<ParcelFileDescriptor>("video")).also {video=it}
        }
        try {
            DataInputStream(ParcelFileDescriptor.AutoCloseInputStream(descriptor)).use { input ->
                val metadata = ByteArray(12); input.readFully(metadata); send(socket, metadata)
                while (active.get()) {
                    val header = ByteArray(12); input.readFully(header)
                    val length = java.nio.ByteBuffer.wrap(header).getInt(8)
                    require(length in 1..(4 * 1024 * 1024))
                    val payload = ByteArray(length); input.readFully(payload)
                    send(socket, header); send(socket, payload)
                }
            }
        } finally { closeMedia(socket) }
    }

    private fun send(socket: WebSocket, bytes: ByteArray) {
        check(active.get() && socket.queueSize() + bytes.size <= 8 * 1024 * 1024)
        check(socket.send(bytes.toByteString())) { "editor_uplink_closed" }
    }

    @Synchronized private fun closeMedia(expected:WebSocket?=null) {
        if(expected!=null && mediaSocket!==expected)return
        runCatching { video?.close() }; video = null
        mediaId?.let { id -> runCatching {
            BrokerClient.invoke("media_close", Bundle().apply { putString("session_id", id) })
        } }; mediaId = null;mediaSocket=null
    }

    @Synchronized override fun close() {
        active.set(false); sockets.values.forEach { it.cancel() }; sockets.clear()
        closeMedia(); worker.shutdownNow()
    }
}
