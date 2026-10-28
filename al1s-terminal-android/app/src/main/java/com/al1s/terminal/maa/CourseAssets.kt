package com.al1s.terminal.maa

import java.io.File
import java.util.zip.ZipFile

object CourseAssets {
    fun install(apk:String, imageFolder:File) {
        check(imageFolder.isDirectory || imageFolder.mkdirs())
        ZipFile(apk).use { zip -> AndroidCoursePass.assets.values.forEach { name ->
            val entry=checkNotNull(zip.getEntry("assets/maa-course/$name")) {"course_resource_missing"}
            require(entry.size in 24..262144)
            val destination=File(imageFolder,"course_$name")
            zip.getInputStream(entry).use { input -> java.io.FileOutputStream(destination).use { output ->
                input.copyTo(output); output.fd.sync()
            } }
            require(destination.length()==entry.size)
        } }
    }
}
