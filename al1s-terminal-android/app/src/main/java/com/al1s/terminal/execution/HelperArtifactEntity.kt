package com.al1s.terminal.execution

import androidx.room.*

@Entity(tableName="helper_artifacts",indices=[Index(value=["attemptId","confirmed"])],
    foreignKeys=[ForeignKey(entity=HelperAttemptEntity::class,parentColumns=["attemptId"],childColumns=["attemptId"],onDelete=ForeignKey.RESTRICT)])
data class HelperArtifactEntity(@PrimaryKey val artifactId:String,val attemptId:String,val kind:String,val path:String,
    val sha256:String,val size:Long,val mediaType:String,val confirmed:Boolean,val createdAt:Long,val remoteArtifactId:String?=null)
