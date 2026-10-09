package com.al1s.terminal.media

import java.nio.ByteBuffer

object ScrcpyControlPacket {
    fun accepts(packet: ByteArray): Boolean {
        val buffer = ByteBuffer.wrap(packet)
        if (packet.size == 14 && packet[0].toInt() == 0) {
            buffer.get()
            val action = buffer.get().toInt()
            return action in 0..1 && buffer.int in setOf(3, 4, 24, 25, 26, 82, 187, 223, 224) &&
                buffer.int == 0 && buffer.int == 0
        }
        if (packet.size != 32 || packet[0].toInt() != 2) return false
        buffer.get()
        val action = buffer.get().toInt()
        val pointer = buffer.long
        val x = buffer.int; val y = buffer.int
        val width = buffer.short.toInt() and 65535; val height = buffer.short.toInt() and 65535
        val pressure = buffer.short.toInt() and 65535
        return action in 0..2 && pointer == 0L && width in 1..8192 && height in 1..8192 &&
            x in 0 until width && y in 0 until height && pressure in setOf(0, 65535) &&
            buffer.int == 0 && buffer.int == 0
    }
}
