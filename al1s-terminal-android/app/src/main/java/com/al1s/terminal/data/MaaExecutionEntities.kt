package com.al1s.terminal.data

import androidx.room.*

@Entity(tableName="package_resources", primaryKeys=["packageId", "resourceKey"],
    foreignKeys=[ForeignKey(entity=InboxTaskEntity::class,parentColumns=["packageId"],childColumns=["packageId"],onDelete=ForeignKey.RESTRICT)],
    indices=[Index(value=["sha256"])])
data class PackageResourceEntity(val packageId: String, val resourceKey: String, val blobId: String, val sha256: String,
    val size: Long, val mediaType: String, val role: String)

@Entity(tableName="maa_attempt_links", indices=[Index(value=["packageId"], unique=true)],
    foreignKeys=[ForeignKey(entity=InboxTaskEntity::class,parentColumns=["packageId"],childColumns=["packageId"],onDelete=ForeignKey.RESTRICT)])
data class MaaAttemptLinkEntity(@PrimaryKey val attemptId: String, val packageId: String, val helperEpoch: String?,
    val helperStatus: String, val lastEventSequence: Long, val updatedAt: Long)
