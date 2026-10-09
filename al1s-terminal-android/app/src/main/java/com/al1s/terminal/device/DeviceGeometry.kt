package com.al1s.terminal.device

data class DeviceGeometry(val width: Int, val height: Int, val rotation: Int, val generation: Long) {
    init { require(width in 1..8192 && height in 1..8192 && rotation in 0..3 && generation > 0) }
    fun accepts(epoch: Long, sourceWidth: Int, sourceHeight: Int, x: Int, y: Int): Boolean =
        epoch == generation && sourceWidth == width && sourceHeight == height && x in 0 until width && y in 0 until height
}
