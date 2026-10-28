package com.al1s.terminal.data

import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(tableName="quick_test_inbox",indices=[Index(value=["terminalId","state","createdAt"])])
data class QuickTestInboxEntity(@PrimaryKey val sessionId:String,val scriptId:String,val candidateVersionId:String,
    val candidateManifestHash:String,val definitionHash:String,val terminalId:String,val deviceId:String,
    val packageHash:String,val state:String,val expiresAt:Long,val reportId:String,val entryDefinitionKey:String,
    val debugStepNumber:Int?,val lastHelperSequence:Long=0,val lastEventSequence:Int=0,
    val resultJson:String?=null,val createdAt:Long,val updatedAt:Long)

@Entity(tableName="quick_test_resources",primaryKeys=["sessionId","resourceKey"],
    foreignKeys=[ForeignKey(entity=QuickTestInboxEntity::class,parentColumns=["sessionId"],childColumns=["sessionId"],onDelete=ForeignKey.RESTRICT)])
data class QuickTestResourceEntity(val sessionId:String,val resourceKey:String,val blobId:String,
    val sha256:String,val size:Long,val mediaType:String,val role:String)

@Entity(tableName="quick_test_events",primaryKeys=["sessionId","sequence"],
    foreignKeys=[ForeignKey(entity=QuickTestInboxEntity::class,parentColumns=["sessionId"],childColumns=["sessionId"],onDelete=ForeignKey.RESTRICT)],
    indices=[Index(value=["sessionId","confirmed","sequence"])])
data class QuickTestEventEntity(val sessionId:String,val sequence:Int,val kind:String,val stepNumber:Int?,
    val code:String?,val createdAt:String,val confirmed:Boolean=false)
