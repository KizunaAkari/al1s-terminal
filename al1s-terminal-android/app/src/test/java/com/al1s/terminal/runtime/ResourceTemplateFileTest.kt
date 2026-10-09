package com.al1s.terminal.runtime

import com.al1s.terminal.maa.ResourceTemplateFile
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.nio.file.Files

class ResourceTemplateFileTest {
    @Test fun interruptedDerivedCopyIsRepairedAndTemplateBytesArePreserved() {
        val root=Files.createTempDirectory("resource-template-test").toFile()
        try {
            val bytes=java.util.Base64.getDecoder().decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1V0AAAAASUVORK5CYII=")
            val source=File(root,"source").apply {writeBytes(bytes)}
            val output=File(root,"images")
            val name=ResourceTemplateFile.materialize(source,output)
            File(output,name).writeText("interrupted")
            assertEquals(name,ResourceTemplateFile.materialize(source,output))
            assertArrayEquals(bytes,File(output,name).readBytes())
            assertEquals(listOf(name),output.listFiles()?.map {it.name})
        } finally {root.deleteRecursively()}
    }
}
