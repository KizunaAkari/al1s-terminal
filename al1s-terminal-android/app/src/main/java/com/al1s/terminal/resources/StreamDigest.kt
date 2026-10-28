package com.al1s.terminal.resources

import java.io.InputStream
import java.io.OutputStream
import java.security.DigestInputStream
import java.security.MessageDigest

private const val COPY_BUFFER_SIZE = 8192

/** Copies bytes while calculating SHA-256 without taking ownership of either stream. */
fun copyWithSha256(input: InputStream, output: OutputStream): String {
    val digest = MessageDigest.getInstance("SHA-256")
    DigestInputStream(input, digest).copyTo(output, COPY_BUFFER_SIZE)
    return digest.digest().joinToString("") { "%02x".format(it.toInt() and 0xff) }
}
