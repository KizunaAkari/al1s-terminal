package com.al1s.terminal.maa

import java.io.File
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest

object ResourceTemplateFile {
    fun materialize(source:File,root:File):String {
        require(source.isFile && !Files.isSymbolicLink(source.toPath()) && source.length() in 24..(32L*1024*1024))
        source.inputStream().use {input->
            val prefix=ByteArray(24);check(input.read(prefix)==24)
            require(prefix.take(8)==listOf(137,80,78,71,13,10,26,10).map {it.toByte()} ||
                prefix[0]==0xff.toByte() && prefix[1]==0xd8.toByte()) {"template_image_invalid"}
        }
        check(root.isDirectory || root.mkdirs())
        val hash=digest(source);val name="$hash.png";val destination=File(root,name)
        if(destination.isFile && destination.length()==source.length() && digest(destination)==hash)return name
        val temporary=Files.createTempFile(root.toPath(),"template-",".part").toFile()
        try {
            source.inputStream().use {input->FileOutputStream(temporary).use {output->input.copyTo(output);output.fd.sync()}}
            check(digest(temporary)==hash && temporary.length()==source.length())
            Files.move(temporary.toPath(),destination.toPath(),StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING)
            return name
        } finally {temporary.delete()}
    }
    private fun digest(file:File):String {
        val digest=MessageDigest.getInstance("SHA-256")
        file.inputStream().use {input->val buffer=ByteArray(65536);while(true) {val n=input.read(buffer);if(n<0)break;digest.update(buffer,0,n)}}
        return digest.digest().joinToString("") {"%02x".format(it)}
    }
}
