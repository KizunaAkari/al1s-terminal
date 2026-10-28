package com.al1s.terminal.execution

import java.io.File
import java.io.RandomAccessFile
import java.nio.channels.FileLock

/** Helper-owned files and a process lock; acquiring it proves previous native execution ended. */
class HelperWorkspace(appUid: Int, providerUid: Int) : AutoCloseable {
    val directory = File("/data/local/tmp/al1s-helper-$appUid-$providerUid")
    private val lockFile: RandomAccessFile
    private val lock: FileLock
    var previousEpoch:String?=null
        private set
    init {
        require(appUid >= 10000 && providerUid in setOf(0, 2000))
        check(directory.isDirectory || directory.mkdirs())
        require(!java.nio.file.Files.isSymbolicLink(directory.toPath()))
        lockFile = RandomAccessFile(File(directory, "owner.lock"), "rw")
        lock = checkNotNull(lockFile.channel.tryLock()) { "existing_helper_not_reconciled" }
    }
    fun beginEpoch(epoch:String) {
        require(java.util.UUID.fromString(epoch).toString()==epoch)
        val owner=File(directory,"owner.epoch")
        require(!java.nio.file.Files.isSymbolicLink(owner.toPath()))
        previousEpoch=if(owner.isFile && owner.length()==36L) runCatching {
            java.util.UUID.fromString(owner.readText(Charsets.UTF_8)).toString()
        }.getOrNull() else null
    }
    fun confirmEpoch(epoch:String) {
        require(java.util.UUID.fromString(epoch).toString()==epoch)
        val owner=File(directory,"owner.epoch")
        require(!java.nio.file.Files.isSymbolicLink(owner.toPath()))
        val temporary=java.nio.file.Files.createTempFile(directory.toPath(),"epoch-",".tmp").toFile()
        try {
            java.io.FileOutputStream(temporary).use {it.write(epoch.toByteArray(Charsets.UTF_8));it.fd.sync()}
            java.nio.file.Files.move(temporary.toPath(),owner.toPath(),java.nio.file.StandardCopyOption.ATOMIC_MOVE,
                java.nio.file.StandardCopyOption.REPLACE_EXISTING)
        } finally {temporary.delete()}
    }
    override fun close() { lock.release(); lockFile.close() }
}
