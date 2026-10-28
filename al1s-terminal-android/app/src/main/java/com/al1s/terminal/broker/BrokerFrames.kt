package com.al1s.terminal.broker

import android.os.Bundle
import android.os.SharedMemory
import android.system.OsConstants

@androidx.annotation.RequiresApi(27)
object BrokerFrames {
    fun readOnly(bytes: ByteArray): Bundle {
        require(bytes.size in 1..(32 * 1024 * 1024))
        val memory = SharedMemory.create("al1s-frame", bytes.size)
        try {
            val mapping = memory.mapReadWrite()
            try { mapping.put(bytes) } finally { SharedMemory.unmap(mapping) }
            check(memory.setProtect(OsConstants.PROT_READ))
            return Bundle().apply { putParcelable("image", memory); putInt("length", bytes.size) }
        } catch (error: Exception) { memory.close(); throw error }
    }
}
