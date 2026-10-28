package com.al1s.terminal.media

import android.media.MediaCodec
import android.media.MediaFormat
import android.media.MediaMuxer
import android.os.ParcelFileDescriptor
import org.json.JSONObject
import java.io.File
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** Muxes real scrcpy H.264 without decoding; only owns its server and controlled output file. */
class NativeRecordingSession(apk:String,private val output:File) {
    private val session=ScrcpySession(apk)
    private val stopping=AtomicBoolean(false)
    private val finished=CountDownLatch(1)
    private val started=CountDownLatch(1)
    @Volatile private var error:String?=null
    private var bytes=0L
    private var pictures=0
    @Volatile private var stopNanos:Long?=null
    private val keeper=java.util.concurrent.Executors.newSingleThreadScheduledExecutor()

    fun start() {
        check(output.parentFile?.isDirectory==true || output.parentFile?.mkdirs()==true)
        val descriptor=session.start()
        keeper.scheduleWithFixedDelay({if(!stopping.get())runCatching {session.renew(session.id)}},10,10,TimeUnit.SECONDS)
        Thread({
            try { ParcelFileDescriptor.AutoCloseInputStream(descriptor).use { consume(ScrcpyVideoPackets(it)) } }
            catch(failure:Exception) {if(!stopping.get())error=failure.javaClass.simpleName}
            finally {keeper.shutdownNow();started.countDown();finished.countDown();runCatching {session.close()}}
        },"al1s-recording").start()
        if(!started.await(8,TimeUnit.SECONDS) || error!=null) {
            stopping.set(true);runCatching {session.close()}
            error("recording_start_failed")
        }
    }

    private fun consume(stream:ScrcpyVideoPackets) {
        val muxer=MediaMuxer(output.absolutePath,MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        var track=-1;var live=false;var first:Long?=null;var firstNanos:Long?=null
        var lastPresentation=0L;var renewed=System.nanoTime()
        try {
            while(!stopping.get()) {
                val packet=stream.next()
                if(packet.configuration && !live) {
                    val format=MediaFormat.createVideoFormat("video/avc",stream.width,stream.height)
                    val (sps,pps)=AvcConfiguration.split(packet.data)
                    format.setByteBuffer("csd-0",ByteBuffer.wrap(sps));format.setByteBuffer("csd-1",ByteBuffer.wrap(pps))
                    track=muxer.addTrack(format);muxer.start();live=true;started.countDown();continue
                }
                if(packet.configuration || !live)continue
                if(bytes+packet.data.size>256L*1024*1024) {error="recording_size_limit";break}
                if(first==null) {first=packet.timestamp;firstNanos=System.nanoTime()}
                lastPresentation=packet.timestamp-checkNotNull(first)
                val info=MediaCodec.BufferInfo().apply {set(0,packet.data.size,packet.timestamp-checkNotNull(first),
                    if(packet.keyframe)MediaCodec.BUFFER_FLAG_KEY_FRAME else 0)}
                muxer.writeSampleData(track,ByteBuffer.wrap(packet.data),info)
                bytes+=packet.data.size;pictures++
                if(System.nanoTime()-renewed>TimeUnit.SECONDS.toNanos(10)) {session.renew(session.id);renewed=System.nanoTime()}
            }
        } finally {
            if(live)runCatching {
                if(pictures>0) {
                    val end=RecordingTimeline.endUs(checkNotNull(firstNanos),stopNanos ?: System.nanoTime(),lastPresentation)
                    muxer.writeSampleData(track,ByteBuffer.allocate(0),MediaCodec.BufferInfo().apply {
                        set(0,0,end,MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                    })
                }
                muxer.stop()
            }.onFailure {error="recording_finalize_failed";android.util.Log.e("AL1S-Recording","MP4 finalization failed",it)}
            muxer.release()
        }
    }

    fun finish():JSONObject {
        stopNanos=System.nanoTime();stopping.set(true);runCatching {session.close()}
        keeper.shutdownNow()
        check(finished.await(5,TimeUnit.SECONDS)) {"recording_finalize_pending"}
        val result=JSONObject().put("path",output.absolutePath).put("mime","video/mp4").put("pictures",pictures)
            .put("error",error ?: JSONObject.NULL)
        if(output.isFile && output.length()>0 && pictures>0) {
            val digest=MessageDigest.getInstance("SHA-256")
            output.inputStream().use {input->val buffer=ByteArray(65536);while(true){val size=input.read(buffer);if(size<0)break;digest.update(buffer,0,size)}}
            result.put("sha256",digest.digest().joinToString(""){"%02x".format(it)}).put("size_bytes",output.length())
        }
        return result
    }
}
