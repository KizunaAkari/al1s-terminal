package com.al1s.terminal.media

import java.io.DataInputStream
import java.io.InputStream

data class ScrcpyVideoPacket(val configuration:Boolean,val keyframe:Boolean,val timestamp:Long,val data:ByteArray)
class ScrcpyVideoPackets(input:InputStream) {
    private val stream=DataInputStream(input)
    val width:Int
    val height:Int
    init {
        require(stream.readInt()==0x68323634) {"video_codec_unsupported"}
        width=stream.readInt();height=stream.readInt()
        require(width in 1..8192 && height in 1..8192)
    }
    fun next():ScrcpyVideoPacket {
        val flags=stream.readLong();val length=stream.readInt()
        require(length in 1..(4*1024*1024)) {"video_packet_too_large"}
        val data=ByteArray(length);stream.readFully(data)
        return ScrcpyVideoPacket(flags<0,flags and (1L shl 62)!=0L,flags and ((1L shl 62)-1),data)
    }
}
