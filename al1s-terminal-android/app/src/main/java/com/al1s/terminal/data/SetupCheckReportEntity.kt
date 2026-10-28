package com.al1s.terminal.data

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(tableName="setup_check_reports",indices=[Index(value=["terminalId","confirmed","createdAt"])])
data class SetupCheckReportEntity(@PrimaryKey val requestId:String,val terminalId:String,
    val instanceId:String,val payloadJson:String,val confirmed:Boolean=false,val createdAt:Long)
