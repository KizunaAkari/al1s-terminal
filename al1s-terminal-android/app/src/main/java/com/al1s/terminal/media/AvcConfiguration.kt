package com.al1s.terminal.media

/** scrcpy's Annex B configuration contains both SPS and PPS; Android expects separate buffers. */
object AvcConfiguration {
    fun split(data:ByteArray):Pair<ByteArray,ByteArray> {
        require(data.size in 8..(1024*1024)) {"h264_configuration_invalid"}
        val starts=mutableListOf<Pair<Int,Int>>()
        var i=0
        while(i+2<data.size) {
            val length=when {
                i+3<data.size && data[i]==0.toByte() && data[i+1]==0.toByte() && data[i+2]==0.toByte() && data[i+3]==1.toByte()->4
                data[i]==0.toByte() && data[i+1]==0.toByte() && data[i+2]==1.toByte()->3
                else->0
            }
            if(length>0) {starts+=i to length;i+=length} else i++
        }
        val units=starts.mapIndexed {index,(start,length)->
            val end=starts.getOrNull(index+1)?.first ?: data.size
            require(start+length<end)
            val body=data.copyOfRange(start+length,end)
            (body[0].toInt() and 31) to (byteArrayOf(0,0,0,1)+body)
        }.toMap()
        return requireNotNull(units[7]) {"h264_sps_missing"} to requireNotNull(units[8]) {"h264_pps_missing"}
    }
}
