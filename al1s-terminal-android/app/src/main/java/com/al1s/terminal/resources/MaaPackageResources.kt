package com.al1s.terminal.resources

import com.al1s.terminal.protocol.PlatformBlobClient
import com.al1s.terminal.runtime.PackageResourceSpec
import java.io.File

class MaaPackageResources(val store: VerifiedResourceStore, private val client: PlatformBlobClient) {
    fun receive(credential: String, resources: List<PackageResourceSpec>): Map<String, File> {
        val paths = mutableMapOf<String, File>()
        resources.distinctBy { it.sha256 }.forEach { resource ->
            val file = store.ready(resource.sha256, resource.size) ?: run {
                val remote = client.head(credential, resource.blobId)
                check(remote.sha256 == resource.sha256 && remote.size == resource.size &&
                    remote.mediaType == resource.mediaType.substringBefore(';').lowercase()) { "resource_manifest_mismatch" }
                store.receive(resource.sha256, resource.size) { start, count ->
                    client.readRange(credential, resource.blobId, start, count, resource.size)
                }
            }
            paths[resource.sha256] = file
        }
        return resources.associate { it.key to checkNotNull(paths[it.sha256]) }
    }
}
