package com.al1s.terminal.media

import android.net.LocalSocket
import android.net.LocalSocketAddress
import android.os.ParcelFileDescriptor
import java.io.File
import java.nio.file.Files
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.concurrent.TimeUnit
import java.util.zip.ZipFile

/** Real pinned scrcpy server, owned by the helper. No inbound TCP or raw ADB exposure. */
class ScrcpySession(private val apk: String) : AutoCloseable {
    private val directory = Files.createTempDirectory(File("/data/local/tmp").toPath(), "al1s-media-").toFile()
    private var process: java.lang.Process? = null
    private var video: LocalSocket? = null
    private var control: LocalSocket? = null
    @Volatile private var diagnostic = ""
    val id: String = java.util.UUID.randomUUID().toString()
    private var validUntil = android.os.SystemClock.elapsedRealtime() + TimeUnit.SECONDS.toMillis(30)

    fun start(): ParcelFileDescriptor {
        try {
            val server = File(directory, "scrcpy-server.jar")
            ZipFile(apk).use { archive ->
                val entry = checkNotNull(archive.getEntry("assets/scrcpy-server-v3.3.4"))
                require(entry.size in 1..(4 * 1024 * 1024))
                archive.getInputStream(entry).use { input -> server.outputStream().use { input.copyTo(it) } }
            }
            val hash = MessageDigest.getInstance("SHA-256").digest(server.readBytes()).joinToString("") { "%02x".format(it) }
            check(hash == SHA256) { "scrcpy_asset_hash_mismatch" }
            val scid = "%08x".format(SecureRandom().nextInt(Int.MAX_VALUE))
            val builder = ProcessBuilder("/system/bin/app_process", "/", "com.genymobile.scrcpy.Server", "3.3.4",
                "scid=$scid", "tunnel_forward=true", "audio=false", "control=true", "video_codec=h264",
                "send_device_meta=false", "send_dummy_byte=true", "clipboard_autosync=false", "max_size=1600",
                "max_fps=24", "video_bit_rate=3000000", "video_codec_options=i-frame-interval=1,frame-rate=24",
                "power_on=false", "cleanup=false")
            builder.environment()["CLASSPATH"] = server.absolutePath
            process = builder.redirectErrorStream(true).start()
            val diagnosticStream = checkNotNull(process).inputStream
            Thread({ runCatching { diagnosticStream.bufferedReader().use { reader ->
                while (true) {
                    val line = reader.readLine() ?: break
                    diagnostic = (diagnostic + "\n" + line.take(512)).takeLast(2048)
                }
            } } }, "al1s-scrcpy-diagnostic").start()
            video = connect("scrcpy_$scid").also { check(it.inputStream.read() == 0) { "scrcpy_handshake_invalid" } }
            video?.soTimeout = 0 // Established video can legitimately be idle; lifecycle owner closes it.
            control = connect("scrcpy_$scid")
            return ParcelFileDescriptor.dup(checkNotNull(video).fileDescriptor)
        } catch (error: Exception) {
            println("AL1S_SCRCPY_FAILED:${error.javaClass.simpleName}:${error.message?.take(100)}")
            println(diagnostic)
            close(); throw error
        }
    }

    @Synchronized fun renew(requestedId: String) {
        require(id == requestedId && alive()) { "media_session_expired" }
        validUntil = android.os.SystemClock.elapsedRealtime() + TimeUnit.SECONDS.toMillis(30)
    }

    @Synchronized fun alive(): Boolean = process?.isAlive == true && android.os.SystemClock.elapsedRealtime() < validUntil

    @Synchronized fun input(requestedId: String, packet: ByteArray) {
        check(requestedId == id && alive()) { "media_session_expired" }
        require(ScrcpyControlPacket.accepts(packet)) { "invalid_control_packet" }
        checkNotNull(control).outputStream.apply { write(packet); flush() }
    }

    private fun connect(name: String): LocalSocket {
        val deadline = android.os.SystemClock.elapsedRealtime() + TimeUnit.SECONDS.toMillis(8)
        while (android.os.SystemClock.elapsedRealtime() < deadline && process?.isAlive == true) {
            val socket = LocalSocket()
            try {
                socket.connect(LocalSocketAddress(name, LocalSocketAddress.Namespace.ABSTRACT))
                socket.soTimeout = 3000
                return socket
            } catch (_: java.io.IOException) { socket.close(); Thread.sleep(50) }
        }
        error("scrcpy_connect_timeout")
    }

    @Synchronized override fun close() {
        runCatching { video?.close() }; video = null
        runCatching { control?.close() }; control = null
        process?.let {
            it.destroy()
            if (!it.waitFor(2, TimeUnit.SECONDS)) {
                it.destroyForcibly()
                check(it.waitFor(2,TimeUnit.SECONDS)) {"media_stop_unconfirmed"}
            }
        }
        process = null
        File(directory, "scrcpy-server.jar").delete()
        directory.delete()
    }

    companion object { const val SHA256 = "8588238c9a5a00aa542906b6ec7e6d5541d9ffb9b5d0f6e1bc0e365e2303079e" }
}
